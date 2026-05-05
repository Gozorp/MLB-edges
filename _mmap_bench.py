"""Benchmark mmap-optimized build for full 2025 season."""
from __future__ import annotations
import sys, time, logging
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bench")

from mlb_edge.build_pipeline import build_historical_frame

t0 = time.time()
df = build_historical_frame(2025, through=None, include_weather=True, use_cache=False)
elapsed = time.time() - t0
log.info("=== 2025 full season: %d rows x %d cols in %.1fs (%.2f min) ===",
         len(df), len(df.columns), elapsed, elapsed / 60.0)
print(f"BENCH_ELAPSED_SECONDS={elapsed:.2f}")
print(f"BENCH_ELAPSED_MIN={elapsed/60.0:.2f}")
print(f"BENCH_ROWS={len(df)}")
print(f"BENCH_COLS={len(df.columns)}")
