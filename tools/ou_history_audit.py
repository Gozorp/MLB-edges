#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ou_history_audit.py -- O/U ledger consolidation + market validation.

Blueprint 2026-07-18, steps 4+5, mapped onto this repo's real files:
  * build_dynamic_history_matrix(days_back): memory-only lazy merge of the
    split per-day ledgers (picks_totals_<date>.csv) joined with verified
    final totals, intraday market snapshots (market_totals_log_<date>.csv,
    written by fetch_market_totals since 2026-07-18) and venue/temperature
    (player_vectors_<date>.json). Nothing on disk is altered.
  * verify_clv_performance: did the closing total move TOWARD the model's
    side after the signal? (CLV_under = open > close.)
  * audit_environmental_biases: mean projection residual by ballpark and
    the >=90F extreme-heat bucket.

Usage: python tools/ou_history_audit.py [days_back]      (default 30)
Read-only; prints a console report.
"""
import csv
import datetime
import glob
import json
import os
import re
import sys

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
S = lambda x: (x if isinstance(x, str) else "") if x is not None else ""
HEAT_F = 90
csv.field_size_limit(10 ** 7)


def _truth():
    truth = {}
    for f in glob.glob("docs/data/postgame/*.json"):
        try: d = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        date = S(d.get("date"))
        for m, i in (d.get("by_matchup", {}) or {}).items():
            if not isinstance(i, dict) or "(G" in S(m):
                continue
            sc = S(i.get("final_score")).strip()
            mm = re.match(r"\s*([A-Za-z0-9]+)\s*@\s*([A-Za-z0-9]+)", S(m))
            if mm and re.match(r"^\d+-\d+$", sc):
                a, b = map(int, sc.split("-"))
                truth[(date, mm.group(1), mm.group(2))] = a + b
    for f in glob.glob("docs/data/_results_*.json"):
        try: d = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        date = S(d.get("date"))
        for g in d.get("games", []):
            if g.get("status") == "Final":
                try:
                    truth[(date, S(g["away"]).strip(), S(g["home"]).strip())] = \
                        int(g["away_runs"]) + int(g["home_runs"])
                except Exception: pass
    return truth


def _market_snapshots(date):
    """(away, home) -> (open_total, close_total) from the intraday log."""
    out = {}
    p = "docs/data/market_totals_log_%s.csv" % date
    if not os.path.exists(p):
        return out
    rows = sorted((r for r in csv.DictReader(open(p, encoding="utf-8"))
                   if S(r.get("total_line"))),
                  key=lambda r: S(r.get("fetched_at")))
    for r in rows:
        key = (S(r.get("away_team")).strip(), S(r.get("home_team")).strip())
        try:
            line = float(r["total_line"])
        except ValueError:
            continue
        if key not in out:
            out[key] = [line, line]
        else:
            out[key][1] = line
    return {k: tuple(v) for k, v in out.items()}


def _env(date):
    """(away, home) -> (venue, temp) from the player-vectors matchup shells."""
    out = {}
    p = "docs/data/player_vectors_%s.json" % date
    try:
        d = json.load(open(p, encoding="utf-8"))
        for m in d.get("matchups", []):
            out[(S(m.get("away_team")), S(m.get("home_team")))] = \
                (m.get("venue"), (m.get("weather") or {}).get("temp"))
    except Exception:
        pass
    return out


def build_dynamic_history_matrix(days_back=30):
    """Blueprint step 4: lazy in-memory merge; production files untouched."""
    import pandas as pd
    truth = _truth()
    frames = []
    today = datetime.date.today()
    for i in range(days_back):
        date = (today - datetime.timedelta(days=i)).isoformat()
        p = "picks_totals_%s.csv" % date
        if not os.path.exists(p):
            p = "docs/data/picks_totals_%s.csv" % date
            if not os.path.exists(p):
                continue
        snaps, env = _market_snapshots(date), _env(date)
        seen = set()
        for r in csv.DictReader(open(p, encoding="utf-8")):
            key = (S(r.get("away_team")).strip(), S(r.get("home_team")).strip())
            tkey = (date,) + key
            if tkey in seen or tkey not in truth:
                continue
            seen.add(tkey)
            try:
                proj = float(S(r.get("pred_runs_cal")) or S(r.get("pred_runs")) or "nan")
            except ValueError:
                continue
            oc = snaps.get(key, (None, None))
            venue, temp = env.get(key, (None, None))
            frames.append({
                "game_date": date, "away": key[0], "home": key[1],
                "model_proj_total": proj,
                "model_action": S(r.get("side")).upper() or None,
                "market_open_total": oc[0], "market_close_total": oc[1],
                "actual_total_runs": truth[tkey],
                "stadium_name": venue, "game_temp": temp,
            })
    if not frames:
        print("! no historical ledger rows in the %d-day window" % days_back)
        return pd.DataFrame()
    return pd.DataFrame(frames)


def verify_clv_performance(master_df):
    """Blueprint step 5.1: did closing lines move toward the model's side?"""
    ok = master_df.dropna(subset=["market_open_total", "market_close_total"])
    ok = ok[ok["market_open_total"] != ok["market_close_total"]]
    if ok.empty:
        print("CLV: no games with intraday line movement captured yet — the "
              "snapshot log (market_totals_log_*.csv) starts 2026-07-18; "
              "re-run once a few slates of open/close pairs accumulate.")
        return
    for side, cmp_ in (("UNDER", lambda d: d["market_open_total"] > d["market_close_total"]),
                       ("OVER", lambda d: d["market_open_total"] < d["market_close_total"])):
        b = ok[ok["model_action"] == side]
        if len(b) < 5:
            print("CLV %s: n=%d (too thin)" % (side, len(b)))
            continue
        rate = cmp_(b).mean() * 100
        print("CLV %s: %.1f%% of %d signals beat the closing move" % (side, rate, len(b)))


def audit_environmental_biases(master_df):
    """Blueprint step 5.2: residual by ballpark + extreme-heat bucket."""
    df = master_df.copy()
    df["error_residual"] = df["actual_total_runs"] - df["model_proj_total"]
    print("\nMean projection residual (actual - calibrated proj): %+.3f over %d games"
          % (df["error_residual"].mean(), len(df)))
    by_park = df.dropna(subset=["stadium_name"]) \
                .groupby("stadium_name")["error_residual"].agg(["mean", "count"])
    if len(by_park):
        by_park = by_park[by_park["count"] >= 3].sort_values("mean")
        print("\nBallpark residuals (n>=3):")
        for name, row in by_park.iterrows():
            print("  %-28s %+0.2f runs (n=%d)" % (name, row["mean"], int(row["count"])))
    else:
        print("(no venue data joined yet — player_vectors sidecar starts 2026-07-18)")
    heat = df[df["game_temp"].notna() & (df["game_temp"] >= HEAT_F)]
    if len(heat) >= 5:
        print("\nExtreme heat (>=%dF) residual: %+.3f runs (n=%d)"
              % (HEAT_F, heat["error_residual"].mean(), len(heat)))
        if heat["error_residual"].mean() > 0.50:
            print("  ACTION: heat bias exceeds +0.50 runs — consider scaling the "
                  "air-density adjustment up ~5%.")
    else:
        print("\nExtreme heat bucket: n=%d (needs temperature capture to accumulate)"
              % len(heat))


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    df = build_dynamic_history_matrix(days)
    if df.empty:
        return
    print("history matrix: %d graded totals rows over %d days\n"
          % (len(df), df["game_date"].nunique()))
    verify_clv_performance(df)
    audit_environmental_biases(df)


if __name__ == "__main__":
    main()
