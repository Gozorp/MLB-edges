# -*- coding: utf-8 -*-
"""
skip_shadow_audit.py -- READ-ONLY shadow-audit ledger for the SKIP layer.

FREEZE-SAFE: reads the diag that main_predict.py already wrote + statsapi finals.
It NEVER imports or modifies the live prediction path, the model, config thresholds,
or any stake. It only LOGS:
  (1) the Tier-1 per-pick schema (every field already lives in the diag), and
  (2) a counterfactual GOLD+ shadow candidate flag with shadow_stake = 0.10.

The live path is untouched: live stake stays whatever main_predict decided
(0 for every pick in the frozen era). This sidecar exists so that, by July, the
SKIP_AUDIT_SPEC_2026-06-14.md single-binding-reason / GOLD+ questions have clean
captured data. Per that spec this is Tier-1 only (no CLV / closing odds — those
need market data we do not capture).

Output: docs/data/skip_shadow_ledger.jsonl  (append-only, idempotent per date).
Wire into the nightly chain like the other sidecars; re-running a date refreshes
its rows (results fill in once games finalize).
"""
import sys, os, json, re, csv, time, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
DD = os.path.join("docs", "data")
LEDGER = os.path.join(DD, "skip_shadow_ledger.jsonl")
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-skipshadow/1.0"}

# Mirror of config.TIER_SIZES (hardcoded so we never import the live package).
# GOLD and below resolve to 0 stake; only DIAMOND/PLATINUM are stakeable.
TIER_SIZES = {"DIAMOND": 1.00, "PLATINUM": 0.30, "GOLD": 0.00}
CANON = {"CWS": "CHW", "AZ": "ARI", "ATH": "OAK", "WSN": "WSH",
         "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}
def canon(x): return CANON.get(str(x).strip(), str(x).strip())


def _get(url, timeout=30, retries=3, sleep=0.4):
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.load(r)
        except Exception as e:
            last = e; time.sleep(sleep)
    raise last


def _finals(date):
    try:
        j = _get("%s/schedule?sportId=1&date=%s&hydrate=team,linescore" % (API, date))
    except Exception:
        return {}
    out = {}
    for d in j.get("dates", []):
        for g in d.get("games", []):
            st = (g.get("status") or {}).get("abstractGameState")
            t = g["teams"]; ls = (g.get("linescore") or {}).get("teams") or {}
            a = canon(t["away"]["team"]["abbreviation"]); h = canon(t["home"]["team"]["abbreviation"])
            ar = (ls.get("away") or {}).get("runs"); hr = (ls.get("home") or {}).get("runs")
            out[(a, h)] = {"final": st == "Final", "ar": ar, "hr": hr}
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _gold_plus(tier, pick_prob, fair_prob, edge_pp, caps):
    """Pre-registered GOLD+ shadow candidate (SKIP_AUDIT_SPEC §6, Tier-1 subset:
    closing-market move filter omitted — needs data we don't capture)."""
    if tier != "GOLD":
        return False
    if pick_prob is None or fair_prob is None or edge_pp is None:
        return False
    if not (0.54 <= pick_prob <= 0.68):
        return False
    if fair_prob < 0.45:
        return False
    if not (5.0 <= edge_pp <= 12.0):
        return False
    # exclude calibrator-hallucination caps (3/6/9) + pick-side bullpen cap (7)
    if any(c in caps for c in (3, 6, 7, 9)):
        return False
    return True


def build(date):
    path = os.path.join(DD, "picks_%s_diag.csv" % date)
    if not os.path.exists(path):
        print("skip_shadow_audit: no diag for %s" % date)
        return []
    fin = _finals(date)
    csv.field_size_limit(10 ** 7)
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8", errors="replace")):
        mm = re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})", (r.get("matchup") or ""))
        if not mm:
            continue
        pick = (r.get("pick") or "").strip()
        if not pick or pick == "TBD":
            continue
        away, home = canon(mm.group(1)), canon(mm.group(2))
        pick_c = canon(pick)
        tier = (r.get("tier") or "").strip()
        why = (r.get("why_skipped") or "").strip()
        reasons = r.get("grade_reasons") or ""
        caps = sorted(set(int(n) for n in re.findall(r"\[HARD CAP (\d+)\]", reasons)))
        pp = _f(r.get("pick_prob")); fair = _f(r.get("fair_prob")); edge = _f(r.get("edge_pp"))
        live_staked = (why == "")
        live_stake_mult = (TIER_SIZES.get(tier, 0.0) if live_staked else 0.0)
        gp = _gold_plus(tier, pp, fair, edge, caps)
        # result / run_margin from finals (pick-side perspective)
        f = fin.get((away, home))
        result = None; run_margin = None
        if f and f.get("final") and f.get("ar") is not None and f.get("hr") is not None:
            ar, hr = f["ar"], f["hr"]
            pr = ar if pick_c == away else hr
            opp = hr if pick_c == away else ar
            run_margin = pr - opp
            result = "win" if pr > opp else "loss"
        rows.append({
            "game_id": "%s@%s" % (away, home),
            "date": date,
            "pick_side": pick_c,
            "pick_prob": pp,
            "fair_prob": fair,
            "edge_pp": edge,
            "tier": tier,
            "stake_mult": round(live_stake_mult, 3),
            "live_staked": live_staked,
            "why_skipped": why,
            "pre_cap_score": _f(r.get("pre_cap_score")),
            "pre_cap_grade": (r.get("pre_cap_grade") or "").strip(),
            "final_grade": (r.get("grade") or "").strip(),
            "cap_hit": caps,
            "gold_plus_shadow": gp,
            "shadow_stake": 0.10 if gp else 0.0,
            "result": result,
            "run_margin": run_margin,
            "scored_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
    return rows


def main(date):
    new_rows = build(date)
    # idempotent: drop any existing rows for this date, then append the fresh set
    existing = []
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("date") != date:
                existing.append(o)
    allrows = existing + new_rows
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for o in allrows:
            fh.write(json.dumps(o) + "\n")
    os.replace(tmp, LEDGER)
    n_gp = sum(1 for r in new_rows if r["gold_plus_shadow"])
    n_staked = sum(1 for r in new_rows if r["live_staked"])
    print("skip_shadow_audit %s: %d picks | live-staked %d | GOLD+ shadow %d -> %s"
          % (date, len(new_rows), n_staked, n_gp, LEDGER))
    for r in new_rows:
        if r["gold_plus_shadow"]:
            print("  GOLD+ shadow: %s pick %s  pp=%.3f fair=%.3f edge=%+.1f  result=%s margin=%s"
                  % (r["game_id"], r["pick_side"], r["pick_prob"], r["fair_prob"], r["edge_pp"],
                     r["result"], r["run_margin"]))


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    main(d)
