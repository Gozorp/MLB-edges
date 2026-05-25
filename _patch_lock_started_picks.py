#!/usr/bin/env python3
"""
_patch_lock_started_picks.py
============================
Pipeline-level fix: when the daily-slate bake re-runs after first pitch
for a game, freeze the pick. The model's output (the raw fresh-from-CSV
PIT-vs-CHC flip the user saw today) reflects new bullpen-fatigue info
that should NOT retroactively change what the user was looking at
pre-game. Lock the pick + grade + tier columns to the prior CSV row's
values once MLB Stats API reports the game's abstractGameState in
("Live", "Final").

Adds three helpers + one call site in main_predict.run:
  - _load_prior_picks_for_lock(out_picks) -> DataFrame|None
    Read the prior CSV BEFORE any to_csv overwrites it. Returns None
    on absence or read error.
  - _games_started_map(slate_date) -> {matchup: bool}
    Fetch MLB schedule once; map "{away_abbr} @ {home_abbr}" -> started?
  - _apply_started_game_lock(new_df, prior_df, started_map) -> int
    In-place: for each row whose game is past pre-game AND whose prior
    row exists with a non-TBD pick, restore the LOCK_COLUMNS from
    prior. Tags stress_warnings with "locked_at_first_pitch".

Lock columns are the ones that constitute "the pick" from a user POV:
pick / p_model / pick_prob / f5_prob / full_prob / fair_prob / edge_pp /
grade / grade_reasons / grade_score / pre_cap_score / pre_cap_grade /
tier / signals / why_skipped + stake-sizing Kelly fields.

NOT locked (these legitimately update mid-game): post-game result
columns (winner / score), live tracker, bullpen meta JSON sidecars.
"""
from __future__ import annotations

import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "mlb_edge" / "main_predict.py"


def must_replace(src: str, old: str, new: str, label: str = "") -> str:
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    return src.replace(old, new, 1)


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    n0 = len(src)
    print(f"input: {TARGET} ({n0} bytes)")

    # ---------- 1. Inject helpers right BEFORE `def run(` ----------
    helpers = (
        '# =============================================================\n'
        '# Locked-pick logic (2026-05-25)\n'
        '#   When the bake re-runs after first pitch, the pick column\n'
        '#   should NOT flip mid-game. Capture prior CSV at run start,\n'
        '#   check MLB status per game, and for any game whose state is\n'
        '#   past pre-game restore the LOCK_COLUMNS from the prior row.\n'
        '# =============================================================\n'
        '_LOCK_COLUMNS = (\n'
        '    "pick", "p_model", "pick_prob",\n'
        '    "f5_prob", "full_prob", "fair_prob", "edge_pp",\n'
        '    "grade", "grade_reasons", "grade_score",\n'
        '    "pre_cap_score", "pre_cap_grade",\n'
        '    "tier", "signals", "why_skipped",\n'
        '    "ev_per_dollar", "kelly_full", "kelly_quarter", "kelly_eighth",\n'
        ')\n'
        '\n'
        '\n'
        'def _load_prior_picks_for_lock(out_picks_path):\n'
        '    """Read prior CSV BEFORE any to_csv overwrites it.\n'
        '\n'
        '    Returns DataFrame or None.\n'
        '    """\n'
        '    if not out_picks_path:\n'
        '        return None\n'
        '    p = Path(out_picks_path)\n'
        '    if not p.exists() or p.stat().st_size == 0:\n'
        '        return None\n'
        '    try:\n'
        '        return pd.read_csv(p)\n'
        '    except Exception as e:\n'
        '        log.warning("[lock] could not read prior CSV %s: %s", p, e)\n'
        '        return None\n'
        '\n'
        '\n'
        'def _games_started_map(slate_date):\n'
        '    """Fetch MLB schedule for the date once.\n'
        '\n'
        '    Returns {\\"{away_abbr} @ {home_abbr}\\": True if game past pre-game}.\n'
        '    abstractGameState == \\"Preview\\" means scheduled / pre-game; anything\n'
        '    else (Live / Final) counts as started.\n'
        '    """\n'
        '    out = {}\n'
        '    try:\n'
        '        import urllib.request as _ur\n'
        '        import json as _json\n'
        '        url = (\n'
        '            "https://statsapi.mlb.com/api/v1/schedule"\n'
        '            f"?sportId=1&date={slate_date.isoformat()}"\n'
        '        )\n'
        '        with _ur.urlopen(url, timeout=10) as resp:\n'
        '            j = _json.loads(resp.read().decode("utf-8"))\n'
        '        for d in j.get("dates", []) or []:\n'
        '            for g in d.get("games", []) or []:\n'
        '                status = g.get("status") or {}\n'
        '                state = status.get("abstractGameState", "")\n'
        '                started = state in ("Live", "Final")\n'
        '                teams = g.get("teams") or {}\n'
        '                away = (teams.get("away") or {}).get("team") or {}\n'
        '                home = (teams.get("home") or {}).get("team") or {}\n'
        '                a = away.get("abbreviation") or ""\n'
        '                h = home.get("abbreviation") or ""\n'
        '                if a and h:\n'
        '                    out[f"{a} @ {h}"] = started\n'
        '    except Exception as e:\n'
        '        log.warning("[lock] failed to fetch MLB schedule: %s", e)\n'
        '    return out\n'
        '\n'
        '\n'
        'def _apply_started_game_lock(new_df, prior_df, started_map):\n'
        '    """In place: restore _LOCK_COLUMNS from prior_df for any row whose\n'
        '    matchup has started AND whose prior row has a real (non-TBD) pick.\n'
        '\n'
        '    Returns count of locked rows.\n'
        '    """\n'
        '    if prior_df is None or prior_df.empty:\n'
        '        return 0\n'
        '    if "matchup" not in new_df.columns or "matchup" not in prior_df.columns:\n'
        '        return 0\n'
        '    import re as _re\n'
        '    # Build prior lookup by matchup, taking the FIRST occurrence so a\n'
        '    # doubleheader doesn\'t blow up (downstream G2/G3 suffixing is\n'
        '    # applied at render time, not in the CSV).\n'
        '    prior_idx = {}\n'
        '    for _, pr in prior_df.iterrows():\n'
        '        mk = str(pr.get("matchup", "")).strip()\n'
        '        if mk and mk not in prior_idx:\n'
        '            prior_idx[mk] = pr\n'
        '    n_locked = 0\n'
        '    for i, row in new_df.iterrows():\n'
        '        mk = str(row.get("matchup", "")).strip()\n'
        '        if not mk:\n'
        '            continue\n'
        '        # Strip any "(G2 of 3)" / "(G2)" suffix before matching the\n'
        '        # MLB schedule (schedule keys are bare).\n'
        '        bare = _re.sub(r"\\s*\\([^)]*\\)\\s*$", "", mk).strip()\n'
        '        started = started_map.get(bare, False) or started_map.get(mk, False)\n'
        '        if not started:\n'
        '            continue\n'
        '        if mk not in prior_idx:\n'
        '            continue\n'
        '        prior_row = prior_idx[mk]\n'
        '        # Only lock when the PRIOR pick was a real pick — don\'t freeze\n'
        '        # TBD/PENDING; in that case let fresh model output stand.\n'
        '        prior_pick = str(prior_row.get("pick", "")).strip().upper()\n'
        '        if prior_pick in ("", "TBD", "NAN", "NONE"):\n'
        '            continue\n'
        '        # Copy locked columns from prior row over the fresh row.\n'
        '        for col in _LOCK_COLUMNS:\n'
        '            if col in new_df.columns and col in prior_row.index:\n'
        '                v = prior_row[col]\n'
        '                if pd.notna(v):\n'
        '                    new_df.at[i, col] = v\n'
        '        # Tag in stress_warnings so the lock is visible in audits.\n'
        '        if "stress_warnings" in new_df.columns:\n'
        '            sw_raw = new_df.at[i, "stress_warnings"]\n'
        '            sw = str(sw_raw) if pd.notna(sw_raw) else ""\n'
        '            if "locked_at_first_pitch" not in sw:\n'
        '                new_df.at[i, "stress_warnings"] = (\n'
        '                    f"{sw};locked_at_first_pitch" if sw\n'
        '                    else "locked_at_first_pitch"\n'
        '                )\n'
        '        n_locked += 1\n'
        '    return n_locked\n'
        '\n'
        '\n'
    )

    src = must_replace(
        src,
        'def run(slate_date: date,\n',
        helpers + 'def run(slate_date: date,\n',
        "1: inject lock helpers",
    )
    print("[ok]   1: lock helpers injected before run()")

    # ---------- 2. Capture prior CSV at top of run() ----------
    src = must_replace(
        src,
        '        skip_news: bool = False) -> None:\n'
        '    if not skip_savant_refresh:\n',
        '        skip_news: bool = False) -> None:\n'
        '    # Capture prior CSV BEFORE any to_csv overwrites it. Used at the\n'
        '    # end of the grading pass to freeze picks for games already in\n'
        '    # progress (avoids mid-game flips when bullpen state changes,\n'
        '    # 2026-05-25 user request).\n'
        '    _prior_picks_for_lock = _load_prior_picks_for_lock(out_picks)\n'
        '\n'
        '    if not skip_savant_refresh:\n',
        "2: capture prior CSV at run() start",
    )
    print("[ok]   2: prior CSV captured at run() start")

    # ---------- 3. Apply lock after grade_picks(), before re-write ----------
    src = must_replace(
        src,
        '                graded = parlay_builder.grade_picks(\n'
        '                    table, anchor=matchup_to_sps, slate_date=slate_date,\n'
        '                )\n'
        '                parlay_path = Path(f"parlay_{slate_date.isoformat()}.txt")\n',
        '                graded = parlay_builder.grade_picks(\n'
        '                    table, anchor=matchup_to_sps, slate_date=slate_date,\n'
        '                )\n'
        '                # Freeze picks for games that have already started.\n'
        '                # Restores LOCK_COLUMNS from the prior CSV row so a\n'
        '                # mid-game re-bake (e.g. bullpen-fatigue flip) never\n'
        '                # changes what the user saw pre-game.\n'
        '                try:\n'
        '                    _started_map = _games_started_map(slate_date)\n'
        '                    _n_locked = _apply_started_game_lock(\n'
        '                        graded, _prior_picks_for_lock, _started_map,\n'
        '                    )\n'
        '                    if _n_locked:\n'
        '                        log.info(\n'
        '                            "[lock] preserved pre-game picks for %d "\n'
        '                            "started game(s)", _n_locked,\n'
        '                        )\n'
        '                except Exception as _e_lock:\n'
        '                    log.warning(\n'
        '                        "[lock] failed (continuing without lock): %s",\n'
        '                        _e_lock,\n'
        '                    )\n'
        '                parlay_path = Path(f"parlay_{slate_date.isoformat()}.txt")\n',
        "3: apply lock after grade_picks",
    )
    print("[ok]   3: lock invoked after grade_picks()")

    TARGET.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {TARGET} ({n1} bytes, delta {n1-n0:+d})")

    # AST sanity check
    import ast
    try:
        ast.parse(src)
        print("[ok]   AST parse: OK")
    except SyntaxError as e:
        print(f"[FAIL] AST parse: line {e.lineno}: {e.msg}")
        sys.exit(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
