#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/fetch_market_totals.py
============================
FREE, KEYLESS daily market-totals scrape -> docs/data/market_totals_<date>.csv.
Tries, in order, sources that need NO API key, NO signup, NO cost:
  1. DraftKings public JSON  (mlb_edge.draftkings_totals.fetch_dk_totals) -- returns
     totals directly; DK sits behind Akamai so works best from a residential IP.
  2. ESPN public lines page  (mlb_edge.odds_fallback.fetch_espn_mlb_totals).
If BOTH come back empty, prints the honest fallback (a free-tier API key) instead
of writing a silent empty file.

Usage:  python tools\\fetch_market_totals.py [YYYY-MM-DD]
        (date arg only used by the ESPN fallback; DK returns its whole upcoming slate)
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _try_dk():
    try:
        from mlb_edge.draftkings_totals import fetch_dk_totals
        df = fetch_dk_totals()
        if df is not None and not df.empty:
            return df, "draftkings_free"
    except Exception as e:
        print("  DK fetch error: %r" % (e,))
    return None, None


def _try_espn(d):
    try:
        from mlb_edge.odds_fallback import fetch_espn_mlb_totals
        df = fetch_espn_mlb_totals(d)
        if df is not None and not df.empty:
            return df, "espn_free"
    except Exception as e:
        print("  ESPN fetch error: %r" % (e,))
    return None, None


def main():
    d = (datetime.date.fromisoformat(sys.argv[1])
         if len(sys.argv) > 1 else datetime.date.today())
    print("Trying FREE keyless sources (DraftKings -> ESPN)...")
    df, src = _try_dk()
    if df is None:
        print("  DraftKings empty/blocked -> trying ESPN lines page for %s..." % d.isoformat())
        df, src = _try_espn(d)

    if df is None or df.empty:
        print("\n  Both free keyless sources returned nothing.")
        print("  -> ESPN appears JS-gated/pulled; DK is Akamai-blocked or its IDs rotated.")
        print("  -> The reliable $0 path is a FREE-TIER API KEY (no payment, no card):")
        print("       the-odds-api 'Starter' = 500 calls/month, free, email signup only.")
        print("     Tell me and I'll wire that adapter in (still costs you nothing).")
        return

    os.makedirs("docs/data", exist_ok=True)
    df = df.copy()
    df["game_date"] = df["game_date"].astype(str)
    df["source"] = src
    df["fetched_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    want = ["game_date", "away_team", "home_team", "total_line",
            "over_decimal", "under_decimal", "source", "fetched_at"]
    cols = [c for c in want if c in df.columns]
    total_games = 0
    for gd, sub in df.groupby("game_date"):
        out = "docs/data/market_totals_%s.csv" % gd
        sub[cols].to_csv(out + ".tmp", index=False)
        os.replace(out + ".tmp", out)  # atomic
        # CLV snapshot log (blueprint 2026-07-18 step 5): the per-date file
        # above keeps only the LATEST fetch; this append-only log keeps every
        # intraday snapshot so open-vs-close line movement is measurable.
        logp = "docs/data/market_totals_log_%s.csv" % gd
        sub[cols].to_csv(logp, mode="a", index=False,
                         header=not os.path.exists(logp))
        total_games += len(sub)
        print("  wrote %d games -> %s  [%s]" % (len(sub), out, src))
        for _, r in sub.iterrows():
            print("     %s @ %s  total=%s" % (r.get("away_team"), r.get("home_team"), r.get("total_line")))
    print("\nDONE: %d games from %s. Free keyless market feed is LIVE." % (total_games, src))


if __name__ == "__main__":
    main()
