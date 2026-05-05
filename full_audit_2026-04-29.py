"""Full per-game audit for the 2026-04-28 slate.

Combines into one Markdown report:
  - matchup, pick, model_prob, fair_prob, edge_pp, EV/$, conviction tier
  - probable pitcher IDs, weather, lineup gaps, SP/bullpen sample sizes
  - news-override row (line move, bullpen-short, late scratch, etc.)
  - gate trail (model_prob band, fair_prob floor, edge band, tier stake)
  - SHAP feature-family decomposition + top 5 individual drivers
  - aggregate gate attrition + freshness flags

Output: full_audit_2026-04-28.md
"""
from __future__ import annotations

import sys
import glob
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Tuple

import joblib
import numpy as np
import pandas as pd
import requests
import xgboost as xgb

sys.path.insert(0, str(Path(__file__).parent))

from mlb_edge.build_pipeline import build_slate_frame
from mlb_edge.edge_calculator import score_conviction
from mlb_edge.market_analysis import shin
from mlb_edge.model import predict as mlb_predict
from mlb_edge.stadiums import normalize_team
from mlb_edge.config import (
    MIN_EDGE_PCT, MAX_EDGE_PCT, MIN_FAIR_PROB, MIN_MODEL_PROB, MAX_MODEL_PROB,
    TIER_SIZES,
)


# ---------------------------------------------------------------------------
# MLB Stats API — probable pitchers + per-pitcher season + last-3 game logs
# ---------------------------------------------------------------------------
def _fetch_sp_info(slate_date: date) -> Dict[Tuple[str, str], Dict[str, dict]]:
    """Return {(away_abbr, home_abbr): {"home": pitcher_dict, "away": pitcher_dict}}.

    Each pitcher dict: id, name, season (W,L,era,ip,k9,bb9,whip,hr9), last3 (list).
    Two API hits total: one schedule, one batched /people.
    """
    sched = requests.get(
        "https://statsapi.mlb.com/api/v1/schedule",
        params={"sportId": 1, "date": slate_date.isoformat(),
                "hydrate": "probablePitcher,team"},
        timeout=20,
    )
    sched.raise_for_status()
    sd = sched.json()
    games = []
    pitcher_ids: set = set()
    for d in sd.get("dates", []):
        for g in d.get("games", []):
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            home_abbr = normalize_team(home["team"]["name"])
            away_abbr = normalize_team(away["team"]["name"])
            home_pp = home.get("probablePitcher") or {}
            away_pp = away.get("probablePitcher") or {}
            entry = {
                "key": (away_abbr, home_abbr),
                "home": {"id": home_pp.get("id"), "name": home_pp.get("fullName")},
                "away": {"id": away_pp.get("id"), "name": away_pp.get("fullName")},
            }
            for side in ("home", "away"):
                pid = entry[side]["id"]
                if pid:
                    pitcher_ids.add(pid)
            games.append(entry)

    stats_by_id: Dict[int, dict] = {}
    if pitcher_ids:
        ids = ",".join(str(i) for i in sorted(pitcher_ids))
        people = requests.get(
            "https://statsapi.mlb.com/api/v1/people",
            params={"personIds": ids,
                    "hydrate": f"stats(group=[pitching],type=[season,gameLog],season={slate_date.year})"},
            timeout=30,
        )
        people.raise_for_status()
        for person in people.json().get("people", []):
            pid = person["id"]
            season = None
            log = []
            for s in person.get("stats", []):
                typ = (s.get("type") or {}).get("displayName", "")
                splits = s.get("splits", [])
                if typ == "season" and splits:
                    st = splits[0]["stat"]
                    season = {
                        "w": st.get("wins"), "l": st.get("losses"),
                        "era": st.get("era"), "ip": st.get("inningsPitched"),
                        "k9": st.get("strikeoutsPer9Inn"),
                        "bb9": st.get("walksPer9Inn"),
                        "whip": st.get("whip"),
                        "hr9": st.get("homeRunsPer9"),
                        "k": st.get("strikeOuts"), "bb": st.get("baseOnBalls"),
                        "hr": st.get("homeRuns"),
                        "starts": st.get("gamesStarted"),
                    }
                elif typ == "gameLog" and splits:
                    for sp in splits:
                        st = sp["stat"]
                        log.append({
                            "date": sp.get("date"),
                            "ip": st.get("inningsPitched"),
                            "er": st.get("earnedRuns"),
                            "k": st.get("strikeOuts"),
                            "bb": st.get("baseOnBalls"),
                            "h": st.get("hits"),
                            "hr": st.get("homeRuns"),
                            "pitches": st.get("numberOfPitches"),
                            "strikes": st.get("strikes"),
                        })
            log.sort(key=lambda x: x.get("date") or "")
            stats_by_id[pid] = {"season": season, "last3": log[-3:]}

    out: Dict[Tuple[str, str], Dict[str, dict]] = {}
    for g in games:
        for side in ("home", "away"):
            pid = g[side]["id"]
            if pid and pid in stats_by_id:
                g[side].update(stats_by_id[pid])
        out[g["key"]] = {"home": g["home"], "away": g["away"]}
    return out


def _format_sp_block(side_label: str, pitcher: dict | None, n_pitches) -> list[str]:
    """Markdown bullets describing one SP. Falls back gracefully when fields
    are missing or the pitcher is TBA."""
    lines = []
    if not pitcher or not pitcher.get("id"):
        lines.append(f"- **{side_label}: TBA** (no probable pitcher announced — MLB Stats API)")
        if pd.notna(n_pitches):
            lines.append(f"  - feature-frame n_pitches={float(n_pitches):.0f} (F1 floor 600)")
        return lines

    name = pitcher.get("name", "?")
    pid = pitcher.get("id", "")
    season = pitcher.get("season") or {}
    last3 = pitcher.get("last3") or []

    pitch_str = (f" · n_pitches={float(n_pitches):.0f}"
                 if pd.notna(n_pitches) else " · n_pitches=n/a")
    lines.append(f"- **{side_label}: {name}** (id `{pid}`){pitch_str}")

    # Season line
    def _f(v, fmt="{:.2f}"):
        if v is None or v in ("-.--", "*.**"):
            return "—"
        try:
            return fmt.format(float(v))
        except (TypeError, ValueError):
            return str(v)
    if season:
        ip = season.get("ip", "—")
        season_bits = (
            f"{season.get('w',0)}-{season.get('l',0)}"
            f" · ERA {_f(season.get('era'))}"
            f" · IP {ip}"
            f" · K/9 {_f(season.get('k9'))}"
            f" · BB/9 {_f(season.get('bb9'))}"
            f" · WHIP {_f(season.get('whip'))}"
            f" · HR/9 {_f(season.get('hr9'))}"
        )
        # FIP not exposed by Stats API; compute from components when possible
        try:
            ip_f = float(ip)
            k = float(season.get("k") or 0)
            bb = float(season.get("bb") or 0)
            hr = float(season.get("hr") or 0)
            if ip_f > 0:
                fip = (13 * hr + 3 * bb - 2 * k) / ip_f + 3.20
                season_bits += f" · FIP {fip:.2f}"
        except (TypeError, ValueError):
            pass
        lines.append(f"  - season: {season_bits}")
    else:
        lines.append("  - season: no stats available (MLB Stats API)")

    # Last 3 starts capsule
    if last3:
        total_ip = 0.0; total_er = 0; total_k = 0; total_bb = 0
        total_pitches = 0; total_strikes = 0
        bits = []
        for g in last3:
            try:
                total_ip += float(g.get("ip") or 0)
            except (TypeError, ValueError):
                pass
            total_er += int(g.get("er") or 0)
            total_k  += int(g.get("k")  or 0)
            total_bb += int(g.get("bb") or 0)
            total_pitches += int(g.get("pitches") or 0)
            total_strikes += int(g.get("strikes") or 0)
            bits.append(f"{g.get('date','?')}: {g.get('ip','—')} IP / "
                        f"{g.get('er','—')} ER / {g.get('k','—')} K / {g.get('bb','—')} BB")
        whiff_str = ""
        if total_pitches:
            whiff_str = f" · {total_strikes}/{total_pitches} strikes ({100*total_strikes/total_pitches:.0f}%)"
        lines.append(
            f"  - last {len(last3)} starts: total {total_ip:.1f} IP / "
            f"{total_er} ER / {total_k} K / {total_bb} BB{whiff_str}"
        )
        for b in bits:
            lines.append(f"    · {b}")
    else:
        lines.append("  - last 3 starts: no game logs returned (MLB Stats API)")

    lines.append("  - source: MLB Stats API `/people?personIds=…&hydrate=stats(group=[pitching])`")
    return lines

DAY = date(2026, 4, 29)
OUT_PATH = Path(f"full_audit_{DAY:%Y-%m-%d}.md")

FAMILIES = {
    "SP_matchup":  ["f5_model_output"],
    "SP_luck":     ["home_sp_luck", "away_sp_luck"],
    "Offense":     ["team_wrcplus_gap", "team_woba_gap",
                    "team_bbk_gap", "team_hardhit_gap",
                    "team_batter_run_value_gap", "team_whiff_rate_gap",
                    "team_blast_swing_gap",
                    "lineup_wrcplus_gap", "lineup_vs_sp_gap",
                    "lineup_hardhit_gap"],
    "Bullpen":     ["bullpen_siera_gap", "bullpen_fatigue_gap",
                    "bullpen_xwoba_gap", "bullpen_k_pct_gap",
                    "bullpen_bb_pct_gap", "bullpen_hardhit_gap"],
    "Park":        ["park_runs_factor", "park_hr_factor",
                    "wind_dir_park", "wind_out_mph", "temp_f",
                    "humidity_pct", "precip_prob"],
    "Ump_Catcher": ["home_ump_boost", "away_ump_boost",
                    "home_catcher_penalty", "away_catcher_penalty"],
    "Defense":     ["team_oaa_gap", "team_frv_gap"],
    "Context":     ["is_divisional", "tz_diff", "is_opener",
                    "is_quick_turnaround", "is_day_game",
                    "dow_sin", "dow_cos", "home_roof_type",
                    "sp_sample_reliability", "sp_ttop3_penalty_gap"],
}


def _flip_for_away(g):
    """Mirror of audit_2026-04-28.py: gap-style features must flip sign and
    home/away pitch-count fields must swap (not negate) when the pick is the
    away team, so score_conviction sees the perspective of the picked side.
    """
    p = g.copy()
    if g["model_prob"] >= 0.5:
        return p
    for col in ["sp_xera_gap", "team_woba_gap", "sp_k_bb_pct_gap",
                "sp_siera_gap", "sp_fip_gap",
                "bullpen_siera_gap", "bullpen_xwoba_gap",
                "bullpen_k_pct_gap", "bullpen_bb_pct_gap",
                "bullpen_hardhit_gap", "bullpen_fatigue_gap"]:
        if col in p:
            p[col] = -p[col]
    p["home_sp_luck"], p["away_sp_luck"] = p.get("away_sp_luck"), p.get("home_sp_luck")
    p["home_sp_n_pitches"], p["away_sp_n_pitches"] = (
        p.get("away_sp_n_pitches"), p.get("home_sp_n_pitches"))
    p["home_bullpen_n_pitches"], p["away_bullpen_n_pitches"] = (
        p.get("away_bullpen_n_pitches"), p.get("home_bullpen_n_pitches"))
    return p


def _gate_trail(p_model_pick, fair, edge, tier):
    """Reproduce mlb_edge.config gate logic so the audit explains exactly
    why each game did or didn't clear the bet sheet."""
    trail = []
    if not (MIN_MODEL_PROB <= p_model_pick <= MAX_MODEL_PROB):
        trail.append(f"FAIL model_prob {p_model_pick:.3f} outside [{MIN_MODEL_PROB},{MAX_MODEL_PROB}]")
    else:
        trail.append(f"PASS model_prob {p_model_pick:.3f} in band")
    if pd.isna(fair):
        trail.append("FAIL fair_prob unavailable")
    elif fair < MIN_FAIR_PROB:
        trail.append(f"FAIL fair_prob {fair:.3f} < {MIN_FAIR_PROB}")
    else:
        trail.append(f"PASS fair_prob {fair:.3f} >= {MIN_FAIR_PROB}")
    if pd.isna(edge):
        trail.append("FAIL edge unavailable")
    elif edge < MIN_EDGE_PCT:
        trail.append(f"FAIL edge {edge*100:+.2f}pp < {MIN_EDGE_PCT*100:.0f}pp")
    elif edge > MAX_EDGE_PCT:
        trail.append(f"FAIL edge {edge*100:+.2f}pp > {MAX_EDGE_PCT*100:.0f}pp (likely bad number)")
    else:
        trail.append(f"PASS edge {edge*100:+.2f}pp in [{MIN_EDGE_PCT*100:.0f},{MAX_EDGE_PCT*100:.0f}]")
    stake_mult = TIER_SIZES.get(tier, 0)
    if stake_mult == 0:
        trail.append(f"FAIL tier {tier} -> stake_mult=0")
    else:
        trail.append(f"PASS tier {tier} -> stake_mult={stake_mult}")
    return trail


def main():
    print(f"Building slate for {DAY}...")
    games = build_slate_frame(DAY, include_weather=True)
    print(f"Built {len(games)} games")

    models = joblib.load("models/latest.pkl")
    stage1, stage2 = models["stage1"], models["stage2"]
    games = mlb_predict(stage1, stage2, games)

    # SHAP contribs from Stage 2 (analyze_slate.py logic).
    games["f5_model_output"] = games.get("f5_prob", games["model_prob"])
    X = games[stage2.feature_cols].copy()
    booster = stage2.booster.get_booster()
    dmat = xgb.DMatrix(X.values, feature_names=list(X.columns))
    contribs = booster.predict(dmat, pred_contribs=True)
    bias = contribs[:, -1]
    shap = contribs[:, :-1]
    feat_names = list(X.columns)
    feat_idx = {f: i for i, f in enumerate(feat_names)}

    # Live diag (fair_prob/edge_pp from the predict pipeline run).
    diag_path = Path(f"picks_{DAY:%Y-%m-%d}_diag.csv")
    diag = pd.read_csv(diag_path) if diag_path.exists() else pd.DataFrame()

    # News overrides
    news_path = Path(f"picks_{DAY:%Y-%m-%d}_news_overrides.csv")
    news = pd.read_csv(news_path) if news_path.exists() else pd.DataFrame()

    # Probable pitchers + season + last-3 (MLB Stats API)
    print("Fetching probable pitchers + season/gameLog stats...")
    try:
        sp_info = _fetch_sp_info(DAY)
        print(f"  loaded SP stats for {sum(1 for v in sp_info.values() for s in v.values() if s.get('id'))} pitchers across {len(sp_info)} games")
    except Exception as e:
        print(f"  WARNING: SP stats fetch failed ({e}); cards will mark TBA")
        sp_info = {}

    out = []
    out.append(f"# Full Audit — MLB Slate {DAY:%A, %B %d, %Y}\n")
    out.append(f"_Generated {datetime.now():%Y-%m-%d %H:%M}_  · {len(games)} games scored\n")
    out.append("\n## Slate summary\n")

    summary_rows = []
    for i, (_, g) in enumerate(games.iterrows()):
        matchup = f"{g['away_team']} @ {g['home_team']}"
        p_home = float(g["model_prob"])
        pick = g["home_team"] if p_home >= 0.5 else g["away_team"]
        p_pick = p_home if p_home >= 0.5 else 1 - p_home
        m = diag[diag["matchup"] == matchup] if not diag.empty else pd.DataFrame()
        fair = float(m["fair_prob"].iloc[0]) if not m.empty and pd.notna(m["fair_prob"].iloc[0]) else np.nan
        edge = float(m["edge_pp"].iloc[0]) / 100 if not m.empty and pd.notna(m["edge_pp"].iloc[0]) else np.nan
        ev = float(m["ev_per_dollar"].iloc[0]) if not m.empty and pd.notna(m["ev_per_dollar"].iloc[0]) else np.nan
        conv = score_conviction(_flip_for_away(g))
        summary_rows.append({
            "matchup": matchup, "pick": pick,
            "p_pick": p_pick, "fair": fair, "edge_pp": edge * 100 if pd.notna(edge) else np.nan,
            "ev": ev, "tier": conv.tier,
            "stake_mult": TIER_SIZES.get(conv.tier, 0),
        })

    sdf = pd.DataFrame(summary_rows).sort_values("edge_pp", ascending=False, na_position="last")
    out.append("| matchup | pick | p_pick | fair | edge_pp | EV/$ | tier | stake× |")
    out.append("|---|---|---:|---:|---:|---:|---|---:|")
    for _, r in sdf.iterrows():
        fair_s = f"{r['fair']:.3f}" if pd.notna(r['fair']) else "—"
        edge_s = f"{r['edge_pp']:+.2f}" if pd.notna(r['edge_pp']) else "—"
        ev_s = f"{r['ev']:+.3f}" if pd.notna(r['ev']) else "—"
        out.append(f"| {r['matchup']} | **{r['pick']}** | {r['p_pick']:.3f} | {fair_s} | {edge_s} | {ev_s} | {r['tier']} | {r['stake_mult']} |")

    # Bet sheet
    bets = sdf[sdf["stake_mult"] > 0]
    bets = bets[bets["edge_pp"].between(MIN_EDGE_PCT * 100, MAX_EDGE_PCT * 100, inclusive="both")]
    bets = bets[bets["p_pick"].between(MIN_MODEL_PROB, MAX_MODEL_PROB, inclusive="both")]
    bets = bets[bets["fair"] >= MIN_FAIR_PROB]
    out.append("\n## Bet sheet\n")
    if bets.empty:
        out.append(f"**No plays clear all gates** (edge ∈ [{MIN_EDGE_PCT*100:.0f}, {MAX_EDGE_PCT*100:.0f}]pp, "
                   f"model_prob ∈ [{MIN_MODEL_PROB}, {MAX_MODEL_PROB}], fair_prob ≥ {MIN_FAIR_PROB}, tier with stake>0).")
    else:
        out.append("| matchup | pick | p_pick | edge_pp | EV/$ | tier | stake× |")
        out.append("|---|---|---:|---:|---:|---|---:|")
        for _, r in bets.iterrows():
            out.append(f"| {r['matchup']} | **{r['pick']}** | {r['p_pick']:.3f} | {r['edge_pp']:+.2f} | {r['ev']:+.3f} | {r['tier']} | {r['stake_mult']} |")

    # Per-game cards
    out.append("\n## Per-game cards\n")
    for i, (_, g) in enumerate(games.iterrows()):
        matchup = f"{g['away_team']} @ {g['home_team']}"
        p_home = float(g["model_prob"])
        pick = g["home_team"] if p_home >= 0.5 else g["away_team"]
        p_pick = p_home if p_home >= 0.5 else 1 - p_home
        m = diag[diag["matchup"] == matchup] if not diag.empty else pd.DataFrame()
        fair = float(m["fair_prob"].iloc[0]) if not m.empty and pd.notna(m["fair_prob"].iloc[0]) else np.nan
        edge = float(m["edge_pp"].iloc[0]) / 100 if not m.empty and pd.notna(m["edge_pp"].iloc[0]) else np.nan
        ev = float(m["ev_per_dollar"].iloc[0]) if not m.empty and pd.notna(m["ev_per_dollar"].iloc[0]) else np.nan
        f5_prob = float(m["f5_prob"].iloc[0]) if not m.empty and pd.notna(m["f5_prob"].iloc[0]) else np.nan
        conv = score_conviction(_flip_for_away(g))

        out.append(f"\n### {matchup} → **{pick}** ({p_pick:.1%}) · {conv.tier}\n")
        out.append("**Headline numbers**\n")
        line = (f"- p_model={p_home:.4f} (home) · p_pick={p_pick:.4f} · "
                f"f5_prob={f5_prob:.4f} · " if pd.notna(f5_prob) else
                f"- p_model={p_home:.4f} (home) · p_pick={p_pick:.4f} · ")
        line += f"fair_prob={fair:.4f}" if pd.notna(fair) else "fair_prob=n/a"
        out.append(line)
        out.append(f"- edge_pp={edge*100:+.2f}pp · EV/$={ev:+.4f}" if pd.notna(edge) else "- edge / EV unavailable")
        out.append(f"- conviction tier **{conv.tier}** · stake_mult={TIER_SIZES.get(conv.tier, 0)}")
        if conv.signals_fired:
            out.append(f"- signals: {', '.join(conv.signals_fired)}")
        if conv.notes:
            out.append(f"- suppression / notes: {' | '.join(conv.notes)}")

        # Probable pitchers (with season line + last-3 capsule)
        out.append("\n**Probable pitchers**")
        sp_pair = sp_info.get(
            (normalize_team(g["away_team"]), normalize_team(g["home_team"])), {}
        )
        out.extend(_format_sp_block(
            f"{g['home_team']} (home)", sp_pair.get("home"),
            g.get("home_sp_n_pitches", np.nan),
        ))
        out.extend(_format_sp_block(
            f"{g['away_team']} (away)", sp_pair.get("away"),
            g.get("away_sp_n_pitches", np.nan),
        ))

        out.append("\n**Lineup gaps**")
        any_lineup = False
        for k in ["lineup_wrcplus_gap", "lineup_vs_sp_gap", "lineup_hardhit_gap"]:
            v = g.get(k)
            if pd.notna(v):
                out.append(f"- {k}={float(v):+.3f}")
                any_lineup = True
        if not any_lineup:
            out.append("- no lineup gap features in slate frame for this game")

        # Bullpen state
        out.append("\n**Bullpen state (72h)**")
        out.append(f"- home bp pitches: {g.get('home_bullpen_n_pitches', np.nan):.0f} (F5 floor 3000)"
                   if pd.notna(g.get('home_bullpen_n_pitches')) else "- home bp pitches: n/a")
        out.append(f"- away bp pitches: {g.get('away_bullpen_n_pitches', np.nan):.0f} (F5 floor 3000)"
                   if pd.notna(g.get('away_bullpen_n_pitches')) else "- away bp pitches: n/a")
        for k in ["bullpen_siera_gap", "bullpen_fatigue_gap", "bullpen_xwoba_gap"]:
            v = g.get(k)
            if pd.notna(v):
                out.append(f"- {k}={float(v):+.3f}")

        # Weather
        out.append("\n**Weather / park**")
        wx_bits = []
        for k, fmt in [("temp_f", "{:.0f}°F"), ("wind_out_mph", "{:+.1f}mph out"),
                       ("wind_dir_park", "{:.0f}° wind"), ("humidity_pct", "{:.0f}% RH"),
                       ("precip_prob", "{:.0f}% precip"),
                       ("park_runs_factor", "park_runs={:.3f}"),
                       ("park_hr_factor", "park_hr={:.3f}"),
                       ("home_roof_type", "roof={:.0f}")]:
            v = g.get(k)
            if pd.notna(v):
                wx_bits.append(fmt.format(float(v)))
        out.append("- " + " · ".join(wx_bits) if wx_bits else "- weather unavailable")

        # News overrides — only emit a section if at least one rule fired.
        if not news.empty:
            n = news[news["matchup"] == matchup]
            if not n.empty:
                nr = n.iloc[0]
                bullets = []
                rationale = nr.get("news_rationale")
                if pd.notna(rationale) and str(rationale).strip():
                    bullets.append(f"- rationale: {rationale}")
                rules = nr.get("news_rules_fired")
                if pd.notna(rules) and str(rules).strip():
                    bullets.append(f"- rules: {rules}")
                bps = nr.get("news_line_move_home_bps", 0)
                if pd.notna(bps) and bps != 0:
                    bullets.append(f"- line move (home): {int(bps)} bps")
                if pd.notna(nr.get("news_model_prob_delta")) and nr["news_model_prob_delta"] != 0:
                    bullets.append(f"- model_prob delta: {float(nr['news_model_prob_delta']):+.3f}")
                if str(nr.get("news_sp_late_scratch_home")) == "True":
                    bullets.append("- **late scratch (home SP)**")
                if str(nr.get("news_sp_late_scratch_away")) == "True":
                    bullets.append("- **late scratch (away SP)**")
                if str(nr.get("news_bullpen_short_home")) == "True":
                    bullets.append("- bullpen short (home)")
                if str(nr.get("news_bullpen_short_away")) == "True":
                    bullets.append("- bullpen short (away)")
                if bullets:
                    out.append("\n**News override**")
                    out.extend(bullets)

        # Gate trail
        p_pick_for_gate = p_home if p_home >= 0.5 else 1 - p_home
        out.append("\n**Gate trail (bet sheet)**")
        for step in _gate_trail(p_pick_for_gate, fair, edge, conv.tier):
            out.append(f"- {step}")

        # SHAP feature-family
        out.append("\n**SHAP feature-family contributions** (positive = favors home)")
        out.append("| family | logit | pp@.5 | direction |")
        out.append("|---|---:|---:|---|")
        family_total = 0.0
        for fam, cols in FAMILIES.items():
            present = [c for c in cols if c in feat_idx]
            if not present:
                continue
            fam_logit = float(sum(shap[i, feat_idx[c]] for c in present))
            family_total += fam_logit
            pp = fam_logit * 25.0
            direction = (g["home_team"] if fam_logit > 0.02 else
                         g["away_team"] if fam_logit < -0.02 else "—")
            out.append(f"| {fam} | {fam_logit:+.3f} | {pp:+.1f}pp | {direction} |")
        out.append(f"| **net (ex-bias)** | **{family_total:+.3f}** | **{family_total*25:+.1f}pp** | bias={bias[i]:+.3f} |")

        # Top 5 individual drivers
        out.append("\n**Top 5 individual drivers** (|logit| desc)")
        out.append("| logit | feature | raw | favors |")
        out.append("|---:|---|---:|---|")
        idx_sorted = np.argsort(-np.abs(shap[i]))
        shown = 0
        for k in idx_sorted:
            if shown >= 5:
                break
            feat = feat_names[k]
            lc = float(shap[i, k])
            if abs(lc) < 0.005:
                break
            raw = g.get(feat, np.nan)
            favors = g["home_team"] if lc > 0 else g["away_team"]
            raw_s = f"{float(raw):+.3f}" if pd.notna(raw) else "n/a"
            out.append(f"| {lc:+.3f} | `{feat}` | {raw_s} | {favors} |")
            shown += 1

    # Aggregate gate attrition
    out.append("\n## Gate attrition\n")
    fail_counts = {"model_prob_out_of_band": 0, "fair_too_low": 0,
                   "edge_too_small": 0, "edge_too_big": 0,
                   "tier_no_stake": 0, "BET": 0}
    for r in summary_rows:
        p_pick = r["p_pick"]
        if not (MIN_MODEL_PROB <= p_pick <= MAX_MODEL_PROB):
            fail_counts["model_prob_out_of_band"] += 1; continue
        if pd.isna(r["fair"]) or r["fair"] < MIN_FAIR_PROB:
            fail_counts["fair_too_low"] += 1; continue
        if pd.isna(r["edge_pp"]) or r["edge_pp"] < MIN_EDGE_PCT * 100:
            fail_counts["edge_too_small"] += 1; continue
        if r["edge_pp"] > MAX_EDGE_PCT * 100:
            fail_counts["edge_too_big"] += 1; continue
        if r["stake_mult"] == 0:
            fail_counts["tier_no_stake"] += 1; continue
        fail_counts["BET"] += 1
    out.append("| gate result | n |")
    out.append("|---|---:|")
    for k, v in fail_counts.items():
        out.append(f"| {k} | {v} |")

    # Freshness sanity
    out.append("\n## Freshness sanity\n")
    today_mid = datetime(DAY.year, DAY.month, DAY.day, 0, 0).timestamp()
    savant_dirs = sorted(glob.glob("data/savant/*"))
    n_savant = n_today = 0
    stale = []
    import os
    for d_ in savant_dirs:
        if not os.path.isdir(d_):
            continue
        csvs = sorted(glob.glob(os.path.join(d_, "*.csv")))
        if not csvs:
            continue
        n_savant += 1
        latest = max(csvs, key=os.path.getmtime)
        mt = os.path.getmtime(latest)
        if mt >= today_mid:
            n_today += 1
        else:
            stale.append((os.path.basename(d_), os.path.basename(latest),
                          datetime.fromtimestamp(mt).isoformat(timespec="seconds")))
    out.append(f"- Savant categories with today's mtime: **{n_today}/{n_savant}**")
    bat = sorted(glob.glob("data/savant_bat_tracking/*.csv"))
    if bat:
        latest = max(bat, key=os.path.getmtime)
        out.append(f"- Bat-tracking latest: `{os.path.basename(latest)}` "
                   f"({datetime.fromtimestamp(os.path.getmtime(latest)):%Y-%m-%d %H:%M})")
    yesterday = (DAY - pd.Timedelta(days=1)).strftime("%Y%m%d")
    boxes = sorted(glob.glob(f"data/bref/boxes/bref_boxscore_*{yesterday}*.json"))
    out.append(f"- B-R boxes for {DAY - pd.Timedelta(days=1):%Y-%m-%d}: **{len(boxes)}**")
    stand = sorted(glob.glob(f"data/bref/standings/{DAY:%Y%m%d}_upto-*.csv"))
    out.append(f"- B-R standings for {DAY:%Y-%m-%d}: **{len(stand)}**")
    if stale:
        out.append("\n_Stale Savant categories (mtime before today):_")
        for cat, fn, ts in stale[:10]:
            out.append(f"  - {cat}/{fn}  mtime={ts}")

    # Done
    text = "\n".join(out) + "\n"
    OUT_PATH.write_text(text, encoding="utf-8")
    print(f"\nWrote {OUT_PATH} ({len(text):,} chars)")


if __name__ == "__main__":
    main()
