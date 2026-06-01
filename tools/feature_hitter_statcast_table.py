#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feature_hitter_statcast_table.py   (Phase 3 of the hitter-status feature)
-------------------------------------------------------------------------
Adds a per-team "Statcast Profile" hitter table to the expander's game preview,
beside the existing platoon table, with the requested columns:
    Pos · BBE · LA° · EV · HH% · xwOBA · xBA · xSLG · K% · BB% · Sprint
xwOBA is heatmapped (directional). Missing values render "-" (so it degrades
gracefully before the bake-join populates the fields). Reuses the existing
.gp-tbl Quant-Terminal styling.

3 idempotent edits to docs/index.html. Run from repo root.
"""
import sys

F = "docs/index.html"
EDITS = []  # (old, new, sentinel)

# --- Edit 1: renderer fns, inserted before _renderProjectedLineupTable -------
FNS = r"""function _gpNum(v, dec, suf, stripZero){
  if (v == null || v === "" || isNaN(v)) return '<td class="gp-mini gp-na">-</td>';
  var t = Number(v).toFixed(dec);
  if (stripZero) t = t.replace(/^0/, "");
  return '<td class="gp-mini">' + t + (suf || "") + '</td>';
}
// Per-team Statcast hitter table (season batted-ball / expected stats / sprint),
// built from the baked top-5 batter JSON. xwOBA is heatmapped green->red.
function _gpStatcastTable(bats, label){
  if (!bats || !bats.length){
    return '<div class="gp-team"><div class="gp-th">' + label + '</div>' +
      '<p class="gp-await">⌛ Awaiting Starting Lineup.</p></div>';
  }
  var rows = "";
  for (var i = 0; i < bats.length; i++){
    var b = bats[i];
    var xw;
    if (b.xwoba == null || isNaN(b.xwoba)){
      xw = '<td class="gp-mini gp-na">-</td>';
    } else {
      xw = '<td class="gp-mini ' + _gpHeatDir(Number(b.xwoba), 0.290, 0.320, 0.370, false) +
        '">' + Number(b.xwoba).toFixed(3).replace(/^0/, "") + '</td>';
    }
    rows += '<tr>' +
      '<td class="gp-ord">' + (b.order != null ? b.order : "") + '</td>' +
      '<td class="gp-name"><span class="gp-dot"></span>' + (b.name || "") +
        '<small>' + (b.bat_side || "") + '</small></td>' +
      '<td class="gp-mini">' + (b.pos || "-") + '</td>' +
      _gpNum(b.bbe, 0, "", false) +
      _gpNum(b.la, 1, "°", false) +
      _gpNum(b.ev, 1, "", false) +
      _gpNum(b.hard_hit_pct, 1, "%", false) +
      xw +
      _gpNum(b.xba, 3, "", true) +
      _gpNum(b.xslg, 3, "", true) +
      _gpNum(b.k_pct, 1, "%", false) +
      _gpNum(b.bb_pct, 1, "%", false) +
      _gpNum(b.sprint, 1, "", false) +
      '</tr>';
  }
  return '<div class="gp-team"><div class="gp-th">' + label + '</div>' +
    '<div class="gp-scroll"><table class="gp-tbl gp-tbl-wide"><thead><tr>' +
    '<th class="gp-ord">#</th><th class="gp-name">Batter</th>' +
    '<th>Pos</th><th>BBE</th><th>LA°</th><th>EV</th><th>HH%</th>' +
    '<th>xwOBA</th><th>xBA</th><th>xSLG</th><th>K%</th><th>BB%</th><th>Sprint</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div></div>';
}
function _renderProjectedLineupTable(r){"""
EDITS.append(("function _renderProjectedLineupTable(r){", FNS,
              "function _gpStatcastTable(bats, label)"))

# --- Edit 2: inject the Statcast grid after the platoon grid ------------------
OLD2 = (
    '      _gpTeamTable(home, awaySP, homeAbbr + " Hitters") +\n'
    "    '</div>' +\n"
    '    \'<div class="gp-legend"><span class="gp-leg-bar"></span>\' +'
)
NEW2 = (
    '      _gpTeamTable(home, awaySP, homeAbbr + " Hitters") +\n'
    "    '</div>' +\n"
    '    \'<div class="gp-head" style="margin-top:.7rem;">Statcast Profile\'+\n'
    '      \'<span class="gp-sub">season batted-ball &middot; expected stats &middot; sprint</span></div>\' +\n'
    '    \'<div class="gp-grid">\' +\n'
    '      _gpStatcastTable(away, awayAbbr + " Hitters") +\n'
    '      _gpStatcastTable(home, homeAbbr + " Hitters") +\n'
    "    '</div>' +\n"
    '    \'<div class="gp-legend"><span class="gp-leg-bar"></span>\' +'
)
EDITS.append((OLD2, NEW2, "_gpStatcastTable(away, awayAbbr"))

# --- Edit 3: minimal CSS for the wide numeric cells --------------------------
CSS = """
/* === statcast-hitter-table-2026 === */
.gp-tbl-wide th, .gp-tbl-wide td { font-size: .72rem; }
.gp-mini { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; padding: 2px 6px; }
/* === end statcast-hitter-table-2026 === */
"""
EDITS.append(("</style>", CSS + "</style>", "statcast-hitter-table-2026"))


def main():
    with open(F, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    applied = skipped = 0
    for old, new, sentinel in EDITS:
        if sentinel in work:
            print(f"  skip (already applied): {sentinel[:42]}")
            skipped += 1
            continue
        n = work.count(old)
        if n != 1:
            print(f"  ERROR anchor count={n} (need 1): {sentinel[:42]}")
            sys.exit(1)
        work = work.replace(old, new, 1)
        applied += 1
        print(f"  applied: {sentinel[:42]}")
    if applied:
        with open(F, "w", encoding="utf-8", newline="") as fh:
            fh.write(work.replace("\n", nl))
    print(f"DONE applied={applied} skipped={skipped}")
    if applied == 0 and skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
