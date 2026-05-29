#!/usr/bin/env python3
"""Integrate a Statcast-style projected-lineup preview into docs/index.html.

Adds a click-to-expand section under The Slate (inside the existing per-game
expander, where the diag row `r` with the baked top-5 batter JSON is in scope).
Renders, per team: pitching matchup + a hitter table (Order, Name, Bat,
OPS vs LHP / vs RHP / vs today's SP) heatmapped on OPS, in the dashboard's
monospace "Quant Terminal" theme. Shows "Awaiting Starting Lineup" until the
lineup card posts (JSON empty). Uses ONLY baked data already in the diag CSV.

Three idempotent insertions (skipped if the sentinel is already present):
  1. CSS block   -> before </style>
  2. JS funcs    -> before `function formatNarrative(`
  3. call line   -> before the `// ----- 8b. Game preview placeholder` comment
AST/structure is validated by the caller (PUSH bat: node --check the script).
Backs up to docs/index.html.bak.
"""
import sys
from pathlib import Path

P = Path("docs/index.html")

CSS_BLOCK = r"""
  /* GP-PREVIEW-CSS  — Statcast projected-lineup preview (click-to-expand under The Slate) */
  .gp-preview{margin:0.7rem 0 0.3rem;border:1px solid var(--border);border-radius:6px;background:var(--bg-elev);overflow:hidden;}
  .gp-head{font-size:0.78rem;letter-spacing:0.07em;font-weight:700;color:var(--accent);padding:0.5rem 0.7rem;border-bottom:1px solid var(--border);text-transform:uppercase;}
  .gp-head .gp-sub{color:var(--muted);font-weight:400;letter-spacing:0.02em;text-transform:none;margin-left:0.45rem;}
  .gp-matchup{display:flex;flex-wrap:wrap;gap:0.35rem 1.5rem;padding:0.45rem 0.7rem;border-bottom:1px solid var(--border);font-size:0.82rem;}
  .gp-sp .gp-spk{display:inline-block;font-size:0.6rem;background:rgba(88,166,255,0.16);color:var(--accent);border-radius:3px;padding:0.05rem 0.32rem;margin-right:0.35rem;letter-spacing:0.05em;vertical-align:middle;}
  .gp-grid{display:grid;grid-template-columns:1fr 1fr;gap:0;}
  .gp-team{border-right:1px solid var(--border);min-width:0;}
  .gp-team:last-child{border-right:none;}
  .gp-th{font-size:0.74rem;font-weight:700;padding:0.4rem 0.6rem;color:var(--text);background:rgba(255,255,255,0.02);border-bottom:1px solid var(--border);}
  .gp-th .gp-vs{color:var(--muted);font-weight:400;}
  .gp-await{color:var(--muted);font-size:0.8rem;padding:0.75rem 0.6rem;margin:0;}
  .gp-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
  .gp-tbl{border-collapse:separate;border-spacing:0;width:100%;font-size:0.76rem;font-variant-numeric:tabular-nums;}
  .gp-tbl th{position:sticky;top:0;background:var(--bg-elev);color:var(--muted);font-weight:600;text-align:right;padding:0.26rem 0.5rem;border-bottom:1px solid var(--border);white-space:nowrap;font-size:0.66rem;text-transform:uppercase;letter-spacing:0.03em;z-index:1;}
  .gp-tbl td{text-align:right;padding:0.22rem 0.5rem;border-bottom:1px solid rgba(255,255,255,0.04);white-space:nowrap;}
  .gp-tbl th.gp-name,.gp-tbl td.gp-name{position:sticky;left:0;text-align:left;background:var(--bg-elev);border-right:1px solid var(--border);min-width:124px;z-index:2;}
  .gp-tbl thead th.gp-name{z-index:3;}
  .gp-tbl th.gp-ord,.gp-tbl td.gp-ord{text-align:center;color:var(--muted);width:1.5rem;}
  .gp-name small{color:var(--muted);margin-left:0.3rem;font-size:0.66rem;}
  .gp-name .gp-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--muted);margin-right:0.42rem;vertical-align:middle;}
  .gp-na{color:var(--muted);}
  .gp-ops{font-weight:600;}
  .gp-pa{color:var(--muted);}
  /* OPS heatmap on the dark theme: blue (poor/cold) -> neutral -> red (good/hot) */
  .gp-c5{background:rgba(56,110,230,0.55);} .gp-c4{background:rgba(56,110,230,0.42);}
  .gp-c3{background:rgba(56,110,230,0.30);} .gp-c2{background:rgba(56,110,230,0.20);}
  .gp-c1{background:rgba(56,110,230,0.11);} .gp-z{background:transparent;}
  .gp-h1{background:rgba(248,81,73,0.12);}  .gp-h2{background:rgba(248,81,73,0.22);}
  .gp-h3{background:rgba(248,81,73,0.33);}  .gp-h4{background:rgba(248,81,73,0.46);}
  .gp-h5{background:rgba(248,81,73,0.58);}
  .gp-legend{font-size:0.69rem;color:var(--muted);padding:0.4rem 0.7rem;border-top:1px solid var(--border);display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;}
  .gp-leg-bar{display:inline-block;width:120px;height:8px;border-radius:2px;border:1px solid var(--border);background:linear-gradient(90deg,rgba(56,110,230,0.55),rgba(56,110,230,0.11),transparent,rgba(248,81,73,0.12),rgba(248,81,73,0.58));}
  @media (max-width:640px){.gp-grid{grid-template-columns:1fr;} .gp-team{border-right:none;border-bottom:1px solid var(--border);}}
"""

JS_FN = r"""
/* GP-PREVIEW-FN */
// Statcast-style projected-lineup preview (Quant Terminal themed). Renders
// per-team hitter tables from the baked top-5 batter JSON (platoon OPS vs
// LHP/RHP + OPS vs today's SP), heatmapped on OPS. "Awaiting Starting Lineup"
// until the lineup card posts. Uses only baked diag data — no fetch.
function _gpParseBatters(s){ try { return JSON.parse(s || "[]") || []; } catch (_) { return []; } }
function _gpHeatClass(v){
  if (v == null || isNaN(v)) return "";
  var lo = 0.600, mid = 0.720, hi = 0.900;            // league-avg OPS pivot
  var t = v <= mid ? (v - mid) / (mid - lo) : (v - mid) / (hi - mid);
  t = Math.max(-1, Math.min(1, t));
  var lvl = Math.round(t * 5);
  return lvl < 0 ? "gp-c" + (-lvl) : lvl > 0 ? "gp-h" + lvl : "gp-z";
}
function _gpOps(v){
  if (v == null || isNaN(v)) return '<td class="gp-na">-</td>';
  var txt = Number(v).toFixed(3).replace(/^0/, "");
  return '<td class="gp-ops ' + _gpHeatClass(v) + '">' + txt + '</td>';
}
function _gpTeamTable(bats, facingSP, label){
  if (!bats || !bats.length){
    return '<div class="gp-team"><div class="gp-th">' + label +
      ' <span class="gp-vs">vs ' + facingSP + '</span></div>' +
      '<p class="gp-await">⌛ Awaiting Starting Lineup &mdash; posts ~3h before first pitch.</p></div>';
  }
  var rows = "";
  for (var i = 0; i < bats.length; i++){
    var b = bats[i];
    var pa = (b.vs_today_SP_PA == null) ? 0 : b.vs_today_SP_PA;
    var vsSP = (pa > 0) ? b.vs_today_SP_OPS : null;
    rows += '<tr>' +
      '<td class="gp-ord">' + (b.order != null ? b.order : "") + '</td>' +
      '<td class="gp-name"><span class="gp-dot"></span>' + (b.name || "") +
        '<small>' + (b.bat_side || "") + '</small></td>' +
      _gpOps(b.vs_LHP_OPS_career) +
      _gpOps(b.vs_RHP_OPS_career) +
      _gpOps(vsSP) +
      '<td class="gp-pa">' + pa + '</td>' +
      '</tr>';
  }
  return '<div class="gp-team"><div class="gp-th">' + label +
    ' <span class="gp-vs">vs ' + facingSP + '</span></div>' +
    '<div class="gp-scroll"><table class="gp-tbl"><thead><tr>' +
    '<th class="gp-ord">#</th><th class="gp-name">Batter</th>' +
    '<th>vL OPS</th><th>vR OPS</th><th>vs SP</th><th>PA</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div></div>';
}
// Builds the projected-lineup preview HTML from a diag row. Away hitters face
// the HOME starter and vice-versa.
function _renderProjectedLineupTable(r){
  if (!r) return "";
  var matchup = (r.matchup || "").trim();
  var parts = matchup.split(" @ ");
  var awayAbbr = (parts[0] || "AWAY").trim();
  var homeAbbr = (parts[1] || "HOME").replace(/\s*\(.*$/, "").trim();
  var awaySP = (r.away_sp_name || "TBD").trim();
  var homeSP = (r.home_sp_name || "TBD").trim();
  var awayK = parseFloat(r.away_sp_k_pct), homeK = parseFloat(r.home_sp_k_pct);
  var away = _gpParseBatters(r.away_top_5_batters_json);
  var home = _gpParseBatters(r.home_top_5_batters_json);
  var kbit = function(k){ return isFinite(k) ? ' <span class="muted">&middot; ' + k.toFixed(1) + '% K</span>' : ""; };
  return '<div class="gp-preview">' +
    '<div class="gp-head">Statcast Game Preview' +
      '<span class="gp-sub">projected lineups &middot; OPS heatmap</span></div>' +
    '<div class="gp-matchup">' +
      '<div class="gp-sp"><span class="gp-spk">SP</span>' + awayAbbr + ': <strong>' + awaySP + '</strong>' + kbit(awayK) + '</div>' +
      '<div class="gp-sp"><span class="gp-spk">SP</span>' + homeAbbr + ': <strong>' + homeSP + '</strong>' + kbit(homeK) + '</div>' +
    '</div>' +
    '<div class="gp-grid">' +
      _gpTeamTable(away, homeSP, awayAbbr + " Hitters") +
      _gpTeamTable(home, awaySP, homeAbbr + " Hitters") +
    '</div>' +
    '<div class="gp-legend"><span class="gp-leg-bar"></span>' +
      ' OPS: poor -&gt; average -&gt; excellent &middot; "vs SP" = career vs today\'s starter (PA shown)</div>' +
  '</div>';
}
"""

CALL_BLOCK = """  /* GP-PREVIEW-CALL */
  // ----- 8a2. Statcast projected-lineup table (baked OPS data) -----
  html += _renderProjectedLineupTable(r);
"""


def insert_before(src, anchor, block, sentinel):
    if sentinel in src:
        print("  - already present (%s); skipping" % sentinel)
        return src, False
    n = src.count(anchor)
    if n != 1:
        raise SystemExit("ANCHOR ERROR: %r found %d times (expected 1)" % (anchor, n))
    return src.replace(anchor, block + anchor, 1), True


def main():
    src = P.read_text(encoding="utf-8")
    original = src
    changed = False

    src, c1 = insert_before(src, "</style>", CSS_BLOCK + "\n", "/* GP-PREVIEW-CSS")
    src, c2 = insert_before(src, "function formatNarrative(r, gradeInfo, result) {",
                            JS_FN + "\n", "/* GP-PREVIEW-FN")
    src, c3 = insert_before(src, "  // ----- 8b. Game preview placeholder (lazy-loaded on row expand) -----",
                            CALL_BLOCK, "/* GP-PREVIEW-CALL")
    changed = c1 or c2 or c3

    if not changed:
        print("Nothing to do (all sentinels already present).")
        return 0

    P.with_suffix(".html.bak").write_text(original, encoding="utf-8")
    P.write_text(src, encoding="utf-8", newline="\n")
    print("Integrated game preview into docs/index.html (backup -> index.html.bak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
