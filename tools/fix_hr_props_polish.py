#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_hr_props_polish.py
----------------------
Polish the Home Run Props block inside "Top Probable Outcomes":

  1. FIX the empty "()" after each name. The diag has no away_team/home_team
     columns -- the teams live in the `matchup` string ("AWY @ HOM"). Derive the
     abbrevs from matchup; also capture the opposing starter (home_sp_name /
     away_sp_name) and opponent for context.
  2. ADD a one-line methodology note + a per-row "why" line explaining the
     probability (season HR/PA compounded over ~4.3 PA, vs the opposing SP).
  3. Reword the empty-state note (supersedes fix_hr_props_note.py) so it reads
     "waiting on lineups", not a bake/deploy gap.

Display-only: docs/index.html text/markup. No data/model/logic change to the
probability itself (still the pitcher-neutral season-rate ranking). Idempotent.
Run from repo root.
"""
import sys

F = "docs/index.html"

EDITS = [
    # (sentinel, old, new)
    (
        'const oppTeam = side === "away"',
        '      const team = (side === "away" ? r.away_team : r.home_team) || "";',
        '''      // diag has no away_team/home_team columns; derive abbrevs from matchup "AWY @ HOM"
      const _mp = matchup.split("@");
      const _awy = (_mp[0] || "").split("(")[0].trim();
      const _hom = (_mp[1] || "").split("(")[0].trim();
      const team = side === "away" ? _awy : _hom;
      const oppTeam = side === "away" ? _hom : _awy;
      const oppSP = (side === "away" ? r.home_sp_name : r.away_sp_name) || "";''',
    ),
    (
        'oppSP: oppSP, oppTeam: oppTeam',
        '        out.push({ name: b.name, team: team, matchup: matchup, prob: p, hr: hr, pa: pa });',
        '        out.push({ name: b.name, team: team, matchup: matchup, prob: p, hr: hr, pa: pa, oppSP: oppSP, oppTeam: oppTeam });',
    ),
    (
        'Ranking is pitcher-neutral; the opposing starter',
        '''    html += `<h3 style="margin-top:0.8rem;color:var(--accent);">Home Run Props <span class="muted" style="font-size:0.78rem;font-weight:normal;">(ranked by single-game HR probability)</span></h3>`;''',
        '''    html += `<h3 style="margin-top:0.8rem;color:var(--accent);">Home Run Props <span class="muted" style="font-size:0.78rem;font-weight:normal;">(ranked by single-game HR probability)</span></h3>`;
    html += `<div class="muted" style="font-size:0.74rem;margin:0.1rem 0 0.5rem;line-height:1.4;">Model: P(&ge;1 HR) = 1 - (1 - season HR/PA)<sup>4.3</sup> &mdash; each hitter's season home-run rate compounded over ~4.3 expected plate appearances. Ranking is pitcher-neutral; the opposing starter is shown for matchup context (the per-game preview applies the SP HR/9 adjustment).</div>`;''',
    ),
    (
        'const _sp = (item.oppSP',
        '      html += `<div style="display:flex;justify-content:space-between;align-items:baseline;gap:0.6rem;padding:0.4rem 0.2rem;border-bottom:1px solid rgba(255,255,255,0.05);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">`',
        '''      const _sp = (item.oppSP && item.oppSP !== "TBD") ? item.oppSP : "";
      const _vs = _sp ? `vs ${_sp}${item.oppTeam ? " (" + item.oppTeam + ")" : ""}` : (item.oppTeam ? "vs " + item.oppTeam : "");
      html += `<div style="padding:0.4rem 0.2rem;border-bottom:1px solid rgba(255,255,255,0.05);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">`
            +   `<div style="display:flex;justify-content:space-between;align-items:baseline;gap:0.6rem;">`''',
    ),
    (
        'season HR/PA compounded over ~4.3 PA',
        '''(${rate}%)</span></span>`
            + `</div>`;''',
        '''(${rate}%)</span></span>`
            +   `</div>`
            +   `<div class="muted" style="font-size:0.74rem;margin-top:0.22rem;line-height:1.4;">${rate}% season HR/PA compounded over ~4.3 PA ${_vs} &rarr; ${pct}% chance of &ge;1 HR</div>`
            + `</div>`;''',
    ),
    (
        'starting lineups are posted (~2h before first pitch)',
        'Home Run Props populate once per-batter season HR/PA is baked into the diag (run a slate refresh).',
        'Home Run Props populate once starting lineups are posted (~2h before first pitch).',
    ),
]


def main():
    with open(F, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    applied = 0
    for i, (sentinel, old, new) in enumerate(EDITS, 1):
        if sentinel in work:
            print(f"  edit {i}: skip (already applied)")
            continue
        c = work.count(old)
        if c != 1:
            print(f"  edit {i}: ERROR anchor count={c} (need 1)")
            sys.exit(1)
        work = work.replace(old, new, 1)
        applied += 1
        print(f"  edit {i}: applied")
    with open(F, "w", encoding="utf-8", newline="") as fh:
        fh.write(work.replace("\n", nl))
    print(f"  done ({applied} applied)")


if __name__ == "__main__":
    main()
