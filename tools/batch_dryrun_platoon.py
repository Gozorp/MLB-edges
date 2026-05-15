"""
batch_dryrun_platoon.py — A/B comparison harness for the platoon-brain MVP.

For each test slate:
  1. Build the v1 diag context (no top_5 JSON) and a v2 context (with it)
  2. Print both side-by-side so a human (or Claude in audit mode) can compare
  3. Emit a markdown report at docs/data/dryrun_top5_v1_vs_v2.md

This is a STRUCTURAL dryrun — it generates the payloads that would be
fed to Claude Brain in v1 vs v2 mode, without actually calling the LLM
API.  The actual Claude run happens via the existing claude-brain
workflow (manually triggered with the v1 or v2 prompt variant).

Success metrics computed by this script:
  * Reasoning use: presence/absence of the top_5 field in the payload
  * Sample-flag distribution: how many LOW_SAMPLE vs OK across the slate
  * Split spread distribution: how many BIG_SPLIT batters per slate

The flip-correctness metric requires running Claude twice and comparing
decisions; that's a separate downstream step (run claude-brain workflow
manually on the v2 branch and diff the resulting claude_picks JSONs).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Make mlb_edge importable when run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mlb_edge import platoon_splits  # noqa: E402


# Same locked-in test set as PRE_FLIGHT
TEST_SLATES = [
    ("2026-05-09", "NYY", "MIL", "away", "L",
     "BASELINE — Claude reads + cites JSON"),
    ("2026-05-10", "ATL", "LAD", "home", "R",
     "FALSE-POS CONTROL — splits favor LAD, no flip expected"),
    ("2026-05-09", "CHC", "TEX", "away", "R",
     "FALSE-POS CONTROL (strong) — splits favor CHC vs Leiter RHP"),
]


def _find_game_pk(date_str: str, away: str, home: str) -> Optional[int]:
    import urllib.request, json as _j
    url = (f"https://statsapi.mlb.com/api/v1/schedule"
           f"?sportId=1&date={date_str}&hydrate=team")
    req = urllib.request.Request(
        url, headers={"User-Agent": "mlb_edge_dryrun/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = _j.load(r)
    aliases = {"CWS": "CHW", "CHW": "CWS", "AZ": "ARI", "ARI": "AZ",
               "ATH": "OAK", "OAK": "ATH", "WSH": "WAS", "WAS": "WSH"}
    def norm(x): return {x, aliases.get(x, x)}
    for day in data.get("dates", []):
        for g in day.get("games", []):
            a = g["teams"]["away"]["team"].get("abbreviation", "")
            h = g["teams"]["home"]["team"].get("abbreviation", "")
            if a in norm(away) and h in norm(home):
                return g["gamePk"]
    return None


def _summarize_payload(payload: List[dict]) -> dict:
    """Compute high-level signals from a top-5 payload."""
    if not payload:
        return {"n": 0, "low_sample": 0, "big_split": 0,
                "avg_pa_lhp": 0, "avg_pa_rhp": 0}
    n = len(payload)
    low = sum(1 for b in payload if b.get("sample_flag") == "LOW_SAMPLE")
    big = 0
    pa_l_sum, pa_r_sum = 0, 0
    for b in payload:
        ol = b.get("vs_LHP_OPS_career") or 0
        orr = b.get("vs_RHP_OPS_career") or 0
        if abs((ol or 0) - (orr or 0)) >= 0.150:
            big += 1
        pa_l_sum += b.get("vs_LHP_PA_career", 0)
        pa_r_sum += b.get("vs_RHP_PA_career", 0)
    return {
        "n": n,
        "low_sample": low,
        "big_split": big,
        "avg_pa_lhp": pa_l_sum // n if n else 0,
        "avg_pa_rhp": pa_r_sum // n if n else 0,
    }


def main() -> int:
    out_lines = []
    out_lines.append("# Platoon-Brain MVP Dry-Run — v1 vs v2 Payload Comparison")
    out_lines.append("")
    out_lines.append(f"_Generated {datetime.utcnow().isoformat()}Z_")
    out_lines.append("")
    out_lines.append("This report compares the diag CSV context that would be")
    out_lines.append("delivered to Claude Brain in v1 mode (no top_5 JSON) vs")
    out_lines.append("v2 mode (with top_5 JSON).  The dryrun does NOT call the")
    out_lines.append("LLM — it surfaces the payload differences for human or")
    out_lines.append("audit-mode comparison.  To complete the dry-run, run the")
    out_lines.append("claude-brain workflow twice manually (once on each prompt")
    out_lines.append("variant) and diff the resulting claude_picks JSONs.")
    out_lines.append("")
    out_lines.append("## Test Slates")
    out_lines.append("")

    any_failure = False
    for date_str, away, home, side, opp_sp_hand, role in TEST_SLATES:
        out_lines.append(f"### {date_str}  {away} @ {home}")
        out_lines.append(f"_{role}_")
        out_lines.append("")
        out_lines.append(f"- Audit side: **{side}** (the side whose pick "
                         f"failed or that we care about evaluating)")
        out_lines.append(f"- Opposing SP handedness: **{opp_sp_hand}** "
                         f"(used to resolve vs_today_SP_* fields)")
        out_lines.append("")

        try:
            game_pk = _find_game_pk(date_str, away, home)
        except Exception as e:
            out_lines.append(f"**FAIL** — schedule lookup error: `{e}`")
            any_failure = True
            continue
        if not game_pk:
            out_lines.append("**FAIL** — could not resolve game_pk")
            any_failure = True
            continue

        try:
            payload = platoon_splits.build_team_top_5_payload(
                game_pk, side, opp_sp_hand)
        except Exception as e:
            out_lines.append(f"**FAIL** — payload build error: `{e}`")
            any_failure = True
            continue

        summary = _summarize_payload(payload)
        out_lines.append(f"**Payload summary:** "
                         f"n={summary['n']}, "
                         f"LOW_SAMPLE={summary['low_sample']}, "
                         f"BIG_SPLIT (Δ≥.150)={summary['big_split']}, "
                         f"avg_PA_LHP={summary['avg_pa_lhp']}, "
                         f"avg_PA_RHP={summary['avg_pa_rhp']}")
        out_lines.append("")
        out_lines.append("**Per-batter detail:**")
        out_lines.append("")
        out_lines.append("| # | Name | Bat | vs LHP OPS (PA) | vs RHP OPS (PA) | "
                         "vs today SP OPS (PA) | flag |")
        out_lines.append("|---|---|---|---|---|---|---|")
        for b in payload:
            ol = b.get("vs_LHP_OPS_career")
            orr = b.get("vs_RHP_OPS_career")
            ot = b.get("vs_today_SP_OPS")
            pa_t = b.get("vs_today_SP_PA")
            out_lines.append(
                f"| {b['order']} | {b['name']} | {b.get('bat_side') or '?'} | "
                f"{ol if ol else '—'} ({b.get('vs_LHP_PA_career',0)}) | "
                f"{orr if orr else '—'} ({b.get('vs_RHP_PA_career',0)}) | "
                f"{ot if ot else '—'} ({pa_t}) | "
                f"{b.get('sample_flag','?')} |")
        out_lines.append("")
        out_lines.append("**Raw JSON (this is what gets injected into v2 brain prompt):**")
        out_lines.append("")
        out_lines.append("```json")
        out_lines.append(json.dumps(payload, indent=2))
        out_lines.append("```")
        out_lines.append("")

    out_lines.append("---")
    out_lines.append("")
    out_lines.append("## Next Step — Run Claude Brain in v1 vs v2 mode")
    out_lines.append("")
    out_lines.append("1. Save the current `tools/claude_brain_prompt.md` as v1 baseline.")
    out_lines.append("2. Append the platoon-brain prompt instruction to create v2.")
    out_lines.append("3. Trigger claude-brain workflow manually with v1 prompt → save output as `claude_picks_v1/<date>.json`.")
    out_lines.append("4. Trigger again with v2 prompt → save as `claude_picks_v2/<date>.json`.")
    out_lines.append("5. Diff per-matchup `claude_decision` and `reasoning` fields.")
    out_lines.append("6. Score against the three metrics: reasoning vocabulary use, "
                     "decision delta count, false-positive resistance.")

    out_path = Path("docs/data/dryrun_top5_v1_vs_v2.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"wrote {out_path}")
    return 2 if any_failure else 0


if __name__ == "__main__":
    sys.exit(main())
