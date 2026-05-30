#!/usr/bin/env python3
"""
fix_thin_sp_count.py
--------------------
Surface the TRUE starter pitch count for sub-threshold ("thin") starters so the
PENDING_SP_DATA reason reads e.g. "85 Statcast pitches" instead of a misleading
"0".

Why (per Architecture Pre-Flight Rule 5 / Rule 12):
  pitcher_as_of() returns the all-NaN stat dict whenever len(df) < 100 (rate
  stats are too noisy below that). That early-return DISCARDS the real pitch
  count, so sp_n_pitches becomes NaN and main_predict prints it as "0". A
  starter who threw ~85 pitches in one recent start (genuinely thin, correctly
  PENDING) is then indistinguishable from a true 0 / a cache miss.

Fix = a DISPLAY-ONLY sidecar `sp_n_pitches_actual`:
  - point_in_time.pitcher_as_of   : attach the real len(df) on the thin path.
  - build_pipeline                : propagate as home/away_sp_n_pitches_actual
                                    (telemetry column, NOT a model feature).
  - main_predict                  : show *_actual in the thin-SP reason.

Guarantees (NOT touched): the model feature `home_sp_n_pitches`, the Bayesian-
shrinkage N-column, and the SP-savant 100-pitch gate. Gate decisions and scoring
are byte-identical; only the human-readable count changes.

Idempotent: re-running after a successful apply is a no-op (each edit is keyed
to a unique marker). Anchors that don't match abort loudly (no partial writes).
"""
import ast
import sys

# (path, old, new, marker_present_means_already_applied)
EDITS = [
    # ---- point_in_time.py : _nan_pitcher_dict key list -------------------
    (
        "mlb_edge/point_in_time.py",
        '        "sp_ip_per_start", "sp_era_xera_gap", "sp_n_pitches",\n'
        '        "sp_ttop3_penalty",\n'
        '    ]}',
        '        "sp_ip_per_start", "sp_era_xera_gap", "sp_n_pitches",\n'
        '        "sp_n_pitches_actual",\n'
        '        "sp_ttop3_penalty",\n'
        '    ]}',
        '        "sp_n_pitches_actual",\n        "sp_ttop3_penalty",',
    ),
    # ---- point_in_time.py : pitcher_as_of thin early-return --------------
    (
        "mlb_edge/point_in_time.py",
        '    df = statcast_df[\n'
        '        (statcast_df["pitcher"] == pitcher_id) &\n'
        '        (pd.to_datetime(statcast_df["game_date"]) < pd.Timestamp(as_of_date))\n'
        '    ]\n'
        '    if len(df) < min_pitches:\n'
        '        return _nan_pitcher_dict()',
        '    df = statcast_df[\n'
        '        (statcast_df["pitcher"] == pitcher_id) &\n'
        '        (pd.to_datetime(statcast_df["game_date"]) < pd.Timestamp(as_of_date))\n'
        '    ]\n'
        '    if len(df) < min_pitches:\n'
        '        # Below the stable-rate threshold: rate stats stay NaN (too noisy\n'
        '        # to report), but surface the TRUE pitch count so a thin arm reads\n'
        '        # "85", not "0", and a genuine 0 stays 0. Leaves the model feature\n'
        '        # sp_n_pitches and the SP-savant gate untouched. (fix 2026-05-29)\n'
        '        _thin = _nan_pitcher_dict()\n'
        '        _thin["sp_n_pitches_actual"] = float(len(df))\n'
        '        return _thin',
        '_thin["sp_n_pitches_actual"] = float(len(df))',
    ),
    # ---- point_in_time.py : pitcher_as_of normal return dict -------------
    (
        "mlb_edge/point_in_time.py",
        '        "sp_n_pitches":           len(df),\n'
        '        "sp_ttop3_penalty":       sp_ttop3_shrunk,',
        '        "sp_n_pitches":           len(df),\n'
        '        "sp_n_pitches_actual":    float(len(df)),\n'
        '        "sp_ttop3_penalty":       sp_ttop3_shrunk,',
        '"sp_n_pitches_actual":    float(len(df)),',
    ),
    # ---- build_pipeline.py : propagate telemetry columns ----------------
    (
        "mlb_edge/build_pipeline.py",
        '        "home_sp_n_pitches":     float(home_sp.get("sp_n_pitches") or 0.0),\n'
        '        "away_sp_n_pitches":     float(away_sp.get("sp_n_pitches") or 0.0),',
        '        "home_sp_n_pitches":     float(home_sp.get("sp_n_pitches") or 0.0),\n'
        '        "away_sp_n_pitches":     float(away_sp.get("sp_n_pitches") or 0.0),\n'
        '        # Telemetry-only true counts (incl. sub-100 thin arms). NOT model\n'
        '        # features / shrinkage inputs - drives the PENDING_SP_DATA display\n'
        '        # so a thin starter shows its real count instead of a misleading 0.\n'
        '        "home_sp_n_pitches_actual": float(home_sp.get("sp_n_pitches_actual") or 0.0),\n'
        '        "away_sp_n_pitches_actual": float(away_sp.get("sp_n_pitches_actual") or 0.0),',
        '"home_sp_n_pitches_actual": float(',
    ),
    # ---- main_predict.py : read the actual counts -----------------------
    (
        "mlb_edge/main_predict.py",
        '        h_n = r.get("home_sp_n_pitches", float("nan"))\n'
        '        a_n = r.get("away_sp_n_pitches", float("nan"))\n'
        '        h_name = (r.get("home_sp_name") or "").strip()',
        '        h_n = r.get("home_sp_n_pitches", float("nan"))\n'
        '        a_n = r.get("away_sp_n_pitches", float("nan"))\n'
        '        # Display the TRUE pitch count (home_sp_n_pitches is NaN\'d below 100\n'
        '        # by pitcher_as_of; the actual count rides in *_actual) so a thin arm\n'
        '        # shows "85", not "0". The gate decision below still keys off h_n/a_n.\n'
        '        h_n_act = r.get("home_sp_n_pitches_actual", h_n)\n'
        '        a_n_act = r.get("away_sp_n_pitches_actual", a_n)\n'
        '        h_name = (r.get("home_sp_name") or "").strip()',
        'h_n_act = r.get("home_sp_n_pitches_actual", h_n)',
    ),
    # ---- main_predict.py : home n_disp uses actual ----------------------
    (
        "mlb_edge/main_predict.py",
        '            n_disp = "0" if pd.isna(h_n) else str(int(h_n))',
        '            n_disp = "0" if pd.isna(h_n_act) else str(int(h_n_act))',
        'str(int(h_n_act))',
    ),
    # ---- main_predict.py : away n_disp uses actual ----------------------
    (
        "mlb_edge/main_predict.py",
        '            n_disp = "0" if pd.isna(a_n) else str(int(a_n))',
        '            n_disp = "0" if pd.isna(a_n_act) else str(int(a_n_act))',
        'str(int(a_n_act))',
    ),
]


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def main():
    applied, skipped = 0, 0
    touched = set()
    for path, old, new, marker in EDITS:
        raw = _read(path)
        nl = "\r\n" if "\r\n" in raw else "\n"
        work = raw.replace("\r\n", "\n")
        if marker in work:
            print(f"  skip (already applied): {path} :: {marker[:48]}")
            skipped += 1
            continue
        if old not in work:
            print(f"  ERROR anchor not found in {path}:\n    {old[:90]!r}")
            sys.exit(1)
        if work.count(old) != 1:
            print(f"  ERROR anchor not unique ({work.count(old)}x) in {path}")
            sys.exit(1)
        work = work.replace(old, new, 1)
        _write(path, work.replace("\n", nl))
        touched.add(path)
        applied += 1
        print(f"  applied: {path} :: {marker[:48]}")

    # Syntax gate every file we changed (Rule 3) ------------------------------
    for path in sorted(touched):
        ast.parse(_read(path).replace("\r\n", "\n"))
        print(f"  ast.parse OK: {path}")

    print(f"DONE  applied={applied}  skipped={skipped}")
    if applied == 0 and skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
