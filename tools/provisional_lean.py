# -*- coding: utf-8 -*-
"""
provisional_lean.py -- DISPLAY-ONLY provisional win lean for TBD / no-SP games.

FREEZE-SAFE: never touches the frozen model, weights, or any stake. For games the
model leaves TBD (probable SP not announced) it produces a clearly-labeled
PROVISIONAL lean from SP-INDEPENDENT signals only -- team strength (season win% +
run differential) and home-field. The frozen model still SKIPs the game and scores
it for real once the SP posts; this is an eyeball estimate, not a model pick, never staked.

Honest scope: for a fully-TBD game the richer secondary signals are unavailable
(Kalshi prices/totals are not posted pre-SP, platoon needs the SP's hand). So this is
deliberately a low-information "better team + home field" lean -- exactly why the model
itself declines to score these. Confidence is always tagged low/medium accordingly.

Writes docs/data/provisional_lean_<date>.json.
"""
import sys, os, json, re, csv, time, math, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-provlean/1.0"}
HOME_FIELD_LOGIT = 0.16     # MLB home teams win ~54% -> logit(0.54)
K_STRENGTH = 1.05           # strength-gap -> logit scale
CANON = {"CHW": "CWS", "ARI": "AZ", "OAK": "ATH", "WSN": "WSH",
         "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}
def cn(x): return CANON.get(str(x).strip(), str(x).strip())


def _get(url, timeout=25, retries=3, sleep=0.4):
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.load(r)
        except Exception as e:
            last = e; time.sleep(sleep)
    raise last


def _tbd_games(date):
    path = os.path.join("docs", "data", "picks_%s_diag.csv" % date)
    if not os.path.exists(path):
        return []
    csv.field_size_limit(10 ** 7)
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8", errors="replace")):
        if (r.get("pick") or "").strip() == "TBD":
            mm = re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})", (r.get("matchup") or ""))
            if mm:
                out.append(((r.get("matchup") or "").strip(), cn(mm.group(1)), cn(mm.group(2))))
    return out


def _team_strength():
    """abbr -> {winpct, rd_pg, strength(z-composite)}."""
    yr = datetime.date.today().year
    teams = _get("%s/teams?sportId=1&season=%d" % (API, yr))
    id2ab = {t["id"]: cn(t.get("abbreviation") or "") for t in teams.get("teams", []) if t.get("id")}
    st = _get("%s/standings?leagueId=103,104&season=%d&standingsTypes=regularSeason" % (API, yr))
    rows = {}
    for rec in st.get("records", []):
        for tr in rec.get("teamRecords", []):
            ab = id2ab.get((tr.get("team") or {}).get("id"))
            if not ab:
                continue
            gp = tr.get("gamesPlayed") or 0
            wp = float(tr.get("winningPercentage") or 0)
            rd = float(tr.get("runDifferential") or 0)
            rows[ab] = {"winpct": wp, "rd_pg": (rd / gp) if gp else 0.0}
    if not rows:
        return {}
    import statistics as S
    wps = [v["winpct"] for v in rows.values()]; rds = [v["rd_pg"] for v in rows.values()]
    mw, sw = S.mean(wps), (S.pstdev(wps) or 1e-9)
    mr, sr = S.mean(rds), (S.pstdev(rds) or 1e-9)
    for ab, v in rows.items():
        v["strength"] = 0.5 * ((v["winpct"] - mw) / sw) + 0.5 * ((v["rd_pg"] - mr) / sr)
    return rows


def main(date):
    tbd = _tbd_games(date)
    games = {}
    if tbd:
        strength = _team_strength()
        for matchup, away, home in tbd:
            sa = strength.get(away, {}).get("strength")
            sh = strength.get(home, {}).get("strength")
            if sa is None or sh is None:
                continue
            logit = K_STRENGTH * (sh - sa) + HOME_FIELD_LOGIT
            p_home = 1.0 / (1.0 + math.exp(-logit))
            pick = home if p_home >= 0.5 else away
            p_pick = p_home if pick == home else (1.0 - p_home)
            conf = "medium" if abs(p_pick - 0.5) >= 0.06 else "low"
            games[matchup] = {
                "provisional_pick": pick, "p_pick": round(p_pick, 3),
                "p_home": round(p_home, 3), "basis": "team-strength(win%+runDiff)+home-field",
                "confidence": conf, "is_provisional": True, "no_sp": True,
                "away": away, "home": home,
                "away_strength": round(sa, 2), "home_strength": round(sh, 2),
            }
    out = {"date": date, "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "method": "sp-independent-team-strength", "note": "DISPLAY-ONLY provisional lean; never feeds the model or staking",
           "games": games}
    p = os.path.join("docs", "data", "provisional_lean_%s.json" % date)
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(json.dumps(out, indent=2))
    os.replace(p + ".tmp", p)
    print("provisional_lean %s: %d TBD game(s), %d leaned -> %s" % (date, len(tbd), len(games), p))
    for mk, g in games.items():
        print("  %s  PROVISIONAL %s ~%.0f%% [%s, no-SP]  (str %s: %+.2f vs %s: %+.2f)"
              % (mk, g["provisional_pick"], g["p_pick"] * 100, g["confidence"],
                 g["home"], g["home_strength"], g["away"], g["away_strength"]))


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    main(d)
