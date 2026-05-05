"""
beginner_slate.py
-----------------
Generate a plain-English summary of today's slate. Translates the model's
technical conviction signals (F1/F2/F3/F4/F5) into readable explanations
for someone new to baseball betting.

Output:
  - Prints a clean per-game summary to the terminal
  - Saves the same content as Markdown to slate_readable_YYYY-MM-DD.md

Usage:
    python scripts/beginner_slate.py             # today
    python scripts/beginner_slate.py --date 2026-04-26
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# Windows default cp1252 stdout can't render the box-drawing chars + emoji we
# use for the human-readable output. Force UTF-8 so the terminal print works
# regardless of the host's code page.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)


# Full team names so the beginner doesn't need to know 3-letter codes
TEAM_NAMES: dict = {
    "ARI": "Arizona Diamondbacks", "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles", "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs", "CHW": "Chicago White Sox",
    "CWS": "Chicago White Sox", "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians", "COL": "Colorado Rockies",
    "DET": "Detroit Tigers", "HOU": "Houston Astros",
    "KC":  "Kansas City Royals", "KCR": "Kansas City Royals",
    "LAA": "Los Angeles Angels", "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins", "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins", "NYM": "New York Mets",
    "NYY": "New York Yankees", "OAK": "Oakland Athletics",
    "ATH": "Athletics", "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates", "SD":  "San Diego Padres",
    "SDP": "San Diego Padres", "SEA": "Seattle Mariners",
    "SF":  "San Francisco Giants", "SFG": "San Francisco Giants",
    "STL": "St. Louis Cardinals", "TB":  "Tampa Bay Rays",
    "TBR": "Tampa Bay Rays", "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays", "WSH": "Washington Nationals",
    "WSN": "Washington Nationals",
}


def team_name(abbr: str) -> str:
    return TEAM_NAMES.get(abbr, abbr)


def poss(name: str) -> str:
    """Possessive form: 'Cubs' -> 'Cubs'' (handles names ending in s)."""
    return name + "'" if name.endswith("s") else name + "'s"


def explain_signal(sig: str, pick_team: str, opp_team: str) -> str:
    """Translate one technical signal token into a plain-English sentence."""
    pname = team_name(pick_team)
    oname = team_name(opp_team)
    s = sig.strip()
    if s.startswith("F1_xera_gap"):
        m = re.search(r"=\s*([+-]?\d+\.?\d*)", s)
        gap = m.group(1) if m else "?"
        return (f"⚾ {poss(pname)} starting pitcher is much better than the {oname} "
                f"starter (expected-runs gap: {gap} per 9 innings)")
    if s.startswith("F2_xwoba_gap"):
        return (f"💥 {poss(pname)} entire batting lineup is significantly better "
                f"than the {oname} lineup")
    if s.startswith("F3_swing_take_gap"):
        return (f"🎯 {poss(pname)} hitters are picking better pitches to swing at — "
                f"more disciplined plate approach than {oname}")
    if s.startswith("F4_our_sp_unlucky"):
        return (f"📈 {poss(pname)} starting pitcher has had bad luck recently — his "
                f"actual ERA is higher than what his underlying stats predict, "
                f"so he's expected to pitch better today (regression to the mean)")
    if s.startswith("F4_opp_sp_lucky"):
        return (f"📉 The {oname} starting pitcher has been getting lucky — his "
                f"actual ERA is better than his underlying stats deserve, so he's "
                f"due to give up more runs")
    if s.startswith("F5_bullpen_gap"):
        m = re.search(r"=\s*[+-]?(\d+\.?\d*)", s)
        gap = m.group(1) if m else "?"
        return (f"🛡️ {poss(pname)} bullpen (relief pitchers) is significantly better "
                f"than the {oname} bullpen — important because games often turn "
                f"in the 7th-9th innings (gap: {gap} runs per 9 innings)")
    return f"• {s}"


def explain_skip_note(note: str, pick_team: str, opp_team: str) -> str:
    """Translate a SKIP note into a plain-English reason."""
    pname = team_name(pick_team)
    oname = team_name(opp_team)
    n = note.lower()
    if "f1 negative" in n and "veto" in n and "suppressed" not in n:
        return (f"⚠️ Why we skip betting: the {oname} starting pitcher is "
                f"meaningfully better than {poss(pname)} starter, so even with "
                f"{pname} as a slight favorite, the SP disadvantage is too "
                f"big a risk to bet against.")
    if "f5 bullpen veto" in n:
        return (f"⚠️ Why we skip betting: {poss(pname)} bullpen is much worse "
                f"than the {oname} bullpen, so even if {pname} leads through "
                f"6 innings, late-game collapse risk makes this too dangerous "
                f"to bet on.")
    if "small sample" in n or "suppressed" in n:
        return (f"⚠️ Why we skip betting: not enough data on the starting "
                f"pitcher(s) yet — model can predict the game but the "
                f"confidence isn't high enough to risk money on it.")
    return f"  Note: {note}"


def feature_pros(g_features, pick_team: str) -> list[str]:
    """Build plain-English bullets describing what tilts the model toward
    `pick_team`, based on the raw v11 feature values for the game.

    Used for SKIP games where the conviction filter killed all signals but
    the model still has a probability >= 50% on one side. Beginners want
    to know "who do you think wins?" — this gives them the underlying
    "why" without the bet."""
    pros: list[str] = []
    if g_features is None:
        return pros

    is_home_pick = (pick_team == g_features.get("home_team"))
    sign = 1 if is_home_pick else -1   # flip features for away picks

    # Lineup quality (positive home gap = home advantage)
    lineup = g_features.get("team_wrcplus_gap", 0) or 0
    if abs(lineup) >= 5 and (sign * lineup) > 0:
        pros.append(f"💥 Stronger overall offense (wRC+ gap of "
                    f"{abs(lineup):.0f} points in favor of "
                    f"{team_name(pick_team)})")

    # Bullpen quality
    bp = g_features.get("bullpen_siera_gap", 0) or 0
    if abs(bp) >= 0.20 and (sign * bp) > 0:
        pros.append(f"🛡️ Better bullpen (relief-pitching gap of "
                    f"{abs(bp):.2f} runs/9 favors {team_name(pick_team)})")

    # Recent form / win pct
    winp = g_features.get("team_win_pct_gap", 0) or 0
    if abs(winp) >= 0.05 and (sign * winp) > 0:
        pros.append(f"📊 Better season-to-date record "
                    f"(win pct gap of {abs(winp):.3f} favors "
                    f"{team_name(pick_team)})")

    # Run differential
    rd = g_features.get("team_run_diff_pg_gap", 0) or 0
    if abs(rd) >= 0.30 and (sign * rd) > 0:
        pros.append(f"⚖️ Better run differential per game "
                    f"({abs(rd):+.2f} runs/game advantage)")

    # Home-field
    if is_home_pick:
        pros.append(f"🏟️ Playing at home (small but real advantage)")

    # Park factor (only mention if extreme)
    park_runs = g_features.get("park_runs_factor", 1.0) or 1.0
    if park_runs >= 1.05 and is_home_pick:
        pros.append(f"🌬️ Hitter-friendly home park (runs factor "
                    f"{park_runs:.2f}) — favors the home offense")
    elif park_runs <= 0.95 and is_home_pick:
        pros.append(f"⛰️ Pitcher-friendly home park (runs factor "
                    f"{park_runs:.2f}) — favors the home pitching")

    return pros


def tier_label(tier: str) -> str:
    return {
        "DIAMOND": "💎 DIAMOND (highest confidence — 4 signals aligned)",
        "PLATINUM": "🏆 PLATINUM (high confidence — 2-3 signals aligned)",
        "GOLD":    "🥇 GOLD (moderate confidence — 1 strong signal)",
        "SKIP":    "❌ SKIP (don't bet)",
    }.get(tier, tier)


def confidence_label(prob: float) -> str:
    if prob >= 0.65:
        return "STRONG"
    if prob >= 0.55:
        return "MODERATE"
    if prob >= 0.51:
        return "SLIGHT EDGE"
    return "ESSENTIALLY A COIN FLIP"


def render_game(g, picks_lookup: dict, lines: list[str],
                features_lookup: dict | None = None) -> None:
    away, home, pick = g["away"], g["home"], g["pick"]
    pname = team_name(pick)
    opp = away if pick == home else home
    oname = team_name(opp)
    pick_prob = float(g["pick_prob"]) / 100.0
    tier = g["tier"]
    signals = str(g["signals"]).strip()
    notes = str(g["notes"]).strip()

    lines.append("")
    lines.append("═" * 75)
    lines.append(f"  {team_name(away)}  @  {team_name(home)}")
    lines.append("═" * 75)
    lines.append(f"  📊 Model's pick: **{pname}** ({pick_prob:.0%} chance to win)")
    lines.append(f"  🎚️  Confidence: {confidence_label(pick_prob)} — {tier_label(tier)}")
    lines.append("")

    # Conviction-signal bullets (when present)
    has_signals = signals and signals.lower() not in ("nan", "")
    if has_signals:
        lines.append("  Why the model picks this side:")
        for s in [x.strip() for x in signals.split(",") if x.strip()]:
            lines.append(f"    {explain_signal(s, pick, opp)}")
        lines.append("")

    # For SKIP games (no conviction signals fired), pull underlying feature
    # gaps from the slate frame so beginners see WHY the model still leans
    # one way, not just the veto reason.
    g_features = (features_lookup or {}).get((away, home))
    if tier == "SKIP" and g_features is not None:
        pros = feature_pros(g_features, pick)
        if pros:
            lines.append(f"  Why {pname} is still the model's lean:")
            for p in pros:
                lines.append(f"    {p}")
            lines.append("")

    # SKIP / suppression reasoning — dedupe so the same plain-English
    # explanation isn't repeated when both F1 and F4 raise the same
    # "small sample" note about the same starter.
    if notes and notes.lower() not in ("nan", ""):
        seen: set = set()
        for n in [x.strip() for x in notes.split("|") if x.strip()]:
            msg = explain_skip_note(n, pick, opp)
            if msg in seen:
                continue
            seen.add(msg)
            lines.append(f"    {msg}")
        lines.append("")

    # Bet recommendation
    bet_row = picks_lookup.get((away, home))
    if bet_row is not None:
        team_bet = bet_row["team"]
        dec = float(bet_row["decimal"])
        edge = float(bet_row["edge_pp"])
        ev = float(bet_row["ev_per_$1"])
        stake = float(bet_row["stake_u"])
        american = (
            f"+{int(round((dec - 1) * 100))}" if dec >= 2.0
            else f"-{int(round(100 / (dec - 1)))}"
        )
        lines.append(f"  💰 BET: ${stake:.2f} on {team_name(team_bet)} at {american} odds")
        lines.append(f"     Expected profit: ${stake * ev:.2f}  "
                     f"(model edge: +{edge:.1f}%)")
    elif tier in ("DIAMOND", "PLATINUM", "GOLD"):
        lines.append(f"  🟡 Model likes {pname} but the betting market already "
                     f"prices them in — no profitable bet available")
    else:
        lines.append(f"  ⚫ No bet recommended on this game")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=lambda s: date.fromisoformat(s),
                    default=date.today(),
                    help="Slate date (default: today). Format YYYY-MM-DD")
    args = ap.parse_args()
    day = args.date

    audit_path = ROOT / f"audit_{day:%Y-%m-%d}.csv"
    picks_path = ROOT / f"picks_{day:%Y-%m-%d}.csv"

    if not audit_path.exists():
        print(f"ERROR: audit file not found at {audit_path}")
        print(f"Run: python audit_v10.py  (and ensure day={day})")
        return 1

    audit = pd.read_csv(audit_path)
    picks_lookup: dict = {}
    if picks_path.exists():
        picks = pd.read_csv(picks_path)
        # picks file has team but not away/home pair — join via game_id is
        # cleaner if present, but the audit doesn't carry game_id. Easiest:
        # match by team appearing as away OR home in audit.
        for _, p in picks.iterrows():
            t = p["team"]
            for _, a in audit.iterrows():
                if a["away"] == t or a["home"] == t:
                    picks_lookup[(a["away"], a["home"])] = p
                    break

    # Load slate features so we can give SKIP games a "model still leans X"
    # paragraph based on raw feature gaps. Best-effort — if the slate fails
    # to build, we simply omit the extra reasoning.
    features_lookup: dict = {}
    try:
        sys.path.insert(0, str(ROOT))
        from mlb_edge.build_pipeline import build_slate_frame
        slate = build_slate_frame(day, include_weather=False)
        for _, r in slate.iterrows():
            features_lookup[(r["away_team"], r["home_team"])] = r.to_dict()
    except Exception as e:
        print(f"(beginner_slate: couldn't load slate features for SKIP "
              f"reasoning — falling back to bare audit: {e})", file=sys.stderr)

    lines: list[str] = []
    lines.append("")
    lines.append("┏" + "━" * 73 + "┓")
    lines.append(f"┃   ⚾  MLB SLATE — {day.strftime('%A, %B %d, %Y')}".ljust(74) + "┃")
    lines.append("┃   How to read: each box shows ONE game with the model's pick,".ljust(74) + "┃")
    lines.append("┃   confidence level, plain-English reasoning, and bet advice.".ljust(74) + "┃")
    lines.append("┗" + "━" * 73 + "┛")

    # Sort: bets first (by stake desc), then non-bet convictions, then skips
    def sort_key(row):
        tier_rank = {"DIAMOND": 0, "PLATINUM": 1, "GOLD": 2, "SKIP": 3}
        away, home = row["away"], row["home"]
        is_bet = (away, home) in picks_lookup
        return (0 if is_bet else 1, tier_rank.get(row["tier"], 4),
                -float(row["pick_prob"]))

    audit_sorted = audit.sort_values(
        by=audit.columns.tolist(),
        key=lambda _: pd.Series([sort_key(r) for _, r in audit.iterrows()]),
    )

    # ── BET SHEET FIRST ──────────────────────────────────────────────────
    if picks_lookup:
        lines.append("")
        lines.append("━" * 75)
        lines.append(f"  💰 ACTIONABLE BETS — {len(picks_lookup)} game(s) worth betting today")
        lines.append("━" * 75)
        for _, g in audit_sorted.iterrows():
            if (g["away"], g["home"]) in picks_lookup:
                render_game(g, picks_lookup, lines, features_lookup)
        total_stake = sum(float(p["stake_u"]) for p in picks_lookup.values())
        total_ev = sum(float(p["stake_u"]) * float(p["ev_per_$1"])
                       for p in picks_lookup.values())
        lines.append("")
        lines.append("━" * 75)
        lines.append(f"  Total at risk:    ${total_stake:.2f}")
        lines.append(f"  Expected profit:  ${total_ev:+.2f}  "
                     f"(if you bet this every day, this is your average return)")
        lines.append("━" * 75)
    else:
        lines.append("")
        lines.append("  ⚫ No actionable bets today — model has no edge over the market.")

    # ── HIGH-CONVICTION (NO-BET) GAMES ──────────────────────────────────
    high_no_bet = [g for _, g in audit_sorted.iterrows()
                   if (g["away"], g["home"]) not in picks_lookup
                   and g["tier"] in ("DIAMOND", "PLATINUM", "GOLD")]
    if high_no_bet:
        lines.append("")
        lines.append("━" * 75)
        lines.append("  🟡 HIGH-CONFIDENCE PICKS WITH NO BET (market priced them in)")
        lines.append("━" * 75)
        for g in high_no_bet:
            render_game(g, picks_lookup, lines, features_lookup)

    # ── SKIPS ────────────────────────────────────────────────────────────
    skips = [g for _, g in audit_sorted.iterrows() if g["tier"] == "SKIP"]
    if skips:
        lines.append("")
        lines.append("━" * 75)
        lines.append("  ❌ GAMES TO SKIP")
        lines.append("━" * 75)
        for g in skips:
            render_game(g, picks_lookup, lines, features_lookup)

    out = "\n".join(lines)
    print(out)

    md_path = ROOT / f"slate_readable_{day:%Y-%m-%d}.md"
    md_path.write_text(out, encoding="utf-8")
    print(f"\n📄 Saved: {md_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
