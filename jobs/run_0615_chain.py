import subprocess, datetime, os
ROOT = r"D:\mlb_edge\mlb_edge"
os.chdir(ROOT)
os.environ["PATH"] = r"C:\Python313;C:\Python313\Scripts;C:\Program Files\Git\cmd;C:\Program Files\Git\bin;" + os.environ.get("PATH", "")
PY = r"C:\Python313\python.exe"
SLATE = "2026-06-15"
LOG = os.path.join(ROOT, "logs", "run_0615.log")
open(LOG, "w", encoding="utf-8").close()

def run(args):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n==== %s : %s ====\n" % (datetime.datetime.now().isoformat(), " ".join(args)))
        f.flush()
        try:
            subprocess.run([PY] + args, stdout=f, stderr=subprocess.STDOUT, cwd=ROOT)
        except Exception as e:
            f.write("\n[orchestrator] step crashed: %r\n" % e)

for step in (
    ["tools/sweep_git_locks.py"],
    ["tools/run_local_slate.py", SLATE],
    ["tools/daily_variance_report.py", SLATE],
    ["tools/streak_indicator.py", SLATE],
    ["tools/sp_hr_recency.py", SLATE],
    ["tools/weather_runs.py", SLATE],
    ["tools/oos_ledger.py", SLATE],
    ["tools/skip_shadow_audit.py", SLATE],
    ["tools/team_tiers.py"],
    ["tools/spread_projection.py", SLATE],
    ["tools/sp_projection.py", SLATE],
    ["tools/provisional_lean.py", SLATE],
    ["tools/refit_post_calibrator.py"],
    ["tools/publish_local.py", "nightly"],
):
    run(step)

with open(LOG, "a", encoding="utf-8") as f:
    f.write("\n==== CHAIN DONE %s ====\n" % datetime.datetime.now().isoformat())
