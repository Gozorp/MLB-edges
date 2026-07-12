#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Live state-tracker + box-score logger for a simulated baseball game.

Standalone utility -- imports nothing from the model/pipeline (freeze-safe).

Architecture (three separable pieces):
  * GameState / PlayerStats ......... the live state
  * BoxScoreLogger .................. prints a live line after EVERY at-bat
                                      and persists everything locally
  * GameSimulator ................... drives play-by-play events using the
                                      same league PA distribution as the
                                      repo's theoretical-chances engine

The logger consumes generic (state, batter, outcome, runs) events, so a real
MLB statsapi live-feed adapter can drive the identical logger unchanged.

Files written to --outdir (created if missing; pass the directory you want):
  <stamp>_playlog.txt   append-only play-by-play + cumulative tracker lines
  <stamp>_boxscore.json full cumulative box score, rewritten atomically per PA
  <stamp>_boxscore.csv  same box score as structured CSV (one row per player)
  live_state.json       latest snapshot (inning/half/outs/bases/score + all
                        player lines), rewritten atomically per PA -- poll
                        this from anywhere for the rolling game state

Usage:
  python tools/game_tracker.py --outdir "D:/your/designated/path"
  python tools/game_tracker.py --outdir game_logs --seed 7 --quiet-innings
  python tools/game_tracker.py --watch "Shohei Ohtani,Dalton Rushing"
  python tools/game_tracker.py --selftest
"""
import argparse
import csv
import datetime
import io
import json
import os
import random
import sys

# League PA outcome distribution -- identical numbers to the repo's
# theoretical-chances Markov engine (_THEO_LEAGUE_PA in docs/index.html and
# mlb_edge/theoretical_chances.py): out, bb, 1b, 2b, 3b, hr.
LEAGUE_PA = {"OUT": 0.690, "BB": 0.085, "1B": 0.140, "2B": 0.045,
             "3B": 0.004, "HR": 0.036}

# Small per-player tilts (renormalized at use). Add names freely.
PROFILES = {
    "Shohei Ohtani":  {"HR": 0.072, "BB": 0.110, "OUT": 0.640},
    "Corbin Carroll": {"3B": 0.012, "1B": 0.155},
    "Dalton Rushing": {"BB": 0.095},
}

DEFAULT_AWAY = ("ARI", ["Corbin Carroll", "Ketel Marte", "Geraldo Perdomo",
                        "Josh Naylor", "Eugenio Suarez", "Gabriel Moreno",
                        "Lourdes Gurriel Jr.", "Jake McCarthy", "Blaze Alexander"])
DEFAULT_HOME = ("LAD", ["Shohei Ohtani", "Mookie Betts", "Freddie Freeman",
                        "Teoscar Hernandez", "Max Muncy", "Dalton Rushing",
                        "Tommy Edman", "Andy Pages", "Miguel Rojas"])

ORDINAL = ["", "First", "Second", "Third", "Fourth", "Fifth", "Sixth",
           "Seventh", "Eighth", "Ninth"]


def inning_name(n):
    return (ORDINAL[n] + " Inning") if n < len(ORDINAL) else ("Inning %d" % n)


class PlayerStats(object):
    """Cumulative standard offensive line: PA, AB, R, H, BB, K, 2B, 3B, HR, RBI."""
    __slots__ = ("name", "team", "pa", "ab", "h", "bb", "k",
                 "d2", "d3", "hr", "rbi", "r")

    def __init__(self, name, team):
        self.name, self.team = name, team
        self.pa = self.ab = self.h = self.bb = self.k = 0
        self.d2 = self.d3 = self.hr = self.rbi = self.r = 0

    def record(self, outcome, rbi, is_k=False):
        self.pa += 1
        if outcome == "BB":
            self.bb += 1
        else:
            self.ab += 1
            if is_k:
                self.k += 1
            if outcome in ("1B", "2B", "3B", "HR"):
                self.h += 1
                if outcome == "2B": self.d2 += 1
                if outcome == "3B": self.d3 += 1
                if outcome == "HR": self.hr += 1
        self.rbi += rbi

    @property
    def avg(self):
        return (float(self.h) / self.ab) if self.ab else 0.0

    def line(self):
        return "%-22s AB:%2d R:%2d H:%2d BB:%2d K:%2d HR:%2d RBI:%2d AVG:.%03d" % (
            self.name, self.ab, self.r, self.h, self.bb, self.k,
            self.hr, self.rbi, round(self.avg * 1000))

    def live_fragment(self):
        """Compact per-play form: '1 AB, 0 R, 1 H, 1 RBI' (extras only if >0)."""
        parts = ["%d AB" % self.ab, "%d R" % self.r, "%d H" % self.h]
        for label, v in (("BB", self.bb), ("K", self.k),
                         ("HR", self.hr), ("RBI", self.rbi)):
            if v:
                parts.append("%d %s" % (v, label))
        return "%s: %s" % (self.name, ", ".join(parts))

    def as_dict(self):
        return {"name": self.name, "team": self.team, "pa": self.pa,
                "ab": self.ab, "r": self.r, "h": self.h, "bb": self.bb,
                "k": self.k, "2b": self.d2, "3b": self.d3, "hr": self.hr,
                "rbi": self.rbi, "avg": round(self.avg, 3)}


class GameState(object):
    def __init__(self, away, home):
        self.away, self.home = away, home          # team codes
        self.inning = 1
        self.half = "Top"                          # Top = away bats
        self.outs = 0
        self.bases = [None, None, None]            # 1B, 2B, 3B -> batter name
        self.score = {away: 0, home: 0}
        self.final = False

    def header(self):
        return "%s %s | %s %d - %s %d | %d out%s" % (
            self.half, inning_name(self.inning).replace(" Inning", ""),
            self.away, self.score[self.away], self.home, self.score[self.home],
            self.outs, "" if self.outs == 1 else "s")

    def as_dict(self):
        return {"inning": self.inning, "inning_name": inning_name(self.inning),
                "label": "Inning %d, %s" % (self.inning, self.half),
                "half": self.half, "outs": self.outs,
                "bases": list(self.bases), "score": dict(self.score),
                "final": self.final}


class BoxScoreLogger(object):
    """Prints the live line after every play and persists all state locally.

    watch_names: players named on the per-play tracker line. Empty/None =
    automatic: every player on the CURRENT batting team who has batted,
    in lineup order (his full line appears the moment he first bats)."""

    def __init__(self, outdir, watch_names=None, quiet_innings=False, echo=True):
        self.outdir = outdir
        self.watch = list(watch_names or [])
        self.quiet = quiet_innings
        self.echo = echo
        self.plays = 0
        os.makedirs(outdir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(outdir, "%s_playlog.txt" % stamp)
        self.box_path = os.path.join(outdir, "%s_boxscore.json" % stamp)
        self.csv_path = os.path.join(outdir, "%s_boxscore.csv" % stamp)
        self.state_path = os.path.join(outdir, "live_state.json")

    # -- output primitives ---------------------------------------------------
    def _emit(self, line):
        if self.echo:
            print(line)
        with io.open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _atomic_json(self, path, obj):
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1)
        os.replace(tmp, path)  # atomic on the same volume

    # -- event API (a live-feed adapter can call these unchanged) -----------
    def on_play(self, state, players, batter, outcome, rbi, detail):
        self.plays += 1
        if self.watch:
            shown = [players[n] for n in self.watch if n in players]
        else:
            team = state.away if state.half == "Top" else state.home
            shown = [p for p in players.values() if p.team == team and p.pa > 0]
        tracker = " | ".join(p.live_fragment() for p in shown)
        self._emit("[%s] %s %s%s" % (state.header(), batter.name, detail,
                                     (" (%d RBI)" % rbi) if rbi else ""))
        self._emit("    %s - %s" % (inning_name(state.inning), tracker))
        self._persist(state, players)

    def on_half_end(self, state, players):
        if not self.quiet:
            self._emit("-" * 64)
            self._emit("End %s %s" % (state.half, inning_name(state.inning)))
            for line in self.box_lines(players):
                self._emit(line)
            self._emit("-" * 64)
        self._persist(state, players)

    def on_final(self, state, players):
        self._emit("=" * 64)
        self._emit("FINAL: %s %d - %s %d (%d innings)" % (
            state.away, state.score[state.away],
            state.home, state.score[state.home], state.inning))
        for line in self.box_lines(players):
            self._emit(line)
        self._emit("=" * 64)
        self._emit("playlog:  %s" % os.path.abspath(self.log_path))
        self._emit("boxscore: %s" % os.path.abspath(self.box_path))
        self._persist(state, players)

    # -- box score -----------------------------------------------------------
    def box_lines(self, players):
        lines, teams = [], []
        for p in players.values():          # insertion order == lineup order
            if p.team not in teams:
                teams.append(p.team)
        for team in teams:
            lines.append("%s box:" % team)
            for p in players.values():
                if p.team == team:
                    lines.append("  " + p.line())
        return lines

    def _persist(self, state, players):
        self._atomic_json(self.box_path, {
            "state": state.as_dict(),
            "players": [p.as_dict() for p in players.values()],
            "plays": self.plays})
        self._atomic_json(self.state_path, {
            "state": state.as_dict(),
            "players": [p.as_dict() for p in players.values()],
            "plays": self.plays,
            "updated": datetime.datetime.now().isoformat(timespec="seconds")})
        # comprehensive rolling CSV, rewritten atomically after every play
        tmp = self.csv_path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["team", "name", "pa", "ab", "r", "h", "bb", "k",
                        "2b", "3b", "hr", "rbi", "avg"])
            for p in players.values():
                w.writerow([p.team, p.name, p.pa, p.ab, p.r, p.h, p.bb, p.k,
                            p.d2, p.d3, p.hr, p.rbi, "%.3f" % p.avg])
        os.replace(tmp, self.csv_path)


class GameSimulator(object):
    """Plays a full game and feeds every at-bat to the logger."""

    K_SHARE = 0.35                        # strikeout share of all outs
    OUT_FLAVORS = [("grounds out", 0.51), ("flies out", 0.37),
                   ("lines out", 0.12)]
    HIT_TEXT = {"1B": "singles", "2B": "doubles", "3B": "triples",
                "HR": "HOME RUN"}

    def __init__(self, away, home, logger, seed=None):
        self.rng = random.Random(seed)
        self.state = GameState(away[0], home[0])
        self.logger = logger
        self.lineups = {away[0]: away[1], home[0]: home[1]}
        self.spot = {away[0]: 0, home[0]: 0}
        self.players = {}
        for code, names in self.lineups.items():
            for n in names:
                self.players[n] = PlayerStats(n, code)

    def _rates(self, name):
        r = dict(LEAGUE_PA)
        r.update(PROFILES.get(name, {}))
        tot = sum(r.values())
        return {k: v / tot for k, v in r.items()}

    def _sample(self, rates):
        x, acc = self.rng.random(), 0.0
        for k, v in rates.items():
            acc += v
            if x <= acc:
                return k
        return "OUT"

    def _advance(self, outcome, batter_name):
        """Simplified base-running. Returns runs scored (list of scorer names).
        1B: 3rd & 2nd score, 1st->2nd.  2B: 3rd & 2nd score, 1st->3rd.
        3B: all score.  HR: everyone incl. batter.  BB: forces only."""
        b = self.state.bases
        runs = []
        if outcome == "BB":
            if b[0] is not None:
                if b[1] is not None:
                    if b[2] is not None:
                        runs.append(b[2]); b[2] = None
                    b[2] = b[1]; b[1] = None
                b[1] = b[0]
            b[0] = batter_name
        elif outcome == "1B":
            if b[2] is not None: runs.append(b[2]); b[2] = None
            if b[1] is not None: runs.append(b[1]); b[1] = None
            if b[0] is not None: b[1], b[0] = b[0], None
            b[0] = batter_name
        elif outcome == "2B":
            if b[2] is not None: runs.append(b[2]); b[2] = None
            if b[1] is not None: runs.append(b[1]); b[1] = None
            if b[0] is not None: b[2], b[0] = b[0], None
            b[1] = batter_name
        elif outcome == "3B":
            for i in (2, 1, 0):
                if b[i] is not None: runs.append(b[i]); b[i] = None
            b[2] = batter_name
        elif outcome == "HR":
            for i in (2, 1, 0):
                if b[i] is not None: runs.append(b[i]); b[i] = None
            runs.append(batter_name)
        return runs

    def _out_text(self):
        """Returns (detail_text, is_strikeout)."""
        if self.rng.random() < self.K_SHARE:
            return "strikes out", True
        x, acc = self.rng.random(), 0.0
        for text, p in self.OUT_FLAVORS:
            acc += p
            if x <= acc:
                return text, False
        return "grounds out", False

    def substitute(self, team, out_name, in_name):
        """Lineup update: in_name takes out_name's batting spot; the new
        player starts a fresh stat line (both remain in the box score)."""
        lu = self.lineups[team]
        i = lu.index(out_name)
        lu[i] = in_name
        if in_name not in self.players:
            self.players[in_name] = PlayerStats(in_name, team)
        self.logger._emit(">> Lineup update (%s): %s replaces %s (spot %d)"
                          % (team, in_name, out_name, i + 1))

    def _batting_team(self):
        return self.state.away if self.state.half == "Top" else self.state.home

    def _half_inning(self):
        st = self.state
        st.outs, st.bases = 0, [None, None, None]
        team = self._batting_team()
        while st.outs < 3:
            name = self.lineups[team][self.spot[team] % 9]
            self.spot[team] += 1
            batter = self.players[name]
            outcome = self._sample(self._rates(name))
            is_k = False
            if outcome == "OUT":
                st.outs += 1
                (detail, is_k), runs = self._out_text(), []
            else:
                runs = self._advance(outcome, name)
                detail = self.HIT_TEXT.get(outcome, "walks")
            for scorer in runs:
                self.players[scorer].r += 1
            st.score[team] += len(runs)
            batter.record(outcome, len(runs), is_k)
            self.logger.on_play(st, self.players, batter, outcome,
                                len(runs), detail)
            # walk-off: home takes the lead in the 9th or later
            if (st.half == "Bottom" and st.inning >= 9
                    and st.score[st.home] > st.score[st.away]):
                return
        self.logger.on_half_end(st, self.players)

    def play(self):
        st = self.state
        while True:
            st.half = "Top"
            self._half_inning()
            home_leads = st.score[st.home] > st.score[st.away]
            if not (st.inning >= 9 and home_leads):    # skip bottom if decided
                st.half = "Bottom"
                self._half_inning()
            if st.inning >= 9 and st.score[st.home] != st.score[st.away]:
                break
            st.inning += 1
        st.final = True
        self.logger.on_final(st, self.players)
        return st


def selftest():
    import tempfile, shutil
    tmp = tempfile.mkdtemp(prefix="game_tracker_selftest_")
    try:
        logger = BoxScoreLogger(tmp, [], quiet_innings=True, echo=False)
        sim = GameSimulator(DEFAULT_AWAY, DEFAULT_HOME, logger, seed=7)
        st = sim.play()
        assert st.final and st.score[st.away] != st.score[st.home]
        box = json.load(io.open(logger.box_path, encoding="utf-8"))
        live = json.load(io.open(logger.state_path, encoding="utf-8"))
        assert live["state"]["final"] is True
        assert live["plays"] == logger.plays > 50
        assert live["state"]["label"].startswith("Inning ")
        total_h = sum(p["h"] for p in box["players"])
        total_ab = sum(p["ab"] for p in box["players"])
        total_k = sum(p["k"] for p in box["players"])
        assert 0 < total_h < total_ab
        assert total_k > 0                      # strikeouts tracked
        # runs bookkeeping: team runs == sum of player runs per team
        for team in (st.away, st.home):
            assert st.score[team] == sum(p["r"] for p in box["players"]
                                         if p["team"] == team)
        # playlog: 2 lines per play + final block; live format present
        log_txt = io.open(logger.log_path, encoding="utf-8").read()
        assert log_txt.count("\n") >= logger.plays * 2
        assert " AB, " in log_txt and " | " in log_txt
        # CSV: header + 18 lineup rows, parses back
        rows = list(csv.reader(io.open(logger.csv_path, encoding="utf-8")))
        assert len(rows) == 1 + 18 and rows[0][:6] == ["team", "name", "pa",
                                                       "ab", "r", "h"]
        assert sum(int(r[5]) for r in rows[1:]) == total_h
        # lineup update mechanics
        sim.substitute("LAD", "Miguel Rojas", "Kike Hernandez")
        assert "Kike Hernandez" in sim.players
        assert sim.lineups["LAD"][8] == "Kike Hernandez"
        # determinism
        logger2 = BoxScoreLogger(tmp, ["Shohei Ohtani"], quiet_innings=True,
                                 echo=False)
        st2 = GameSimulator(DEFAULT_AWAY, DEFAULT_HOME, logger2, seed=7).play()
        assert st2.score == st.score
        print("SELFTEST PASS -- final %s %d-%d %s in %d innings, %d plays, "
              "%d H / %d K; playlog+boxscore JSON+CSV+live_state verified in %s"
              % (st.away, st.score[st.away], st.score[st.home], st.home,
                 st.inning, logger.plays, total_h, total_k, tmp))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outdir", default="game_logs",
                    help="directory for playlog/boxscore/live_state files "
                         "(created if missing) -- point this at your "
                         "designated path")
    ap.add_argument("--seed", type=int, default=None,
                    help="RNG seed for a reproducible game")
    ap.add_argument("--watch", default="",
                    help="comma-separated players for the per-play tracker "
                         "line; default = every batter on the current "
                         "batting team who has batted (full lineup coverage)")
    ap.add_argument("--quiet-innings", action="store_true",
                    help="suppress the full box score between half-innings")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    watch = [w.strip() for w in args.watch.split(",") if w.strip()]
    logger = BoxScoreLogger(args.outdir, watch,
                            quiet_innings=args.quiet_innings)
    sim = GameSimulator(DEFAULT_AWAY, DEFAULT_HOME, logger, seed=args.seed)
    sim.play()
    return 0


if __name__ == "__main__":
    sys.exit(main())
