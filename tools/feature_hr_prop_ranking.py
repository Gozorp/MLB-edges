#!/usr/bin/env python3
"""
feature_hr_prop_ranking.py
--------------------------
Ship the long-parked HR-prop ranking (the "coming in Phase 1.5" note).

The frontend HR math (_hrProbability) already exists; the only gap is the data
the note names: per-batter SEASON HR/PA in the diag. Two changes:

BACKEND  mlb_edge/platoon_splits.py
  - get_career_splits: also fetch season HR + PA (one extra statsapi call,
    cached alongside the splits; the cache check now requires "season_PA" so
    stale entries refresh).
  - build_team_top_5_payload: add season_HR + season_PA to each batter record
    (and the NO_DATA stub) -> lands in away/home_top_5_batters_json.

FRONTEND docs/index.html
  - _topHRProps(rows): rank batters slate-wide by single-game HR probability
    from the baked season HR/PA (neutral pitcher factor for the overview).
  - renderTopProbableOutcomes: replace the Phase-1.5 note with a real
    "Home Run Props" ranked section (falls back to a friendly line until the
    next bake carries the new fields).

Idempotent. Run from the repo root.
"""
import sys

PS = "mlb_edge/platoon_splits.py"
IDX = "docs/index.html"

EDITS = []

# ---- BACKEND B1a: cache check requires the new season field ----------------
EDITS.append((PS,
    '    cached = _read_cache(player_id)\n'
    '    if cached is not None:\n'
    '        return cached',
    '    cached = _read_cache(player_id)\n'
    '    if cached is not None and "season_PA" in cached:\n'
    '        return cached',
    'if cached is not None and "season_PA" in cached:'))

# ---- BACKEND B1b: fetch season HR/PA before writing cache ------------------
EDITS.append((PS,
    '    _write_cache(player_id, out)\n'
    '    return out',
    '    # Season HR + PA (Phase 1.5 HR-prop ranking). Cached alongside the\n'
    '    # splits; the cache-version check above (requires "season_PA")\n'
    '    # refreshes pre-existing entries that lack it.\n'
    '    out["season_HR"] = 0\n'
    '    out["season_PA"] = 0\n'
    '    try:\n'
    '        sdata = _fetch_json(\n'
    '            f"https://statsapi.mlb.com/api/v1/people/{player_id}"\n'
    '            f"/stats?stats=season&group=hitting")\n'
    '        for s in sdata.get("stats", []):\n'
    '            for split in s.get("splits", []):\n'
    '                st = split.get("stat", {})\n'
    '                try:\n'
    '                    out["season_HR"] = int(st.get("homeRuns", 0) or 0)\n'
    '                except (TypeError, ValueError):\n'
    '                    pass\n'
    '                try:\n'
    '                    out["season_PA"] = int(st.get("plateAppearances", 0) or 0)\n'
    '                except (TypeError, ValueError):\n'
    '                    pass\n'
    '    except Exception as e:\n'
    '        log.debug("[platoon_splits] season HR/PA fetch failed for %s: %s",\n'
    '                  player_id, e)\n'
    '\n'
    '    _write_cache(player_id, out)\n'
    '    return out',
    'out["season_HR"] = 0'))

# ---- BACKEND B2: season fields on the success record ----------------------
EDITS.append((PS,
    '            "vs_today_SP_OPS": round(ops_today, 3) if ops_today else None,\n'
    '            "vs_today_SP_PA": pa_today,\n'
    '            "sample_flag": flag,',
    '            "vs_today_SP_OPS": round(ops_today, 3) if ops_today else None,\n'
    '            "vs_today_SP_PA": pa_today,\n'
    '            "season_HR": int(splits.get("season_HR", 0) or 0),\n'
    '            "season_PA": int(splits.get("season_PA", 0) or 0),\n'
    '            "sample_flag": flag,',
    '"season_HR": int(splits.get("season_HR", 0) or 0),'))

# ---- BACKEND B3: season fields on the NO_DATA stub ------------------------
EDITS.append((PS,
    '                "vs_today_SP_OPS": None, "vs_today_SP_PA": 0,\n'
    '                "sample_flag": "NO_DATA",',
    '                "vs_today_SP_OPS": None, "vs_today_SP_PA": 0,\n'
    '                "season_HR": 0, "season_PA": 0,\n'
    '                "sample_flag": "NO_DATA",',
    '"season_HR": 0, "season_PA": 0,'))

# ---- FRONTEND F1: insert _topHRProps before the renderer ------------------
EDITS.append((IDX,
    'function renderTopProbableOutcomes(rows, totalsByMatchup, results) {',
    '''// Rank batters slate-wide by single-game HR probability (Phase 1.5).
// Uses baked per-batter season HR/PA from the platoon top-5 batter JSON via
// the _hrProbability formula. Neutral pitcher factor for the slate-wide
// overview (the SP-weighted variant stays available in _hrProbability for
// per-game context).
function _topHRProps(rows) {
  const out = [];
  for (const r of rows || []) {
    const matchup = (r.matchup || "").trim();
    for (const side of ["away", "home"]) {
      let bats = [];
      try { bats = JSON.parse(r[side + "_top_5_batters_json"] || "[]") || []; }
      catch (_) { bats = []; }
      const team = (side === "away" ? r.away_team : r.home_team) || "";
      for (const b of bats) {
        const hr = parseInt((b.season_HR != null ? b.season_HR : b.hr) || 0);
        const pa = parseInt((b.season_PA != null ? b.season_PA : b.pa) || 0);
        if (!(pa >= 30) || hr < 0) continue;
        const p = _hrProbability({ hr: hr, pa: pa }, null, 4.3);
        if (p == null) continue;
        out.push({ name: b.name, team: team, matchup: matchup, prob: p, hr: hr, pa: pa });
      }
    }
  }
  return out.sort((a, b) => b.prob - a.prob);
}

function renderTopProbableOutcomes(rows, totalsByMatchup, results) {''',
    'function _topHRProps(rows) {'))

# ---- FRONTEND F2: replace the Phase-1.5 note with the ranked section ------
EDITS.append((IDX,
    '  html += `<div class="muted" style="font-size:0.78rem;margin-top:0.8rem;font-style:italic;">` +\n'
    '          `ⓘ HR-prop ranking coming in Phase 1.5 once per-batter season HR/PA is in the diag CSV.` +\n'
    '          `</div></div>`;',
    '  const hrs = _topHRProps(rows).slice(0, 10);\n'
    '  if (hrs.length) {\n'
    '    html += `<h3 style="margin-top:0.8rem;color:var(--accent);">Home Run Props <span class="muted" style="font-size:0.78rem;font-weight:normal;">(ranked by single-game HR probability)</span></h3>`;\n'
    '    hrs.forEach((item, i) => {\n'
    '      const pct = (item.prob * 100).toFixed(1);\n'
    '      const rate = item.pa > 0 ? (item.hr / item.pa * 100).toFixed(1) : "0.0";\n'
    '      html += `<div style="display:flex;justify-content:space-between;align-items:baseline;gap:0.6rem;padding:0.4rem 0.2rem;border-bottom:1px solid rgba(255,255,255,0.05);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">`\n'
    '            +   `<span><span class="muted">${i + 1}.</span> <strong>${item.name}</strong> <span class="muted">(${item.team})</span></span>`\n'
    '            +   `<span style="white-space:nowrap;"><strong style="color:var(--accent);">${pct}% HR</strong> <span class="muted" style="font-size:0.76rem;">· ${item.hr} HR / ${item.pa} PA (${rate}%)</span></span>`\n'
    '            + `</div>`;\n'
    '    });\n'
    '  } else {\n'
    '    html += `<div class="muted" style="font-size:0.78rem;margin-top:0.8rem;font-style:italic;">ⓘ Home Run Props populate once per-batter season HR/PA is baked into the diag (run a slate refresh).</div>`;\n'
    '  }\n'
    '  html += `</div>`;',
    'const hrs = _topHRProps(rows).slice(0, 10);'))


def _read(p):
    with open(p, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write(p, t):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(t)


def main():
    applied = skipped = 0
    for path, old, new, marker in EDITS:
        raw = _read(path)
        nl = "\r\n" if "\r\n" in raw else "\n"
        work = raw.replace("\r\n", "\n")
        if marker in work:
            print(f"  skip (already applied): {marker[:48]}")
            skipped += 1
            continue
        if work.count(old) != 1:
            print(f"  ERROR anchor count={work.count(old)} (need 1): {marker[:48]}")
            sys.exit(1)
        work = work.replace(old, new, 1)
        _write(path, work.replace("\n", nl))
        applied += 1
        print(f"  applied: {marker[:48]}")
    print(f"DONE applied={applied} skipped={skipped}")
    if applied == 0 and skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
