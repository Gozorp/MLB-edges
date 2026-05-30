#!/usr/bin/env python3
"""
feature_bullpen_kmatrix.py
--------------------------
Bullpen edge: show each hitter's K% vs EVERY opposing reliever at once
(hitters x relievers heatmap matrix) instead of vs the single top-leverage arm.

Frontend-only. _fetchTeamRoster already builds the FULL bullpen (every reliever,
each with k9), leverage-sorted; the client just .slice(0,3)'d it. We keep the
full pen and render a matrix that reuses the existing .gp-tbl + _gpHeatDir
heatmap system (no new CSS, no backend change).

Two edits to docs/index.html:
  1. fetchGamePreview : add awayBullpenFull / homeBullpenFull (full pens).
  2. renderBullpenEdge: rewrite as a per-team hitters x relievers K% matrix.

Idempotent (keyed to unique markers). Anchors that don't match abort loudly.
Run from the repo root.
"""
import sys

INDEX = "docs/index.html"

# --- Edit 1: keep the full bullpen alongside the existing top-3 slice --------
OLD_1 = (
    "  out.awayBullpen = aR.bullpen.slice(0, 3);\n"
    "  out.homeBullpen = hR.bullpen.slice(0, 3);\n"
)
NEW_1 = (
    "  out.awayBullpen = aR.bullpen.slice(0, 3);\n"
    "  out.homeBullpen = hR.bullpen.slice(0, 3);\n"
    "  // Full bullpens (every tracked arm, leverage-sorted) for the K% matrix.\n"
    "  out.awayBullpenFull = aR.bullpen;\n"
    "  out.homeBullpenFull = hR.bullpen;\n"
)
MARK_1 = "out.awayBullpenFull = aR.bullpen;"

# --- Edit 2: rewrite renderBullpenEdge as a K% matrix ------------------------
OLD_2 = '''function renderBullpenEdge(preview) {
  if (!preview) return "";
  // Look up bullpen_meta for the strain notes (best-effort; if the
  // sidecar isn't baked yet we simply omit the strain line).
  const meta = (function() {
    try {
      const m = `${preview.awayAbbr || preview.awayName} @ ${preview.homeAbbr || preview.homeName}`;
      return _bullpenMetaForMatchup(m) || {};
    } catch (_) { return {}; }
  })();
  const awayCloser = _topBullpenArm(preview.awayBullpen);
  const homeCloser = _topBullpenArm(preview.homeBullpen);

  const kList = (lineup, opposingCloser, teamName, teamAbbr, oppTeamBlock) => {
    if (!opposingCloser) {
      return `<div class="muted" style="font-size:0.82rem;">${teamName}: opposing bullpen not yet hydrated.</div>`;
    }
    if (!lineup || !lineup.length) {
      return `<div class="muted" style="font-size:0.82rem;">${teamName}: lineup card not yet posted.</div>`;
    }
    const rows = [];
    for (const b of lineup) {
      const pK = _batterKProb(b, opposingCloser);
      if (pK == null) continue;
      rows.push({ name: b.name, pos: b.pos, pa: b.pa, prob: pK });
    }
    if (!rows.length) {
      return `<div class="muted" style="font-size:0.82rem;">${teamName}: not enough batter PA for K-projection.</div>`;
    }
    rows.sort((a, b) => b.prob - a.prob);
    const meanK = rows.reduce((s, r) => s + r.prob, 0) / rows.length;
    const contactPct = (1 - meanK) * 100;
    const k9Str = parseFloat(opposingCloser.k9 || 0).toFixed(1);
    const strain = _bullpenStrainNote(oppTeamBlock, opposingCloser.name);
    let html = `<div>`
             + `<div style="display:flex;align-items:baseline;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.25rem;">`
             +   `<strong style="font-size:0.88rem;">${teamAbbr || teamName} lineup</strong>`
             +   `<span class="muted" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:0.76rem;">`
             +     `contact ${contactPct.toFixed(0)}% · vs ${opposingCloser.name} (${k9Str} K/9)`
             +   `</span>`
             + `</div>`;
    if (strain) {
      html += `<div style="font-size:0.76rem;margin:0.2rem 0 0.3rem 0;font-style:italic;">${strain}</div>`;
    }
    html += `<ol style="margin:0.2rem 0 0 1.4rem;padding:0;font-size:0.8rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">`;
    for (const r of rows) {
      const pct = (r.prob * 100).toFixed(0);
      const color = r.prob >= 0.32 ? "var(--red)"
                  : r.prob >= 0.25 ? "var(--yellow)"
                  : "var(--muted)";
      html += `<li style="margin:0.1rem 0;">`
            +   `<span style="display:inline-block;min-width:3.2rem;color:${color};font-weight:700;">${pct}% K</span>`
            +   `<span class="muted"> · </span>`
            +   `<span style="color:var(--text);">${r.name}</span>`
            +   `<span class="muted"> (${r.pos || "-"})</span>`
            + `</li>`;
    }
    html += `</ol></div>`;
    return html;
  };

  return `<div class="preview-card" style="grid-column: 1 / -1;">`
       + `<h5>Bullpen edge <span class="muted" style="font-size:0.78rem;font-weight:normal;">— per-batter K vs the opposing top-leverage arm + manager-decision context</span></h5>`
       + `<div style="display:grid;grid-template-columns:1fr 1fr;gap:0.9rem;">`
       +   kList(preview.awayLineup, homeCloser, preview.awayName, preview.awayAbbr, meta.home)
       +   kList(preview.homeLineup, awayCloser, preview.homeName, preview.homeAbbr, meta.away)
       + `</div></div>`;
}'''

NEW_2 = '''function renderBullpenEdge(preview) {
  if (!preview) return "";
  // Full opposing bullpens (every tracked arm, leverage-sorted closer-first).
  // Fall back to the top-3 slice if the full pen wasn't captured (e.g. a
  // final-game preview that only hydrated the used arms).
  const homePen = (preview.homeBullpenFull && preview.homeBullpenFull.length)
                  ? preview.homeBullpenFull : (preview.homeBullpen || []);
  const awayPen = (preview.awayBullpenFull && preview.awayBullpenFull.length)
                  ? preview.awayBullpenFull : (preview.awayBullpen || []);

  // K% heat: low K (good for the hitter) = cold/blue, high K (pitcher edge) =
  // hot/red. Reuses the dashboard's directional heatmap so cell colors match
  // the rest of the expander.
  const heat   = (p) => (p == null) ? "gp-z" : _gpHeatDir(p * 100, 15, 24, 33, false);
  const pctTxt = (p) => (p == null) ? "·" : (p * 100).toFixed(0);
  const lastNm = (n) => { const s = (n || "").trim().split(/\\s+/); return s[s.length - 1] || (n || "—"); };

  const matrix = (lineup, bullpen, teamName, teamAbbr) => {
    if (!bullpen || !bullpen.length) {
      return `<div class="muted" style="font-size:0.82rem;">${teamName}: opposing bullpen not yet hydrated.</div>`;
    }
    if (!lineup || !lineup.length) {
      return `<div class="muted" style="font-size:0.82rem;">${teamName}: lineup card not yet posted.</div>`;
    }
    const arms = bullpen.slice(0, 8);  // leverage-sorted; cap so it stays readable
    const batRows = [];
    for (const b of lineup) {
      const cells = arms.map(a => _batterKProb(b, a));
      if (cells.every(c => c == null)) continue;   // skip thin-PA batters
      batRows.push({ name: b.name, pos: b.pos, cells });
    }
    if (!batRows.length) {
      return `<div class="muted" style="font-size:0.82rem;">${teamName}: not enough batter PA for K-projection.</div>`;
    }
    const armMean = arms.map((a, i) => {
      const v = batRows.map(r => r.cells[i]).filter(c => c != null);
      return v.length ? v.reduce((s, c) => s + c, 0) / v.length : null;
    });

    let h = `<div style="margin:0.1rem 0 0.3rem 0;">`
          + `<strong style="font-size:0.86rem;">${teamAbbr || teamName} lineup</strong>`
          + `<span class="muted" style="font-size:0.74rem;"> — K% vs each opposing reliever (closer → low-leverage)</span>`
          + `</div>`;
    h += `<div style="overflow-x:auto;max-width:100%;"><table class="gp-tbl">`;
    h += `<thead><tr><th class="gp-name">Batter</th>`;
    for (const a of arms) {
      const k9 = parseFloat(a.k9 || 0);
      const k9s = (isFinite(k9) && k9 > 0) ? k9.toFixed(1) : "—";
      h += `<th title="${a.name} · ${k9s} K/9">${lastNm(a.name)}<div style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--muted);">${k9s}</div></th>`;
    }
    h += `</tr></thead><tbody>`;
    for (const r of batRows) {
      h += `<tr><td class="gp-name">${r.name} <span class="muted">${r.pos || ""}</span></td>`;
      for (const c of r.cells) {
        h += `<td class="${heat(c)}" title="${pctTxt(c)}% K">${pctTxt(c)}</td>`;
      }
      h += `</tr>`;
    }
    h += `<tr><td class="gp-name muted" style="font-style:italic;">lineup avg</td>`;
    for (const m of armMean) {
      h += `<td class="${heat(m)}" style="font-weight:700;">${pctTxt(m)}</td>`;
    }
    h += `</tr></tbody></table></div>`;
    return h;
  };

  return `<div class="preview-card" style="grid-column: 1 / -1;">`
       + `<h5>Bullpen edge <span class="muted" style="font-size:0.78rem;font-weight:normal;">— each hitter's K% vs every opposing reliever (Log5; red = high-K / pitcher edge)</span></h5>`
       + `<div style="display:flex;flex-direction:column;gap:0.9rem;">`
       +   matrix(preview.awayLineup, homePen, preview.awayName, preview.awayAbbr)
       +   matrix(preview.homeLineup, awayPen, preview.homeName, preview.homeAbbr)
       + `</div></div>`;
}'''
MARK_2 = "const matrix = (lineup, bullpen, teamName, teamAbbr) =>"

EDITS = [
    (INDEX, OLD_1, NEW_1, MARK_1),
    (INDEX, OLD_2, NEW_2, MARK_2),
]


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def main():
    applied = skipped = 0
    for path, old, new, mark in EDITS:
        raw = _read(path)
        nl = "\r\n" if "\r\n" in raw else "\n"
        work = raw.replace("\r\n", "\n")
        if mark in work:
            print(f"  skip (already applied): {mark[:50]}")
            skipped += 1
            continue
        if work.count(old) != 1:
            print(f"  ERROR anchor count={work.count(old)} (need 1) for: {mark[:50]}")
            sys.exit(1)
        work = work.replace(old, new, 1)
        _write(path, work.replace("\n", nl))
        applied += 1
        print(f"  applied: {mark[:50]}")
    print(f"DONE  applied={applied}  skipped={skipped}")
    if applied == 0 and skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
