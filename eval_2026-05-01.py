"""Post-mortem for 2026-05-01 slate. Pulls actuals from MLB Stats API,
joins with picks_diag, computes metrics, flags outliers, and produces
rolling baseline against prior days. Writes Markdown report."""
from __future__ import annotations

import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
from mlb_edge.stadiums import normalize_team

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
OUT_PATH = Path(r"D:\mlb_edge\eval_2026-05-01.md")
TARGET = date(2026, 5, 1)
BASELINE_DATES = [
    date(2026, 4, 25), date(2026, 4, 26), date(2026, 4, 27),
    date(2026, 4, 28), date(2026, 4, 29), date(2026, 4, 30),
]


def fetch_outcomes(d: date) -> pd.DataFrame:
    r = requests.get(
        SCHEDULE_URL,
        params={"sportId": 1, "date": d.isoformat(), "hydrate": "linescore"},
        timeout=20,
    )
    r.raise_for_status()
    rows = []
    for dd in r.json().get("dates", []):
        for g in dd.get("games", []):
            state = (g.get("status", {}) or {}).get("detailedState", "")
            if state not in ("Final", "Game Over", "Completed Early"):
                continue
            home = g.get("teams", {}).get("home", {})
            away = g.get("teams", {}).get("away", {})
            try:
                hr = int(home.get("score", 0))
                ar = int(away.get("score", 0))
            except (TypeError, ValueError):
                continue
            rows.append({
                "home": normalize_team(home.get("team", {}).get("name", "")),
                "away": normalize_team(away.get("team", {}).get("name", "")),
                "home_R": hr, "away_R": ar,
                "run_diff": abs(hr - ar),
            })
    return pd.DataFrame(rows)


def load_diag(d: date) -> pd.DataFrame:
    """Read picks_<d>_diag.csv with the correct column semantics.

    BUG FIX 2026-05-02: a prior version of this loader treated `p_model` as
    home-perspective, then re-derived p_pick by inverting for away picks —
    which was wrong because `p_model` is ALREADY pick-perspective per
    main_predict.build_diagnostic_table. The away-pick rows had their Brier
    computed against the wrong probability (1 - p_pick instead of p_pick).

    Diagnostic CSVs written from 2026-05-02 onward also include an explicit
    `pick_prob` alias column. This loader prefers `pick_prob` when present
    and falls back to `p_model` (which is the same value, just less clearly
    named).
    """
    p = Path("picks_{}_diag.csv".format(d.isoformat()))
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df = df.drop_duplicates(subset=["matchup"], keep="first").copy()
    parts = df["matchup"].str.split(" @ ", expand=True)
    df["away"] = parts[0].apply(normalize_team)
    df["home"] = parts[1].apply(normalize_team)
    df["pick"] = df["pick"].apply(normalize_team)
    # Pick-perspective probability — the value the model assigned to its pick.
    src_col = "pick_prob" if "pick_prob" in df.columns else "p_model"
    df["p_pick"] = pd.to_numeric(df[src_col], errors="coerce")
    df["fair_prob"] = pd.to_numeric(df["fair_prob"], errors="coerce")
    df["edge_pp"] = pd.to_numeric(df["edge_pp"], errors="coerce")
    df["tier"] = df["tier"].fillna("SKIP")
    # Cross-check (defensive): for rows where the diag also has full_prob
    # (= home-perspective) and pick is away, full_prob + p_pick should == 1.
    if "full_prob" in df.columns:
        check = df.copy()
        check["full_prob"] = pd.to_numeric(check["full_prob"], errors="coerce")
        check["pick_is_home"] = check["pick"] == check["home"]
        away_rows = check[(~check["pick_is_home"]) & check["full_prob"].notna()
                          & check["p_pick"].notna()]
        if not away_rows.empty:
            mismatch = (away_rows["full_prob"] + away_rows["p_pick"] - 1.0).abs()
            if mismatch.max() > 1e-3:
                raise ValueError(
                    f"Diag perspective mismatch on {d}: "
                    f"max |full_prob + p_pick - 1| = {mismatch.max():.4f} "
                    f"(>1e-3). Either main_predict changed the convention or "
                    f"the diag is corrupted.")
    return df


def compute_metrics(j: pd.DataFrame) -> dict:
    """Compute Brier / log-loss / hit-rate from PICK perspective.

    p_pick is the model's probability that the pick wins. pick_won is 1 iff
    the pick won the actual game. Both are pick-perspective so brier and
    log-loss compose without sign flips.
    """
    if j.empty:
        return {"n": 0}
    j = j.copy()
    p = j["p_pick"].clip(1e-6, 1 - 1e-6)
    j["home_won"] = (j["home_R"] > j["away_R"]).astype(int)
    j["pick_correct"] = ((j["pick"] == j["home"]) == (j["home_won"] == 1)).astype(int)
    j["pick_won"] = j["pick_correct"]   # alias for readability
    j["brier"] = (p - j["pick_won"]) ** 2
    j["log_loss"] = -(j["pick_won"] * p.apply(math.log)
                      + (1 - j["pick_won"]) * (1 - p).apply(math.log))
    return {
        "n": len(j),
        "brier": j["brier"].mean(),
        "log_loss": j["log_loss"].mean(),
        "hit_rate": j["pick_correct"].mean(),
        "avg_p_pick": j["p_pick"].mean(),
        "joined": j,
    }


def main():
    print(f"Pulling actuals for {TARGET}...")
    d = load_diag(TARGET)
    if d.empty:
        raise SystemExit(f"No diag at picks_{TARGET}_diag.csv")
    outs = fetch_outcomes(TARGET)
    j = d.merge(outs, on=["away", "home"], how="left")
    completed = j["home_R"].notna()
    n_completed = int(completed.sum())
    n_no_result = int((~completed).sum())
    j = j[completed].copy()

    metrics = compute_metrics(j)
    print(f"  n={metrics['n']}  hit_rate={metrics['hit_rate']:.3f}  "
          f"brier={metrics['brier']:.4f}  logloss={metrics['log_loss']:.4f}")

    full = metrics["joined"].copy()

    # Rolling baseline
    print("Pulling rolling baseline...")
    baseline_rows = []
    for bd in BASELINE_DATES:
        bd_diag = load_diag(bd)
        if bd_diag.empty:
            continue
        bd_outs = fetch_outcomes(bd)
        bj = bd_diag.merge(bd_outs, on=["away", "home"], how="inner")
        if bj.empty:
            continue
        m = compute_metrics(bj)
        baseline_rows.append({
            "date": bd.isoformat(), "n": m["n"], "hit_rate": m["hit_rate"],
            "brier": m["brier"], "log_loss": m["log_loss"],
            "avg_p_pick": m["avg_p_pick"],
        })
        print(f"  {bd}  n={m['n']}  hit={m['hit_rate']:.3f}  brier={m['brier']:.4f}")

    # Compose markdown
    lines = []
    lines.append(f"# Eval — 2026-05-01 slate post-mortem\n")
    lines.append(f"_Generated {datetime.now():%Y-%m-%d %H:%M}_  · "
                 f"actuals from MLB Stats API · diag from picks_2026-05-01_diag.csv\n")

    lines.append("## Headline\n")
    lines.append(f"- **{metrics['n']} games** scored & completed "
                 f"({n_no_result} on slate had no final result available)\n"
                 f"- **Hit rate: {metrics['hit_rate']:.1%}**\n"
                 f"- **Brier: {metrics['brier']:.4f}**  "
                 f"(coin-flip 0.2500; well-calibrated MLB ≈ 0.235)\n"
                 f"- **Log-loss: {metrics['log_loss']:.4f}**  "
                 f"(coin-flip 0.6931)\n"
                 f"- avg p_pick: {metrics['avg_p_pick']:.3f}")

    # Per-game results table
    lines.append("\n## Per-game results\n")
    lines.append("| matchup | pick | p_pick | tier | result | win? | brier |")
    lines.append("|---|---|---:|---|---|:---:|---:|")
    pretty = full.sort_values("p_pick", ascending=False)
    for _, r in pretty.iterrows():
        result = f"{int(r['away_R'])}-{int(r['home_R'])}"
        win_marker = "✅" if r["pick_correct"] else "❌"
        lines.append(f"| {r['away']} @ {r['home']} | **{r['pick']}** "
                     f"| {r['p_pick']:.3f} | {r['tier']} | {result} | "
                     f"{win_marker} | {r['brier']:.3f} |")

    # Tier segmentation
    lines.append("\n## By tier\n")
    lines.append("| tier | n | hit | brier | logloss | avg p_pick |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for tier, sub in full.groupby("tier"):
        m = compute_metrics(sub)
        lines.append(f"| {tier} | {m['n']} | {m['hit_rate']:.3f} | "
                     f"{m['brier']:.4f} | {m['log_loss']:.4f} | "
                     f"{m['avg_p_pick']:.3f} |")

    # Edge band segmentation — picks_diag for 05-01 had no fair_prob/edge,
    # but report tier groups instead. (Edge bands deferred when edge=NaN.)
    n_with_edge = full["edge_pp"].notna().sum()
    if n_with_edge > 0:
        lines.append("\n## By edge band\n")
        lines.append("| band (pp) | n | hit | brier | avg p_pick |")
        lines.append("|---|---:|---:|---:|---:|")
        bins = [(-100, 0), (0, 4), (4, 15), (15, 100)]
        labels = ["edge<0", "edge[0,4)", "edge[4,15] (gate band)", "edge>15"]
        for (lo, hi), lab in zip(bins, labels):
            sub = full[(full["edge_pp"] >= lo) & (full["edge_pp"] < hi)]
            if sub.empty:
                continue
            m = compute_metrics(sub)
            lines.append(f"| {lab} | {m['n']} | {m['hit_rate']:.3f} | "
                         f"{m['brier']:.4f} | {m['avg_p_pick']:.3f} |")
    else:
        lines.append("\n## By edge band\n")
        lines.append("_No edge data in picks_2026-05-01_diag.csv (fair_prob/edge_pp columns empty — "
                     "predict run wrote diag without odds devigging). Skipping edge-band table._")

    # Outliers
    lines.append("\n## Notable hits & misses\n")
    high_prob_losses = full[(full["p_pick"] >= 0.65) & (full["pick_correct"] == 0)]
    low_prob_wins   = full[(full["p_pick"] <= 0.55) & (full["pick_correct"] == 1)]
    if not high_prob_losses.empty:
        lines.append("**Confidence busts** (p_pick ≥ 65% but lost):")
        for _, r in high_prob_losses.iterrows():
            lines.append(f"- `{r['away']} @ {r['home']}`  pick={r['pick']} "
                         f"@ {r['p_pick']:.1%} ({r['tier']}) → final {int(r['away_R'])}-{int(r['home_R'])}")
    else:
        lines.append("**Confidence busts:** none (no pick ≥65% lost).")
    if not low_prob_wins.empty:
        lines.append("\n**Underdog wins** (model picked at <55% and pick won):")
        for _, r in low_prob_wins.iterrows():
            lines.append(f"- `{r['away']} @ {r['home']}`  pick={r['pick']} "
                         f"@ {r['p_pick']:.1%} ({r['tier']}) → final {int(r['away_R'])}-{int(r['home_R'])}")
    else:
        lines.append("\n**Underdog wins:** none.")

    # PLATINUM / GOLD called out separately
    plat_gold = full[full["tier"].isin(["PLATINUM", "DIAMOND", "GOLD"])]
    if not plat_gold.empty:
        lines.append("\n**High-conviction tier results** (PLATINUM/DIAMOND/GOLD):")
        for _, r in plat_gold.iterrows():
            mark = "✅" if r["pick_correct"] else "❌"
            lines.append(f"- {mark} `{r['away']} @ {r['home']}` "
                         f"{r['pick']} @ {r['p_pick']:.1%} · {r['tier']} → "
                         f"final {int(r['away_R'])}-{int(r['home_R'])}")

    # Rolling comparison
    lines.append("\n## Rolling baseline (prior 6 slates)\n")
    if baseline_rows:
        lines.append("| date | n | hit | brier | logloss | avg p_pick |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for b in baseline_rows:
            lines.append(f"| {b['date']} | {b['n']} | {b['hit_rate']:.3f} | "
                         f"{b['brier']:.4f} | {b['log_loss']:.4f} | "
                         f"{b['avg_p_pick']:.3f} |")
        # 7-day pooled
        all_n = sum(b["n"] for b in baseline_rows) + metrics["n"]
        b_brier = sum(b["brier"] * b["n"] for b in baseline_rows)
        b_loss = sum(b["log_loss"] * b["n"] for b in baseline_rows)
        b_hit = sum(b["hit_rate"] * b["n"] for b in baseline_rows)
        bn = sum(b["n"] for b in baseline_rows)
        if bn > 0:
            lines.append(f"| **prior pooled** | **{bn}** | "
                         f"**{b_hit/bn:.3f}** | **{b_brier/bn:.4f}** | "
                         f"**{b_loss/bn:.4f}** | — |")
        lines.append(f"| **2026-05-01** | **{metrics['n']}** | "
                     f"**{metrics['hit_rate']:.3f}** | **{metrics['brier']:.4f}** | "
                     f"**{metrics['log_loss']:.4f}** | "
                     f"**{metrics['avg_p_pick']:.3f}** |")

        delta_brier = metrics["brier"] - (b_brier / bn) if bn else 0
        delta_hit = metrics["hit_rate"] - (b_hit / bn) if bn else 0
        verdict = "in-line" if abs(delta_brier) < 0.02 else (
            "**worse** than baseline" if delta_brier > 0 else "**better** than baseline")
        lines.append(f"\n**Day vs. baseline:** Brier delta = {delta_brier:+.4f}, "
                     f"hit-rate delta = {delta_hit:+.3f} → 05-01 was {verdict}.")
    else:
        lines.append("_No baseline diag CSVs found for prior days._")

    # TBA-skipped games — read full schedule and diff against scored picks
    lines.append("\n## Games not scored at predict time\n")
    full_sched = fetch_outcomes(TARGET)
    diag_pairs = set(zip(full["away"], full["home"]))
    sched_pairs = set(zip(full_sched["away"], full_sched["home"]))
    not_scored = sched_pairs - diag_pairs
    if not_scored:
        for a, h in sorted(not_scored):
            row = full_sched[(full_sched["away"] == a) & (full_sched["home"] == h)].iloc[0]
            lines.append(f"- `{a} @ {h}` — final {int(row['away_R'])}-{int(row['home_R'])} "
                         f"(not in diag — TBA pitcher at predict time, not back-filled)")
    else:
        lines.append("- none — all completed games on the slate were scored.")

    text = "\n".join(lines) + "\n"
    OUT_PATH.write_text(text, encoding="utf-8")
    print(f"\nWrote {OUT_PATH} ({len(text):,} chars)")


if __name__ == "__main__":
    main()
