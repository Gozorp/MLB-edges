# Cloud Re-Enable / Local Stand-Down — Pre-Japan Atomic Swap

**When:** ~24–48h before departure (≈ 6/21, per the `mlb-edge-reenable-cloud-before-japan`
reminder), NOT before — flipping early reintroduces the local-vs-cloud publish race the
06-03 cutover removed. Goal: exactly ONE publisher active at all times.

**Why:** laptop is off/unreliable during travel → local stops publishing → public freezes
unless cloud crons are back on. Re-enabling cloud also revives the `*_heartbeat` checks, so
`health.json` clears its known cutover false-RED.

---

## Step 1 — Re-enable the 6 cloud workflows  (GitHub → Actions → workflow → ··· → Enable)
Verified present with active crons in `.github/workflows/`:
- [ ] `daily-slate.yml`            (cron 0 6 UTC — bakes the slate + the 4 display sidecars)
- [ ] `self-learn.yml`             (cron 0 0,18 UTC — weights_state.json)
- [ ] `claude-brain.yml`           (cron 0 7 UTC — claude_picks/<date>.json)
- [ ] `claude-postgame.yml`        (cron 0 12 UTC — postgame/<date>.json)
- [ ] `savant-hitters-harvest.yml` (cron 0 11 UTC — savant_hitters_2026.csv)
- [ ] `savant-harvest.yml`         (cron 0 0 UTC — data/ harvest feeding the cloud bake)

**Leave ON / do NOT touch** (already correct): `pages-build-deployment` (serves the public UI),
`health-check.yml`, `bake-data.yml` (dispatch-only), and the weeklies
(`bvp-backtest`, `weekly-backtest`, `claude-weekly`, `refit-calibrator`, `umpire-refresh`).

## Step 2 — Re-enable the 2 Cowork scheduled tasks
- [ ] `mlb-edge-hourly-pipeline`        (the every-2h dispatcher behind cloud slate/self-learn)
- [ ] `mlb-edge-system-debug-4x-daily`  (autonomous fixer)

## Step 3 — Stand DOWN the local Windows tasks (kills the double-publish race)
Task Scheduler → Disable (NOT delete — trivially restored on return), or
`schtasks /change /tn <name> /disable`:
- [ ] `mlb_edge_refit`            (nightly chain — the local sole-publisher)
- [ ] `mlb_edge_slate_midday`
- [ ] `mlb_edge_brain`
- [ ] `mlb_edge_postgame`
- [ ] `mlb_edge_weekly_baseline`
- [ ] `mlb_edge_nightly_backstop`
- [ ] `mlb_edge_game_end_watcher`   (Running)
- [ ] `mlb_edge_live_event_watcher` (Running)

## Step 4 — Verify ONE full cloud cycle before you leave
- [ ] Trigger `daily-slate.yml` once via workflow_dispatch (don't wait for cron) → green run.
- [ ] Confirm a fresh commit lands on origin/main and the public site updates.
- [ ] Confirm `claude-brain.yml` ran and wrote a `claude_picks/<date>.json` (cloud OAuth ok).
- [ ] `health.json` overall trends GREEN as the heartbeats revive (give it a cycle).
- [ ] Confirm `CLAUDE_CODE_OAUTH_TOKEN` secret is present/valid in repo settings
      (brain/postgame run on the Max token — NEVER an ANTHROPIC_API_KEY).

## Step 5 — On return (~7/14, reminder `mlb-edge-postreturn-redisable-cloud`)
Symmetric reverse: re-disable the 6 cloud workflows + 2 Cowork tasks, re-enable the local
tasks (Step 3 list), restore `MAX_DAILY_RISK_UNITS` 10→15 (or keep), and clear
`data/state/weights_freeze.json` (auto-expires 7/20 anyway).

---

### Known caveats (not blockers)
- **Brain has no API-retry:** the cloud brain is the same code that hit `ECONNRESET` on
  06-12 and silently wrote no file. A rare day may have no brain picks. Accept for the trip;
  retry-hardening is a July item.
- **Atomic swap only:** never leave Steps 1–2 done with Step 3 undone (double publisher → race
  on origin/main). If you abort mid-swap, revert Step 1–2 rather than leave both live.
