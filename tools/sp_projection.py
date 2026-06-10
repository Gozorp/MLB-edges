# -*- coding: utf-8 -*-
"""
sp_projection.py -- DISPLAY-ONLY projected starting pitcher for games whose
probable SP has not yet been announced. Rotation + rest-day heuristic via the
MLB Stats API. Writes docs/data/sp_projection_<date>.json.

FREEZE-SAFE: never feeds the model. The frozen booster still scores only
CONFIRMED starters (pending games stay SKIP). This sidecar only powers a
[PROJ] badge on the dashboard so the user can eyeball a likely matchup; it is
overwritten by the next chain run once the official probable posts.

Heuristic:
  1. Pull the team's listed starters for the prior ~16 days (one /schedule call
     hydrated with probablePitcher).
  2. Per pitcher, compute days of rest = slate_date - last_start_date.
  3. Eliminate anyone who started in the last 1-3 days (rest < 4).
  4. Among the rested rotation, pick the slot that is due -- rest closest to 5,
     within the standard 4-6 day window. Confidence reflects how clean the pick
     is (single 4-6d candidate = high; ambiguous = medium; fallback = low).
"""
import sys, os, json, re, csv, time, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-spproj/1.0"}


def _get(url, timeout=30, retries=3, sleep=0.4):
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception as e:
            last = e
            time.sleep(sleep)
    raise last


_ALIAS = {"CHW": "CWS", "ARI": "AZ", "OAK": "ATH", "WSN": "WSH",
          "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}


def _abbr2id():
    j = _get("%s/teams?sportId=1&season=%d" % (API, datetime.date.today().year))
    out = {}
    for t in j.get("teams", []):
        ab = (t.get("abbreviation") or "").strip()
        if ab and t.get("id"):
            out[ab] = t["id"]
    return out


def _pending_sides(date):
    """[(matchup, abbr, side)] for games whose probable SP is not yet announced,
    parsed from the diag's why_skipped column."""
    path = os.path.join("docs", "data", "picks_%s_diag.csv" % date)
    if not os.path.exists(path):
        path = "picks_%s_diag.csv" % date
    out = []
    if not os.path.exists(path):
        return out
    csv.field_size_limit(10 ** 7)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            m = (row.get("matchup") or "").strip()
            wy = row.get("why_skipped") or ""
            for mm in re.finditer(r"Probable SP not yet announced for ([A-Za-z]{2,3}) \((away|home)\)", wy):
                out.append((m, mm.group(1), mm.group(2)))
    return out


def _recent_starts(team_id, end_date):
    """[(date, pid, name)] for the team's games in the 16 days before end_date,
    newest first, using the listed probable/starter."""
    D = datetime.date.fromisoformat(end_date)
    d1 = (D - datetime.timedelta(days=16)).isoformat()
    d2 = (D - datetime.timedelta(days=1)).isoformat()
    url = "%s/schedule?sportId=1&teamId=%d&startDate=%s&endDate=%s&hydrate=probablePitcher" % (API, team_id, d1, d2)
    j = _get(url)
    starts = []
    for d in j.get("dates", []):
        ds = d.get("date")
        for g in d.get("games", []):
            if (g.get("gameType") or "R") not in ("R", "F", "D", "L", "W"):
                continue
            for side in ("away", "home"):
                t = ((g.get("teams") or {}).get(side) or {})
                if ((t.get("team") or {}).get("id")) != team_id:
                    continue
                pp = t.get("probablePitcher") or {}
                pid = pp.get("id"); nm = pp.get("fullName")
                if pid and ds:
                    starts.append((ds, pid, nm))
    starts.sort(key=lambda x: x[0], reverse=True)
    return starts


def _project(team_id, end_date):
    starts = _recent_starts(team_id, end_date)
    if not starts:
        return None
    D = datetime.date.fromisoformat(end_date)
    last, order = {}, []
    for ds, pid, nm in starts:
        if pid not in last:
            last[pid] = (ds, nm)
            order.append(pid)
    rest = {pid: (D - datetime.date.fromisoformat(last[pid][0])).days for pid in last}
    cand = [pid for pid in order if rest[pid] >= 4]   # eliminate last 1-3 day starters
    if not cand:
        return None
    # the slot that's due (~5d); ties prefer MORE rest -- the longer-rested arm is due first
    cand.sort(key=lambda pid: (abs(rest[pid] - 5), -rest[pid]))
    best = cand[0]
    r = rest[best]
    in_window = [pid for pid in cand if 4 <= rest[pid] <= 6]
    if 4 <= r <= 6 and len(in_window) == 1:
        conf = "high"
    elif 4 <= r <= 6:
        conf = "medium"
    else:
        conf = "low"
    return {"projected_sp": last[best][1], "pitcher_id": best, "rest_days": r,
            "rotation_size": len(order), "confidence": conf, "is_projected": True}


def main(date):
    pend = _pending_sides(date)
    games = {}
    abbr2id = _abbr2id() if pend else {}
    for matchup, abbr, side in pend:
        tid = abbr2id.get(abbr) or abbr2id.get(_ALIAS.get(abbr, abbr))
        if not tid:
            continue
        try:
            proj = _project(tid, date)
        except Exception as e:
            print("  project-fail %s (%s): %r" % (abbr, side, e))
            proj = None
        if not proj:
            continue
        proj["team"] = abbr
        games.setdefault(matchup, {})[side] = proj
    out = {"date": date,
           "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "method": "rotation-rest-heuristic",
           "games": games}
    p = os.path.join("docs", "data", "sp_projection_%s.json" % date)
    open(p, "w", encoding="utf-8").write(json.dumps(out, indent=2))
    n_proj = sum(len(v) for v in games.values())
    print("sp_projection: %d pending side(s), %d projected -> %s" % (len(pend), n_proj, p))
    for mk, sides in games.items():
        for side, pr in sides.items():
            print("  %s  (%s, %s): %s  [%dd rest, conf=%s, rot=%d]"
                  % (mk, pr["team"], side, pr["projected_sp"], pr["rest_days"], pr["confidence"], pr["rotation_size"]))


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    main(d)
