"""Step-8 debug + freshness sanity checks for 2026-04-26."""
import os
import sys
import glob
from datetime import date, datetime
from pathlib import Path

import joblib
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from mlb_edge.build_pipeline import build_slate_frame
from mlb_edge.model import predict as mlb_predict

day = date(2026, 4, 26)

# ---- Build slate with feature columns we need ---------------------------
print("Building 2026-04-26 slate ...")
games = build_slate_frame(day, include_weather=True)
print(f"Built {len(games)} games")

models = joblib.load("models/latest.pkl")
games = mlb_predict(models["stage1"], models["stage2"], games)

# Pull fields per game
needed = [
    "home_team", "away_team", "model_prob",
    "home_sp_n_pitches", "away_sp_n_pitches",
    "home_bullpen_n_pitches", "away_bullpen_n_pitches",
]
for c in needed:
    if c not in games.columns:
        games[c] = pd.NA

# We need conviction tier per game — re-derive via score_conviction (same logic as audit).
from mlb_edge.edge_calculator import score_conviction
tiers = []
signals_lst = []
notes_lst = []
for _, g in games.iterrows():
    p_home = g["model_prob"]
    perspective = g.copy()
    if p_home < 0.5:
        for col in [
            "sp_xera_gap", "team_woba_gap", "sp_k_bb_pct_gap",
            "sp_siera_gap", "sp_fip_gap",
            "bullpen_siera_gap", "bullpen_xwoba_gap",
            "bullpen_k_pct_gap", "bullpen_bb_pct_gap",
            "bullpen_hardhit_gap", "bullpen_fatigue_gap",
        ]:
            if col in perspective:
                perspective[col] = -perspective[col]
        perspective["home_sp_luck"], perspective["away_sp_luck"] = (
            perspective.get("away_sp_luck"), perspective.get("home_sp_luck")
        )
        perspective["home_sp_n_pitches"], perspective["away_sp_n_pitches"] = (
            perspective.get("away_sp_n_pitches"), perspective.get("home_sp_n_pitches")
        )
        perspective["home_bullpen_n_pitches"], perspective["away_bullpen_n_pitches"] = (
            perspective.get("away_bullpen_n_pitches"), perspective.get("home_bullpen_n_pitches")
        )
    conv = score_conviction(perspective)
    tiers.append(conv.tier)
    signals_lst.append(", ".join(conv.signals_fired))
    notes_lst.append(" | ".join(conv.notes))

games = games.assign(_tier=tiers, _signals=signals_lst, _notes=notes_lst)

flags = []  # list of (rule_id, message)

# ---- Rule 1: PLATINUM/DIAMOND with sp_n_pitches < 600 -------------------
high = games[games["_tier"].isin(["PLATINUM", "DIAMOND"])]
for _, g in high.iterrows():
    h_n = g.get("home_sp_n_pitches")
    a_n = g.get("away_sp_n_pitches")
    if pd.notna(h_n) and h_n < 600:
        flags.append(("RULE-1", f"{g['away_team']}@{g['home_team']} ({g['_tier']}): home_sp_n_pitches={h_n:.0f} < 600 (small-sample F1 leak)"))
    if pd.notna(a_n) and a_n < 600:
        flags.append(("RULE-1", f"{g['away_team']}@{g['home_team']} ({g['_tier']}): away_sp_n_pitches={a_n:.0f} < 600 (small-sample F1 leak)"))

# ---- Rule 2: PLATINUM/DIAMOND with bullpen_n_pitches < 3000 -------------
for _, g in high.iterrows():
    h_b = g.get("home_bullpen_n_pitches")
    a_b = g.get("away_bullpen_n_pitches")
    if pd.notna(h_b) and h_b < 3000:
        flags.append(("RULE-2", f"{g['away_team']}@{g['home_team']} ({g['_tier']}): home_bullpen_n_pitches={h_b:.0f} < 3000 (small-sample F5 leak — v11 should prevent)"))
    if pd.notna(a_b) and a_b < 3000:
        flags.append(("RULE-2", f"{g['away_team']}@{g['home_team']} ({g['_tier']}): away_bullpen_n_pitches={a_b:.0f} < 3000 (small-sample F5 leak — v11 should prevent)"))

# ---- Rule 3: PLATINUM with model_prob in [0.495, 0.505] -----------------
for _, g in games[games["_tier"] == "PLATINUM"].iterrows():
    if 0.495 <= g["model_prob"] <= 0.505:
        flags.append(("RULE-3", f"{g['away_team']}@{g['home_team']} PLATINUM with model_prob={g['model_prob']:.3f} (coin-flip overconfidence)"))

# ---- Rule 4: F1 / F5 negative-veto fires on 0 games when >=5 credible ----
n_credible_f1 = ((games["home_sp_n_pitches"].fillna(0) >= 800) &
                 (games["away_sp_n_pitches"].fillna(0) >= 800)).sum()
n_credible_f5 = ((games["home_bullpen_n_pitches"].fillna(0) >= 3000) &
                 (games["away_bullpen_n_pitches"].fillna(0) >= 3000)).sum()
n_f1_fired = games["_notes"].str.contains("F1 negative", na=False).sum()
n_f5_fired = games["_notes"].str.contains("F5 bullpen veto", na=False).sum()

print(f"\nF1 credible-sample games: {n_credible_f1}; F1 negative-veto fires: {n_f1_fired}")
print(f"F5 credible-sample games: {n_credible_f5}; F5 bullpen-veto fires: {n_f5_fired}")
if n_credible_f1 >= 5 and n_f1_fired == 0:
    flags.append(("RULE-4", f"F1 negative-veto fired on 0 games but {n_credible_f1} have credible SP samples — guards may be wired wrong"))
if n_credible_f5 >= 5 and n_f5_fired == 0:
    flags.append(("RULE-4", f"F5 bullpen-veto fired on 0 games but {n_credible_f5} have credible bullpen samples — guards may be wired wrong"))

# ---- Rule 5: predict log -----------------------------------------------
log_path = "predict_2026-04-26.log"
if os.path.exists(log_path):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        log = f.read()
    if "Traceback" in log or "ERROR" in log or "Error" in log:
        # filter out spurious "errors"
        bad_lines = [ln for ln in log.splitlines()
                     if ("Traceback" in ln or " ERROR " in ln or " Error " in ln)
                     and "0 errors" not in ln]
        if bad_lines:
            flags.append(("RULE-5", f"predict log contains errors: {len(bad_lines)} lines (first: {bad_lines[0][:200]})"))

# ---- Rule 6: data freshness --------------------------------------------
print("\n=== DATA FRESHNESS ===")
threshold = datetime(2026, 4, 25, 12, 0).timestamp()
stale = []

# Savant CSVs (must have today's mtime, i.e. mtime >= today midnight)
today_mid = datetime(2026, 4, 26, 0, 0).timestamp()
savant_dirs = sorted(glob.glob("data/savant/*"))
n_savant = 0
n_savant_today = 0
for d in savant_dirs:
    if not os.path.isdir(d):
        continue
    csvs = sorted(glob.glob(os.path.join(d, "*.csv")))
    if not csvs:
        continue
    n_savant += 1
    latest = max(csvs, key=os.path.getmtime)
    mt = os.path.getmtime(latest)
    if mt >= today_mid:
        n_savant_today += 1
    else:
        stale.append(("savant", latest, datetime.fromtimestamp(mt).isoformat()))
print(f"Savant: {n_savant_today}/{n_savant} categories have a CSV with today's mtime")

# B-R boxes for 2026-04-25 (must exist)
boxes_25 = sorted(glob.glob("data/bref/boxes/bref_boxscore_*20260425*.json"))
print(f"B-R boxes for 2026-04-25: {len(boxes_25)} found")
if not boxes_25:
    flags.append(("RULE-6", "No B-R boxes for 2026-04-25 (Chrome scrape skipped?)"))
# B-R index for 2026-04-25
idx_25 = sorted(glob.glob("data/bref/indexes/bref_index_20260425*.json"))
print(f"B-R index for 2026-04-25: {len(idx_25)} found")

# Standings 20260426_upto-*.csv (must exist)
stand = sorted(glob.glob("data/bref/standings/20260426_upto-*.csv"))
print(f"B-R standings 20260426_upto-*: {len(stand)} files found")
if not stand:
    flags.append(("RULE-6", "No 20260426_upto-* standings files — bref_fetch.fetch_standings did not run"))

# Bat-tracking
bat = sorted(glob.glob("data/savant_bat_tracking/*.csv"))
if bat:
    latest_bat = max(bat, key=os.path.getmtime)
    print(f"Latest bat_tracking: {os.path.basename(latest_bat)}  mtime={datetime.fromtimestamp(os.path.getmtime(latest_bat))}")

# Stale list
print("\nFiles with mtime < 2026-04-25 12:00:")
n_stale = 0
for kind, path, ts in stale:
    if datetime.fromisoformat(ts).timestamp() < threshold:
        print(f"  STALE [{kind}] {path}  mtime={ts}")
        n_stale += 1
        flags.append(("RULE-6", f"Stale {kind} file: {path} mtime={ts}"))
if n_stale == 0:
    print("  (none)")

# ---- Summary ----
print("\n========== RED FLAGS ==========")
if flags:
    for rule, msg in flags:
        print(f"  {rule}: {msg}")
    print(f"\nTotal flags: {len(flags)}")
else:
    print("  (none)")
