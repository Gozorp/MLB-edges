"""Build-features verifier — runs build_historical_frame for a small scope
under whichever build_pipeline.py is currently installed and dumps the output
to the path passed on the command line.

Usage:
    python _mmap_verify.py OUT_PATH

Works with either OLD (closure) or NEW (mmap) variant of build_pipeline.py;
the orchestration is in the surrounding shell pipeline.
"""
from __future__ import annotations
import sys, time, logging
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("verify")

from mlb_edge.build_pipeline import build_historical_frame

out_path = sys.argv[1]
t0 = time.time()
df = build_historical_frame(2026, through=date(2026, 4, 5),
                            include_weather=True, use_cache=False)
elapsed = time.time() - t0

log.info("Built %d rows x %d cols in %.1fs", len(df), len(df.columns), elapsed)
df.to_parquet(out_path, index=False)
log.info("Wrote %s (size=%d bytes)", out_path, __import__('os').path.getsize(out_path))
print(f"ELAPSED_SECONDS={elapsed:.2f}")
