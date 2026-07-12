#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kprop_odds.py -- pitcher-strikeout prop ingestion (the-odds-api) + edge calc.

Joe's step 4-5 prerequisites, built so the pipeline is key-ready:
  * pulls pitcher_strikeouts prop lines + prices per MLB event
  * devigs each Over/Under pair -> implied market probability
  * projects the model-side P(Over) from the SP's rolling K% x projected
    batters faced (Poisson tail) -- an explicitly-labeled HEURISTIC
  * edge_pp = (model P(Over) - devigged market P(Over)) * 100
  * writes docs/data/kprops_<date>.json for the combo engine's leg pool

SHADOW STATUS: these legs are marked shadow_validation. Per the same locked
discipline as the totals rebuild, projection-vs-market edges do not become
bettable legs until a pre-registered OOS validation passes. This tool creates
the data needed to run exactly that validation.

Key + quota:
  * ODDS_API_KEY read from env or repo-root .env (never hardcode/commit).
  * NO KEY -> writes a status file and exits 0 (chain-safe no-op).
  * Quota guard: stops fetching when x-requests-remaining < 60. Free tier is
    500 credits/month; ~15 event calls/day for props alone is ~450/month,
    which CANNOT coexist with the totals plan's ~480/month on one free key.
  * 20h same-day cache: safe to call from the hourly job; it fetches at most
    once per day.

Usage: python tools/kprop_odds.py [YYYY-MM-DD] | --selftest
"""
import datetime
import io
import json
import math
import os
import sys
import urllib.request

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
API = "https://api.the-odds-api.com/v4"
SPORT = "baseball_mlb"
QUOTA_FLOOR = 60
CACHE_HOURS = 20
TBF_PER_IP = 4.2          # ~batters faced per inning for a starter
DEFAULT_IP = 5.4          # league-ish SP innings when no projection exists


def _read_key():
    k = os.environ.get("ODDS_API_KEY")
    if k:
        return k.strip()
    env = os.path.join(ROOT, ".env")
    if os.path.exists(env):
        for line in io.open(env, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line.startswith("ODDS_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def american_to_prob(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o < 0:
        return -o / (-o + 100.0)
    return 100.0 / (o + 100.0)


def devig_pair(p_over_raw, p_under_raw):
    if not p_over_raw or not p_under_raw:
        return None
    s = p_over_raw + p_under_raw
    return p_over_raw / s if s > 0 else None


def poisson_p_over(k_pct, line, ip=DEFAULT_IP):
    """P(SP strikeouts > line) with K ~ Poisson(lambda = K% x TBF).
    k_pct on 0-100 scale (diag convention). Heuristic projection, not the
    frozen model; labeled as such everywhere it surfaces."""
    if k_pct is None or line is None:
        return None
    lam = (float(k_pct) / 100.0) * (ip * TBF_PER_IP)
    need = int(math.floor(float(line))) + 1     # over 6.5 -> >= 7
    # P(X >= need) = 1 - CDF(need-1)
    p = 0.0
    term = math.exp(-lam)
    total = term
    for k in range(1, need):
        term *= lam / k
        total += term
    return max(0.0, min(1.0, 1.0 - total))


def _sp_kpct_map(date):
    """SP name -> rolling K% from today's diag (0-100 scale)."""
    import csv as _csv
    path = os.path.join(ROOT, "picks_%s_diag.csv" % date)
    if not os.path.exists(path):
        path = os.path.join(ROOT, "docs", "data", "picks_%s_diag.csv" % date)
    out = {}
    if not os.path.exists(path):
        return out
    _csv.field_size_limit(10 ** 7)
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for r in _csv.DictReader(f):
            for side in ("home", "away"):
                nm = (r.get("%s_sp_name" % side) or "").strip()
                kp = r.get("%s_sp_k_pct" % side)
                try:
                    if nm and kp not in (None, ""):
                        out[nm] = float(kp)
                except ValueError:
                    pass
    return out


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "mlb_edge-kprops/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        remaining = resp.headers.get("x-requests-remaining")
        return json.loads(resp.read().decode("utf-8")), \
            float(remaining) if remaining else None


def _out_path(date):
    return os.path.join(ROOT, "docs", "data", "kprops_%s.json" % date)


def _write(date, obj):
    p = _out_path(date)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, p)
    return p


def build(date):
    now = datetime.datetime.now(datetime.timezone.utc)
    # 20h cache: at most one fetch per day even if the hourly job calls us
    prev = None
    if os.path.exists(_out_path(date)):
        try:
            prev = json.load(io.open(_out_path(date), encoding="utf-8"))
        except Exception:
            prev = None
    if prev and prev.get("status") == "ok" and prev.get("fetched_utc"):
        try:
            age = (now - datetime.datetime.strptime(
                prev["fetched_utc"], "%Y-%m-%dT%H:%M:%SZ")
                .replace(tzinfo=datetime.timezone.utc)).total_seconds() / 3600.0
            if age < CACHE_HOURS:
                print("[kprops] cache fresh (%.1fh) -- no fetch" % age)
                return prev
        except Exception:
            pass
    key = _read_key()
    if not key:
        obj = {"date": date, "status": "no_key",
               "status_note": "awaiting ODDS_API_KEY in .env -- adapter ready "
                              "(tools/kprop_odds.py); free-tier quota math in "
                              "the tool docstring",
               "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "legs": []}
        _write(date, obj)
        print("[kprops] no ODDS_API_KEY -- wrote status file (chain-safe no-op)")
        return obj
    kmap = _sp_kpct_map(date)
    legs, used_events, remaining = [], 0, None
    try:
        events, remaining = _get("%s/sports/%s/events?apiKey=%s&date=%s"
                                 % (API, SPORT, key, date))
    except Exception as e:
        note = str(e)[:200]
        if "401" in note:
            note = ("ODDS_API_KEY present but REJECTED (401) -- the lapsed "
                    "pre-05/21 key is still in .env; replace it with a fresh "
                    "the-odds-api key to light up K-prop shadow legs")
        obj = {"date": date, "status": "error", "status_note": note,
               "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "legs": []}
        _write(date, obj)
        print("[kprops] events fetch failed: %s" % e)
        return obj
    for ev in events or []:
        if remaining is not None and remaining < QUOTA_FLOOR:
            print("[kprops] quota floor reached (%.0f left) -- stopping" % remaining)
            break
        try:
            odds, remaining = _get(
                "%s/sports/%s/events/%s/odds?apiKey=%s&regions=us"
                "&markets=pitcher_strikeouts&oddsFormat=american"
                % (API, SPORT, ev.get("id"), key))
            used_events += 1
        except Exception:
            continue
        for bk in (odds or {}).get("bookmakers", []):
            for mkt in bk.get("markets", []):
                if mkt.get("key") != "pitcher_strikeouts":
                    continue
                by_player = {}
                for o in mkt.get("outcomes", []):
                    nm = o.get("description") or o.get("player") or ""
                    by_player.setdefault(nm, {})[str(o.get("name", "")).lower()] = o
                for nm, pair in by_player.items():
                    over, under = pair.get("over"), pair.get("under")
                    if not over:
                        continue
                    line = over.get("point")
                    p_mkt = devig_pair(american_to_prob(over.get("price")),
                                       american_to_prob((under or {}).get("price")))
                    if p_mkt is None:
                        continue
                    p_model = poisson_p_over(kmap.get(nm), line)
                    leg = {"sel": "%s over %s Ks" % (nm, line), "player": nm,
                           "line": line, "book": bk.get("key"),
                           "market_prob": round(p_mkt, 4),
                           "model_prob": round(p_model, 4) if p_model is not None else None,
                           "edge_pp": round((p_model - p_mkt) * 100, 2)
                                      if p_model is not None else None,
                           "model_basis": "Poisson(K%% x TBF) heuristic"
                                          if p_model is not None
                                          else "no K%% for this SP in diag"}
                    legs.append(leg)
                break  # first book with the market is enough per bookmaker loop
    obj = {"date": date, "status": "ok",
           "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "fetched_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "events_fetched": used_events, "quota_remaining": remaining,
           "legs": legs,
           "shadow_note": "projection-vs-market edges; SHADOW until the "
                          "pre-registered OOS validation passes"}
    _write(date, obj)
    print("[kprops] %s: %d legs from %d events (quota left: %s)"
          % (date, len(legs), used_events, remaining))
    return obj


def selftest():
    # devig: fair coin at -110/-110
    p = devig_pair(american_to_prob(-110), american_to_prob(-110))
    assert abs(p - 0.5) < 1e-9
    # heavier over price -> devigged over prob > 0.5
    p2 = devig_pair(american_to_prob(-150), american_to_prob(+120))
    assert p2 > 0.5
    # poisson tail: better K% -> higher P(over); line up -> P down
    a = poisson_p_over(28.0, 5.5)
    b = poisson_p_over(20.0, 5.5)
    c = poisson_p_over(28.0, 7.5)
    assert a > b and a > c and 0 < a < 1
    # ~league SP (22%, 5.4 IP -> lam ~5.0) over 5.5 should be near a coin flip
    mid = poisson_p_over(22.0, 5.5)
    assert 0.30 < mid < 0.60
    # no-key path writes a chain-safe status file
    import tempfile, shutil
    global ROOT
    old_root, old_env = ROOT, os.environ.pop("ODDS_API_KEY", None)
    tmp = tempfile.mkdtemp(prefix="kprops_selftest_")
    try:
        ROOT = tmp
        obj = build("2026-07-12")
        assert obj["status"] == "no_key"
        assert os.path.exists(os.path.join(tmp, "docs", "data",
                                           "kprops_2026-07-12.json"))
    finally:
        ROOT = old_root
        if old_env:
            os.environ["ODDS_API_KEY"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST PASS -- devig, poisson tail monotonic + sane midpoint, "
          "no-key chain-safe no-op")
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    date = None
    for a in sys.argv[1:]:
        if len(a) == 10 and a[4] == "-":
            date = a
    date = date or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    build(date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
