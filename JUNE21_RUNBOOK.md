# Travel Runbook — staged commands for the two milestones

Zero-friction copy-paste for logging back in. **Authority for *why* each step exists is
`CLOUD_MIGRATION_CHECKLIST.md`** — this file is just the exact commands. Two milestones, and
they are NOT the same date:

| Milestone | When | What |
|-----------|------|------|
| **A — Cloud swap** | **~6/21, 24–48h pre-departure** | hand publishing to the cloud; stand down local |
| **B — Return reverse** | **~7/14, on return** | hand it back to local; **decide** the risk-cap restore |

> The **risk-cap restore is a RETURN task (B), not 6/21.** The cap stays tight at 10 through
> travel on purpose. Don't run the restore bat before you're back.

---

## MILESTONE A — Cloud swap (~6/21). Do Steps 1→2→3 in one sitting, then 4.
Atomic swap: brief overlap is fine, a long gap with both sides off (no publisher) or both on
(double-publish race) is not. Complete the whole thing in one session.

### A1 — Enable the 6 cloud workflows
Scripted (needs `gh auth login` once, run from the repo):
```bash
for wf in daily-slate self-learn claude-brain claude-postgame savant-hitters-harvest savant-harvest; do
  gh workflow enable "$wf.yml"
done
gh workflow list   # confirm the 6 show "active"
```
UI fallback (no gh): GitHub → Actions → each workflow → ··· → **Enable**.
**Do NOT touch** `pages-build-deployment`, `health-check.yml`, `bake-data.yml`, or the weeklies.

### A2 — Re-enable the 2 Cowork scheduled tasks (manual — Cowork UI, can't script)
`mlb-edge-hourly-pipeline` and `mlb-edge-system-debug-4x-daily` → Resume/Enable.

### A3 — Stand down the 8 local Windows tasks (run in an ADMIN cmd/PowerShell)
```bat
for %T in (mlb_edge_refit mlb_edge_slate_midday mlb_edge_brain mlb_edge_postgame mlb_edge_weekly_baseline mlb_edge_nightly_backstop mlb_edge_game_end_watcher mlb_edge_live_event_watcher) do schtasks /change /tn "%T" /disable
schtasks /query /fo table | findstr /I "mlb_edge"
```
(If they live under a Task Scheduler folder, prefix the name, e.g. `/tn "\MLB\mlb_edge_refit"`.)

### A4 — Verify ONE full cloud cycle before you leave
```bash
gh workflow run daily-slate.yml          # don't wait for the 0 6 UTC cron
gh run list --workflow=daily-slate.yml --limit 1     # watch it go green
```
Then confirm: a fresh commit lands on `origin/main` + the public site updates; `claude-brain.yml`
wrote a `claude_picks/<today>.json` (cloud OAuth ok); `health.json` trends GREEN as heartbeats
revive; and the `CLAUDE_CODE_OAUTH_TOKEN` secret is present (brain/postgame use the Max token,
**never** an `ANTHROPIC_API_KEY`).

---

## MILESTONE B — On return (~7/14). Symmetric reverse + the cap decision.

### B1 — DECISION: restore the daily risk cap, or keep it tight?
Cap is at **10** (travel value). To restore to **15**:
```bat
PUSH_RISK_CAP_RESTORE.bat
```
(Idempotent; dry-run first with `python _patch_risk_cap_restore.py` to preview. If you'd rather
keep 10 as the new normal, just skip this — no action needed.)

### B2 — Re-enable the 8 local tasks
```bat
for %T in (mlb_edge_refit mlb_edge_slate_midday mlb_edge_brain mlb_edge_postgame mlb_edge_weekly_baseline mlb_edge_nightly_backstop mlb_edge_game_end_watcher mlb_edge_live_event_watcher) do schtasks /change /tn "%T" /enable
```

### B3 — Re-disable the 6 cloud workflows + 2 Cowork tasks (reverse of A1/A2)
```bash
for wf in daily-slate self-learn claude-brain claude-postgame savant-hitters-harvest savant-harvest; do
  gh workflow disable "$wf.yml"
done
```
Then pause the 2 Cowork tasks again. Same atomic-swap rule: one publisher at all times.

### B4 — Clear the model freeze
`data/state/weights_freeze.json` (auto-expires 7/20 anyway, but clear it to resume self-learn).
This is also when the **July post-freeze work** unlocks: the calibration bake-off
(`CALIBRATION_SPEC.md`), the incoherence read (`tools/incoherence_audit.py`), the totals rebuild
(`TOTALS_REBUILD_PLAN.md`), bullpen-fatigue study, etc.

---

### Caveats (from the checklist, not blockers)
- The cloud brain has **no API-retry** (same code that hit ECONNRESET 06-12); a rare day may
  have no brain picks. Accept for the trip.
- **Never** leave A1–A2 done with A3 undone for any length of time (double publisher → origin
  race). If you abort mid-swap, reverse A1–A2 rather than leave both live.
