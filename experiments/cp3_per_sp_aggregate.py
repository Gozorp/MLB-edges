"""Phase 1, CP3 — per-SP aggregation against a real slate.

For each announced SP on the target slate:
  1. Pull their last ~60 days of pitches from `data/pitch_quality/dataset.parquet`
     (which is filtered Statcast 2022-2026 YTD).
  2. Score each pitch with both Stuff+ and Location+.
  3. Compute pitch-count-weighted per-SP means.
  4. Build a per-SP table + per-game gap features.
  5. Sniff-check: ace cluster up top, bust cluster at the bottom, slate
     spread >= 8pp top-to-bottom.

NOT wiring into build_pipeline. CP4 does that behind the USE_STUFF_PLUS flag.

Run:
    PYTHONIOENCODING=utf-8 python experiments/cp3_per_sp_aggregate.py
    PYTHONIOENCODING=utf-8 python experiments/cp3_per_sp_aggregate.py --date 2026-05-01
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_edge.config import STUFF_PLUS_CFG
from mlb_edge.pitch_quality import score_pitches
from mlb_edge.stadiums import normalize_team


# Aggregate window — same idea as `sp_xera_gap` rolling lookback in
# build_pipeline. For 2026-04-29 the available 2026 sample is ~30 days,
# so "all 2026 YTD" is itself within the 60-day window.
TARGET_LOOKBACK_DAYS = 60
MIN_SAMPLE_FOR_AGG = 200          # if 2026 is thinner, fall back to 2025


def fetch_probable_pitchers(d: date) -> List[Dict]:
    """Return a list of {game_pk, away, home, away_sp_id, home_sp_id, names}
    for completed games on `d`."""
    r = requests.get(
        "https://statsapi.mlb.com/api/v1/schedule",
        params={"sportId": 1, "date": d.isoformat(),
                "hydrate": "probablePitcher"}, timeout=20,
    )
    r.raise_for_status()
    rows = []
    for dd in r.json().get("dates", []):
        for g in dd.get("games", []):
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            home_pp = (home.get("probablePitcher") or {})
            away_pp = (away.get("probablePitcher") or {})
            rows.append({
                "game_pk": g["gamePk"],
                "away": normalize_team(away["team"]["name"]),
                "home": normalize_team(home["team"]["name"]),
                "away_sp_id": home_pp.get("id") and away_pp.get("id"),  # allow 0
                "away_sp_id": away_pp.get("id"),
                "home_sp_id": home_pp.get("id"),
                "away_sp_name": away_pp.get("fullName"),
                "home_sp_name": home_pp.get("fullName"),
            })
    return rows


def select_pitcher_window(df: pd.DataFrame, pid: int) -> pd.DataFrame:
    """Pick this SP's recent body of work. Prefer 2026 if they have enough
    sample, else supplement with most recent 2025 pitches up to a 500 cap.
    Returns the row subset to score."""
    if pid is None:
        return pd.DataFrame()
    yt = df[(df["pitcher"] == pid) & (df["game_year"] == 2026)]
    if len(yt) >= MIN_SAMPLE_FOR_AGG:
        return yt
    # Supplement from 2025. Cache doesn't carry game_date in our trimmed
    # parquet so we approximate "most recent" with the tail of the 2025
    # rows for this pitcher (chunks were stored chronologically).
    yp = df[(df["pitcher"] == pid) & (df["game_year"] == 2025)].tail(500 - len(yt))
    return pd.concat([yp, yt], ignore_index=True)


def aggregate_sp_scores(scored: pd.DataFrame) -> Tuple[float, float, int]:
    """Pitch-count-weighted means (= simple mean since each row is a
    single pitch)."""
    if scored.empty:
        return float("nan"), float("nan"), 0
    return (
        float(scored["stuff_plus"].mean()),
        float(scored["location_plus"].mean()),
        int(len(scored)),
    )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-04-29",
                    help="Slate date to score (YYYY-MM-DD)")
    args = ap.parse_args()
    target = datetime.strptime(args.date, "%Y-%m-%d").date()
    print(f"=" * 72)
    print(f"Phase 1 / CP3 — per-SP aggregation for slate {target}")
    print(f"=" * 72)

    # Load artifacts.
    models_dir = Path("models")
    stuff = joblib.load(models_dir / "stuff_plus_v1.pkl")
    loc = joblib.load(models_dir / "location_plus_v1.pkl")
    norms = json.loads((models_dir / "pitch_quality_norms_v1.json").read_text())
    print(f"  loaded models from {models_dir}")
    print(f"    Stuff+    val R^2 = {norms['stuff_plus']['r2_val']:.4f}")
    print(f"    Location+ val R^2 = {norms['location_plus']['r2_val']:.4f}")

    df = pd.read_parquet(Path(STUFF_PLUS_CFG["cache_dir"]) / "dataset.parquet")
    print(f"  loaded {len(df):,} pitches from dataset.parquet")

    games = fetch_probable_pitchers(target)
    print(f"  {len(games)} games on schedule for {target}")

    # ----------------------------------------------------------------
    # Score each unique SP exactly once.
    # ----------------------------------------------------------------
    sp_ids = set()
    for g in games:
        if g["home_sp_id"]: sp_ids.add(g["home_sp_id"])
        if g["away_sp_id"]: sp_ids.add(g["away_sp_id"])
    print(f"  {len(sp_ids)} unique SP ids to score")

    sp_scores: Dict[int, Dict] = {}
    n_in_dataset = 0
    n_fallback = 0
    n_missing = 0
    center = norms["stuff_plus"]["center"]   # fallback value for unknowns

    for pid in sp_ids:
        window = select_pitcher_window(df, pid)
        if window.empty:
            sp_scores[pid] = {
                "n": 0, "stuff_plus": center, "location_plus": center,
                "source": "league_mean (no cache match)",
            }
            n_missing += 1
            continue
        scored = score_pitches(window, stuff, loc, norms)
        s_plus, l_plus, n = aggregate_sp_scores(scored)
        # Tag whether this came from 2026 or had to dip into 2025.
        n_2026 = int((window["game_year"] == 2026).sum())
        if n_2026 >= MIN_SAMPLE_FOR_AGG:
            source = f"2026 YTD (n={n_2026})"
            n_in_dataset += 1
        else:
            n_2025 = n - n_2026
            source = f"2026 YTD (n={n_2026}) + 2025 tail (n={n_2025})"
            n_fallback += 1
        sp_scores[pid] = {
            "n": n, "stuff_plus": s_plus, "location_plus": l_plus,
            "source": source,
        }

    print(f"  scored {n_in_dataset} from 2026 YTD, "
          f"{n_fallback} via 2025 fallback, "
          f"{n_missing} fell back to league mean")

    # ----------------------------------------------------------------
    # Build per-SP table.
    # ----------------------------------------------------------------
    rows = []
    for g in games:
        for side, sp_id, name in [
            ("home", g["home_sp_id"], g["home_sp_name"]),
            ("away", g["away_sp_id"], g["away_sp_name"]),
        ]:
            if not sp_id:
                rows.append({
                    "game_pk": g["game_pk"],
                    "matchup": f"{g['away']} @ {g['home']}",
                    "side": side, "sp_name": "TBD", "sp_id": None,
                    "n_pitches": 0, "stuff_plus": np.nan,
                    "location_plus": np.nan, "pitching_plus_06_04": np.nan,
                    "source": "TBA",
                })
                continue
            s = sp_scores.get(sp_id, {"n": 0, "stuff_plus": np.nan,
                                       "location_plus": np.nan, "source": "?"})
            sp = s["stuff_plus"]; lp = s["location_plus"]
            rows.append({
                "game_pk": g["game_pk"],
                "matchup": f"{g['away']} @ {g['home']}",
                "side": side, "sp_name": name, "sp_id": sp_id,
                "n_pitches": s["n"],
                "stuff_plus": sp, "location_plus": lp,
                # Reference 0.6/0.4 blend (NOT a feature, just a sanity column).
                "pitching_plus_06_04": (0.6 * sp + 0.4 * lp
                                         if pd.notna(sp) and pd.notna(lp)
                                         else np.nan),
                "source": s["source"],
            })
    tbl = pd.DataFrame(rows)

    # Per-SP one-row view sorted by Stuff+ for the slate ranking.
    sp_view = tbl.dropna(subset=["sp_id"]).drop_duplicates("sp_id").copy()
    sp_view = sp_view.sort_values("stuff_plus", ascending=False)

    print("\n" + "=" * 72)
    print("PER-SP TABLE (slate, sorted by Stuff+)")
    print("=" * 72)
    print(f"{'rank':>4} {'SP':<28} {'team':<6} {'n_pitches':>10} "
          f"{'Stuff+':>8} {'Location+':>10} {'P+(0.6/0.4)':>12} {'source':<35}")
    for i, r in enumerate(sp_view.itertuples(), 1):
        team = r.matchup.split(" @ ")[1] if r.side == "home" else r.matchup.split(" @ ")[0]
        sp_name = (r.sp_name or "?")[:28]
        sp_str = f"{r.stuff_plus:.2f}" if pd.notna(r.stuff_plus) else "—"
        lp_str = f"{r.location_plus:.2f}" if pd.notna(r.location_plus) else "—"
        pp_str = f"{r.pitching_plus_06_04:.2f}" if pd.notna(r.pitching_plus_06_04) else "—"
        print(f"{i:>4} {sp_name:<28} {team:<6} {r.n_pitches:>10} "
              f"{sp_str:>8} {lp_str:>10} {pp_str:>12} {r.source[:35]:<35}")

    # ----------------------------------------------------------------
    # Spread / sniff
    # ----------------------------------------------------------------
    valid = sp_view.dropna(subset=["stuff_plus"])
    if not valid.empty:
        sp_max, sp_min = valid["stuff_plus"].max(), valid["stuff_plus"].min()
        lp_max, lp_min = valid["location_plus"].max(), valid["location_plus"].min()
        print(f"\n  Stuff+    spread: {sp_min:.2f} → {sp_max:.2f}  "
              f"(Δ = {sp_max - sp_min:.2f}pp)")
        print(f"  Location+ spread: {lp_min:.2f} → {lp_max:.2f}  "
              f"(Δ = {lp_max - lp_min:.2f}pp)")
        spread_pass = (sp_max - sp_min) >= 8.0
        print(f"  Spread sniff (≥8pp) : {'PASS' if spread_pass else 'BORDERLINE'}")

    # ----------------------------------------------------------------
    # Per-game gap features (home - away).
    # ----------------------------------------------------------------
    print("\n" + "=" * 72)
    print("GAP FEATURES (home_sp_score − away_sp_score)")
    print("=" * 72)
    pivot = tbl.pivot_table(
        index=["game_pk", "matchup"], columns="side",
        values=["stuff_plus", "location_plus", "sp_name"],
        aggfunc="first",
    ).reset_index()
    pivot.columns = ["game_pk", "matchup", "loc_away", "loc_home",
                     "name_away", "name_home", "stuff_away", "stuff_home"]
    pivot["sp_stuff_plus_gap"] = pivot["stuff_home"] - pivot["stuff_away"]
    pivot["sp_location_plus_gap"] = pivot["loc_home"] - pivot["loc_away"]

    print(f"{'matchup':<14} {'home SP':<22} {'away SP':<22} "
          f"{'stuff_gap':>10} {'loc_gap':>9}")
    for _, r in pivot.sort_values("sp_stuff_plus_gap", ascending=False).iterrows():
        sg = r["sp_stuff_plus_gap"]
        lg = r["sp_location_plus_gap"]
        sg_s = f"{sg:+.2f}" if pd.notna(sg) else "—"
        lg_s = f"{lg:+.2f}" if pd.notna(lg) else "—"
        print(f"{r['matchup']:<14} {(r['name_home'] or '?')[:22]:<22} "
              f"{(r['name_away'] or '?')[:22]:<22} {sg_s:>10} {lg_s:>9}")

    # ----------------------------------------------------------------
    # Sniff: where do the headline SPs land in this slate?
    # ----------------------------------------------------------------
    print("\n" + "=" * 72)
    print("HEADLINE SP CHECK (slate-level rank)")
    print("=" * 72)
    targets = ["Glasnow", "deGrom", "Skubal", "Wheeler", "Sale", "Skenes",
               "Bello", "Bassitt", "Lauer", "Eovaldi", "Ohtani"]
    for q in targets:
        hits = sp_view[sp_view["sp_name"].fillna("").str.contains(q, case=False)]
        if hits.empty:
            print(f"  {q}: not on this slate")
            continue
        for _, r in hits.iterrows():
            rank = (sp_view["stuff_plus"] >= r["stuff_plus"]).sum()
            print(f"  {r['sp_name']:<24} rank {rank:>2}/{len(sp_view)}  "
                  f"Stuff+={r['stuff_plus']:.2f}  "
                  f"Location+={r['location_plus']:.2f}  "
                  f"n={r['n_pitches']}")

    # Persist per-SP table for downstream review.
    out_csv = Path(STUFF_PLUS_CFG["cache_dir"]) / f"slate_{target.isoformat()}_per_sp.csv"
    tbl.to_csv(out_csv, index=False)
    print(f"\nSaved per-SP table to {out_csv}")
    print("CP3 complete. Holding for review.")


if __name__ == "__main__":
    main()
