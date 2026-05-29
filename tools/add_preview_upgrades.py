#!/usr/bin/env python3
"""UI upgrades to the in-expander game preview (depends on add_game_preview.py).

  1. Restyle the live "top hitters" lists into Quant-Terminal tables
     (Name/Pos/OPS/HR/AVG/PA), OPS heatmapped (high = hot).
  2. Restyle "probable starters" into a table with DIRECTIONAL heat:
     ERA/WHIP low = hot, K/9 high = hot (pitcher-success mapping).
  3. Add inline heat "chips" to the bullpen narrative metrics (ERA/WHIP/K9),
     preserving the leverage/fatigue narrative.
  4. Heatmap the SP K% chip in the projected-lineup header (high = hot).

Directional rule (per user): map color to PITCHER success — low ERA/WHIP/xwOBA
= red/hot, high K%/K9 = red/hot. invert=true flips the scale for "low is good".

All edits are idempotent + backed up. Caller node --checks the script.
REQUIRES add_game_preview.py to have run first (uses its .gp-* CSS + theme).
"""
import sys
from pathlib import Path

P = Path("docs/index.html")

FN_ADD = r'''/* GP-UPGRADES-FN */
// Directional heatmap. invert=true => LOW is good/hot (ERA, WHIP, xwOBA-allowed).
// invert=false => HIGH is good/hot (K%, K/9, OPS).
function _gpHeatDir(v, lo, mid, hi, invert){
  if (v == null || isNaN(v)) return "";
  var t = v <= mid ? (v - mid) / (mid - lo) : (v - mid) / (hi - mid);
  t = Math.max(-1, Math.min(1, t));
  if (invert) t = -t;
  var lvl = Math.round(t * 5);
  return lvl < 0 ? "gp-c" + (-lvl) : lvl > 0 ? "gp-h" + lvl : "gp-z";
}
// Inline heat "chip" for a single metric value (used in the bullpen narrative).
function _gpStat(raw, lo, mid, hi, invert){
  var cls = _gpHeatDir(parseFloat(raw), lo, mid, hi, invert);
  var disp = (raw == null || raw === "") ? "-" : raw;
  return '<span class="gp-stat ' + cls + '">' + disp + '</span>';
}
// Season "top hitters" -> Quant-Terminal table, OPS heatmapped (high = hot).
function _gpBatterTable(bats){
  if (!bats || !bats.length) return '<p class="gp-await">No qualified hitters in the snapshot yet.</p>';
  var rows = "";
  for (var i = 0; i < bats.length; i++){
    var b = bats[i];
    var opsNum = (b.ops_f != null) ? b.ops_f : parseFloat(b.ops);
    var opsCls = isNaN(opsNum) ? "" : _gpHeatDir(opsNum, 0.600, 0.720, 0.900, false);
    rows += '<tr>' +
      '<td class="gp-name"><span class="gp-dot"></span>' + (b.name || "") + '</td>' +
      '<td class="gp-ord">' + (b.pos || "-") + '</td>' +
      '<td class="gp-ops ' + opsCls + '">' + (b.ops != null ? b.ops : "-") + '</td>' +
      '<td>' + (b.hr != null ? b.hr : "-") + '</td>' +
      '<td>' + (b.avg != null ? b.avg : "-") + '</td>' +
      '<td class="gp-pa">' + (b.pa != null ? b.pa : "-") + '</td>' +
      '</tr>';
  }
  return '<div class="gp-scroll"><table class="gp-tbl"><thead><tr>' +
    '<th class="gp-name">Batter</th><th class="gp-ord">Pos</th>' +
    '<th>OPS</th><th>HR</th><th>AVG</th><th>PA</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div>';
}
// Probable starters -> table; directional heat (ERA/WHIP low=hot, K/9 high=hot).
function _gpPitcherTable(awayP, homeP, awayAbbr, homeAbbr){
  function prow(p, abbr){
    if (!p) return '<tr><td class="gp-name">' + abbr + '</td><td class="gp-await" colspan="5" style="padding:0.25rem 0.5rem;">TBD</td></tr>';
    var hand = (p.hand || "?") + "HP";
    return '<tr>' +
      '<td class="gp-name"><span class="gp-dot"></span>' + (p.name || "") + '<small>' + abbr + ' &middot; ' + hand + '</small></td>' +
      '<td>' + (p.ip || "0") + '</td>' +
      '<td class="gp-ops ' + _gpHeatDir(parseFloat(p.era), 2.5, 3.9, 5.5, true) + '">' + (p.era || "-") + '</td>' +
      '<td class="gp-ops ' + _gpHeatDir(parseFloat(p.whip), 1.0, 1.25, 1.55, true) + '">' + (p.whip || "-") + '</td>' +
      '<td class="gp-ops ' + _gpHeatDir(parseFloat(p.k9), 6.5, 8.8, 11.5, false) + '">' + (p.k9 || "-") + '</td>' +
      '<td class="gp-pa">' + (p.gs || 0) + '</td>' +
      '</tr>';
  }
  return '<div class="gp-scroll"><table class="gp-tbl"><thead><tr>' +
    '<th class="gp-name">Probable SP</th><th>IP</th><th>ERA</th><th>WHIP</th><th>K/9</th><th>GS</th>' +
    '</tr></thead><tbody>' + prow(awayP, awayAbbr) + prow(homeP, homeAbbr) + '</tbody></table></div>';
}
'''

CSS_ADD = (
    "\n  /* GP-UPGRADES-CSS - inline heat chips for pitching metrics */\n"
    "  .gp-stat{display:inline-block;padding:0 0.34rem;border-radius:3px;font-weight:600;font-variant-numeric:tabular-nums;}\n"
)

# (old, new, human label) — each idempotent + asserted unique.
REPLACE = [
    (r'<ul>${fmtBatters(preview.awayBatters)}</ul>',
     r'${_gpBatterTable(preview.awayBatters)}',
     "away top-hitters table"),
    (r'<ul>${fmtBatters(preview.homeBatters)}</ul>',
     r'${_gpBatterTable(preview.homeBatters)}',
     "home top-hitters table"),
    ('        <ul>\n'
     '          ${fmtPitcher(preview.awayPitcher, preview.awayAbbr || "Away")}\n'
     '          ${fmtPitcher(preview.homePitcher, preview.homeAbbr || "Home")}\n'
     '        </ul>',
     '        ${_gpPitcherTable(preview.awayPitcher, preview.homePitcher, preview.awayAbbr || "Away", preview.homeAbbr || "Home")}',
     "probable-starters table"),
    ('`<div><strong>${r.name}</strong> <span class="muted">(${labels[i]})</span> '
     '— ${r.era} ERA, ${r.whip} WHIP, ${r.sv} SV, ${r.hld} HLD, ${r.ip} IP, ${r.k9 || "-"} K/9</div>`',
     '`<div><strong>${r.name}</strong> <span class="muted">(${labels[i]})</span> '
     '— ${_gpStat(r.era,2.5,3.9,5.5,true)} ERA, ${_gpStat(r.whip,1.0,1.25,1.55,true)} WHIP, '
     '${r.sv} SV, ${r.hld} HLD, ${r.ip} IP, ${_gpStat(r.k9,6.5,8.8,11.5,false)} K/9</div>`',
     "bullpen metric heat-chips"),
    ('''  var kbit = function(k){ return isFinite(k) ? ' <span class="muted">&middot; ' + k.toFixed(1) + '% K</span>' : ""; };''',
     '''  var kbit = function(k){ return isFinite(k) ? ' <span class="gp-stat ' + _gpHeatDir(k,15,22,30,false) + '">' + k.toFixed(1) + '% K</span>' : ""; };''',
     "SP K% heat-chip in projected-lineup header"),
]


def main():
    src = P.read_text(encoding="utf-8")
    # dependency guard: GP-PREVIEW must already be applied (on origin)
    if "_gpHeatClass" not in src or "GP-PREVIEW-CSS" not in src:
        print("ERROR: base game preview not found. Run PUSH_GAME_PREVIEW.bat first "
              "(and let it push), then re-run this.")
        return 2
    original = src

    if "/* GP-UPGRADES-CSS" not in src:
        assert src.count("</style>") == 1, "</style> anchor not unique"
        src = src.replace("</style>", CSS_ADD + "</style>", 1)
        print("  + CSS (.gp-stat)")
    if "/* GP-UPGRADES-FN" not in src:
        anchor = "function formatGamePreview(preview) {"
        assert src.count(anchor) == 1, "formatGamePreview anchor not unique"
        src = src.replace(anchor, FN_ADD + "\n" + anchor, 1)
        print("  + functions (_gpHeatDir/_gpStat/_gpBatterTable/_gpPitcherTable)")

    for old, new, label in REPLACE:
        if new in src:
            print("  - %s already applied; skipping" % label)
            continue
        n = src.count(old)
        if n != 1:
            raise SystemExit("ANCHOR ERROR (%s): found %d (expected 1)" % (label, n))
        src = src.replace(old, new, 1)
        print("  ~ %s" % label)

    if src == original:
        print("Nothing to do (all upgrades already present).")
        return 0
    P.with_suffix(".html.bak2").write_text(original, encoding="utf-8")
    P.write_text(src, encoding="utf-8", newline="\n")
    print("Applied preview UI upgrades (backup -> index.html.bak2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
