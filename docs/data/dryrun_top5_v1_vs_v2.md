# Platoon-Brain MVP Dry-Run — v1 vs v2 Payload Comparison

_Generated 2026-05-15T02:37:37.616825Z_

This report compares the diag CSV context that would be
delivered to Claude Brain in v1 mode (no top_5 JSON) vs
v2 mode (with top_5 JSON).  The dryrun does NOT call the
LLM — it surfaces the payload differences for human or
audit-mode comparison.  To complete the dry-run, run the
claude-brain workflow twice manually (once on each prompt
variant) and diff the resulting claude_picks JSONs.

## Test Slates

### 2026-05-09  NYY @ MIL
_BASELINE — Claude reads + cites JSON_

- Audit side: **away** (the side whose pick failed or that we care about evaluating)
- Opposing SP handedness: **L** (used to resolve vs_today_SP_* fields)

**Payload summary:** n=5, LOW_SAMPLE=0, BIG_SPLIT (Δ≥.150)=0, avg_PA_LHP=892, avg_PA_RHP=2244

**Per-batter detail:**

| # | Name | Bat | vs LHP OPS (PA) | vs RHP OPS (PA) | vs today SP OPS (PA) | flag |
|---|---|---|---|---|---|---|
| 1 | Max Schuemann | R | 0.532 (179) | 0.634 (506) | 0.532 (179) | OK |
| 2 | Ben Rice | L | 0.771 (209) | 0.854 (659) | 0.771 (209) | OK |
| 3 | Aaron Judge | R | 1.062 (1299) | 1.017 (3897) | 1.062 (1299) | OK |
| 4 | Cody Bellinger | L | 0.809 (1536) | 0.823 (3414) | 0.809 (1536) | OK |
| 5 | Amed Rosario | R | 0.799 (1238) | 0.67 (2745) | 0.799 (1238) | OK |

**Raw JSON (this is what gets injected into v2 brain prompt):**

```json
[
  {
    "order": 1,
    "name": "Max Schuemann",
    "bat_side": "R",
    "vs_LHP_OPS_career": 0.532,
    "vs_LHP_PA_career": 179,
    "vs_RHP_OPS_career": 0.634,
    "vs_RHP_PA_career": 506,
    "vs_today_SP_OPS": 0.532,
    "vs_today_SP_PA": 179,
    "sample_flag": "OK"
  },
  {
    "order": 2,
    "name": "Ben Rice",
    "bat_side": "L",
    "vs_LHP_OPS_career": 0.771,
    "vs_LHP_PA_career": 209,
    "vs_RHP_OPS_career": 0.854,
    "vs_RHP_PA_career": 659,
    "vs_today_SP_OPS": 0.771,
    "vs_today_SP_PA": 209,
    "sample_flag": "OK"
  },
  {
    "order": 3,
    "name": "Aaron Judge",
    "bat_side": "R",
    "vs_LHP_OPS_career": 1.062,
    "vs_LHP_PA_career": 1299,
    "vs_RHP_OPS_career": 1.017,
    "vs_RHP_PA_career": 3897,
    "vs_today_SP_OPS": 1.062,
    "vs_today_SP_PA": 1299,
    "sample_flag": "OK"
  },
  {
    "order": 4,
    "name": "Cody Bellinger",
    "bat_side": "L",
    "vs_LHP_OPS_career": 0.809,
    "vs_LHP_PA_career": 1536,
    "vs_RHP_OPS_career": 0.823,
    "vs_RHP_PA_career": 3414,
    "vs_today_SP_OPS": 0.809,
    "vs_today_SP_PA": 1536,
    "sample_flag": "OK"
  },
  {
    "order": 5,
    "name": "Amed Rosario",
    "bat_side": "R",
    "vs_LHP_OPS_career": 0.799,
    "vs_LHP_PA_career": 1238,
    "vs_RHP_OPS_career": 0.67,
    "vs_RHP_PA_career": 2745,
    "vs_today_SP_OPS": 0.799,
    "vs_today_SP_PA": 1238,
    "sample_flag": "OK"
  }
]
```

### 2026-05-10  ATL @ LAD
_FALSE-POS CONTROL — splits favor LAD, no flip expected_

- Audit side: **home** (the side whose pick failed or that we care about evaluating)
- Opposing SP handedness: **R** (used to resolve vs_today_SP_* fields)

**Payload summary:** n=5, LOW_SAMPLE=0, BIG_SPLIT (Δ≥.150)=0, avg_PA_LHP=1369, avg_PA_RHP=3184

**Per-batter detail:**

| # | Name | Bat | vs LHP OPS (PA) | vs RHP OPS (PA) | vs today SP OPS (PA) | flag |
|---|---|---|---|---|---|---|
| 1 | Shohei Ohtani | L | 0.853 (1447) | 0.998 (3067) | 0.998 (3067) | OK |
| 2 | Freddie Freeman | L | 0.805 (2908) | 0.934 (6632) | 0.934 (6632) | OK |
| 3 | Andy Pages | R | 0.835 (298) | 0.748 (941) | 0.748 (941) | OK |
| 4 | Kyle Tucker | L | 0.843 (1141) | 0.869 (2198) | 0.869 (2198) | OK |
| 5 | Max Muncy | L | 0.788 (1054) | 0.847 (3082) | 0.847 (3082) | OK |

**Raw JSON (this is what gets injected into v2 brain prompt):**

```json
[
  {
    "order": 1,
    "name": "Shohei Ohtani",
    "bat_side": "L",
    "vs_LHP_OPS_career": 0.853,
    "vs_LHP_PA_career": 1447,
    "vs_RHP_OPS_career": 0.998,
    "vs_RHP_PA_career": 3067,
    "vs_today_SP_OPS": 0.998,
    "vs_today_SP_PA": 3067,
    "sample_flag": "OK"
  },
  {
    "order": 2,
    "name": "Freddie Freeman",
    "bat_side": "L",
    "vs_LHP_OPS_career": 0.805,
    "vs_LHP_PA_career": 2908,
    "vs_RHP_OPS_career": 0.934,
    "vs_RHP_PA_career": 6632,
    "vs_today_SP_OPS": 0.934,
    "vs_today_SP_PA": 6632,
    "sample_flag": "OK"
  },
  {
    "order": 3,
    "name": "Andy Pages",
    "bat_side": "R",
    "vs_LHP_OPS_career": 0.835,
    "vs_LHP_PA_career": 298,
    "vs_RHP_OPS_career": 0.748,
    "vs_RHP_PA_career": 941,
    "vs_today_SP_OPS": 0.748,
    "vs_today_SP_PA": 941,
    "sample_flag": "OK"
  },
  {
    "order": 4,
    "name": "Kyle Tucker",
    "bat_side": "L",
    "vs_LHP_OPS_career": 0.843,
    "vs_LHP_PA_career": 1141,
    "vs_RHP_OPS_career": 0.869,
    "vs_RHP_PA_career": 2198,
    "vs_today_SP_OPS": 0.869,
    "vs_today_SP_PA": 2198,
    "sample_flag": "OK"
  },
  {
    "order": 5,
    "name": "Max Muncy",
    "bat_side": "L",
    "vs_LHP_OPS_career": 0.788,
    "vs_LHP_PA_career": 1054,
    "vs_RHP_OPS_career": 0.847,
    "vs_RHP_PA_career": 3082,
    "vs_today_SP_OPS": 0.847,
    "vs_today_SP_PA": 3082,
    "sample_flag": "OK"
  }
]
```

### 2026-05-09  CHC @ TEX
_FALSE-POS CONTROL (strong) — splits favor CHC vs Leiter RHP_

- Audit side: **away** (the side whose pick failed or that we care about evaluating)
- Opposing SP handedness: **R** (used to resolve vs_today_SP_* fields)

**Payload summary:** n=5, LOW_SAMPLE=0, BIG_SPLIT (Δ≥.150)=0, avg_PA_LHP=1016, avg_PA_RHP=2867

**Per-batter detail:**

| # | Name | Bat | vs LHP OPS (PA) | vs RHP OPS (PA) | vs today SP OPS (PA) | flag |
|---|---|---|---|---|---|---|
| 1 | Nicky Lopez | L | 0.574 (549) | 0.633 (1830) | 0.633 (1830) | OK |
| 2 | Michael Conforto | L | 0.709 (1080) | 0.815 (3403) | 0.815 (3403) | OK |
| 3 | Alex Bregman | R | 0.856 (1634) | 0.833 (3888) | 0.833 (3888) | OK |
| 4 | Ian Happ | S | 0.708 (1173) | 0.822 (3465) | 0.822 (3465) | OK |
| 5 | Seiya Suzuki | R | 0.839 (645) | 0.813 (1751) | 0.813 (1751) | OK |

**Raw JSON (this is what gets injected into v2 brain prompt):**

```json
[
  {
    "order": 1,
    "name": "Nicky Lopez",
    "bat_side": "L",
    "vs_LHP_OPS_career": 0.574,
    "vs_LHP_PA_career": 549,
    "vs_RHP_OPS_career": 0.633,
    "vs_RHP_PA_career": 1830,
    "vs_today_SP_OPS": 0.633,
    "vs_today_SP_PA": 1830,
    "sample_flag": "OK"
  },
  {
    "order": 2,
    "name": "Michael Conforto",
    "bat_side": "L",
    "vs_LHP_OPS_career": 0.709,
    "vs_LHP_PA_career": 1080,
    "vs_RHP_OPS_career": 0.815,
    "vs_RHP_PA_career": 3403,
    "vs_today_SP_OPS": 0.815,
    "vs_today_SP_PA": 3403,
    "sample_flag": "OK"
  },
  {
    "order": 3,
    "name": "Alex Bregman",
    "bat_side": "R",
    "vs_LHP_OPS_career": 0.856,
    "vs_LHP_PA_career": 1634,
    "vs_RHP_OPS_career": 0.833,
    "vs_RHP_PA_career": 3888,
    "vs_today_SP_OPS": 0.833,
    "vs_today_SP_PA": 3888,
    "sample_flag": "OK"
  },
  {
    "order": 4,
    "name": "Ian Happ",
    "bat_side": "S",
    "vs_LHP_OPS_career": 0.708,
    "vs_LHP_PA_career": 1173,
    "vs_RHP_OPS_career": 0.822,
    "vs_RHP_PA_career": 3465,
    "vs_today_SP_OPS": 0.822,
    "vs_today_SP_PA": 3465,
    "sample_flag": "OK"
  },
  {
    "order": 5,
    "name": "Seiya Suzuki",
    "bat_side": "R",
    "vs_LHP_OPS_career": 0.839,
    "vs_LHP_PA_career": 645,
    "vs_RHP_OPS_career": 0.813,
    "vs_RHP_PA_career": 1751,
    "vs_today_SP_OPS": 0.813,
    "vs_today_SP_PA": 1751,
    "sample_flag": "OK"
  }
]
```

---

## Next Step — Run Claude Brain in v1 vs v2 mode

1. Save the current `tools/claude_brain_prompt.md` as v1 baseline.
2. Append the platoon-brain prompt instruction to create v2.
3. Trigger claude-brain workflow manually with v1 prompt → save output as `claude_picks_v1/<date>.json`.
4. Trigger again with v2 prompt → save as `claude_picks_v2/<date>.json`.
5. Diff per-matchup `claude_decision` and `reasoning` fields.
6. Score against the three metrics: reasoning vocabulary use, decision delta count, false-positive resistance.