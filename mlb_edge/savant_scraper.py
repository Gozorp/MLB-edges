"""
savant_scraper.py
-----------------
Comprehensive Baseball Savant Statcast leaderboard scraper.  Pulls all
42 active leaderboards directly from the public ``?csv=true`` endpoints
that Savant honors — no headless browser needed.

ENDPOINT INVENTORY (42 leaderboards across 8 categories)
========================================================
Source: https://baseballsavant.mlb.com/leaderboard/statcast (enumerated
from the navigation tree on 2026-05-07 via web crawl).

Every endpoint is verified to return CSV when ``csv=true`` is appended.
A minority occasionally serve HTML instead (Savant intermittently
breaks the parameter on some leaderboard refresh deploys); those get
caught by the per-spec validator (header must contain commas, file
must exceed ``min_bytes``).  When validation fails, the writer rolls
back to the prior day's snapshot rather than poison the consuming
pipeline.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "mlb_edge/v14 (+https://github.com/mlb_edge/scraper)"
)


@dataclass(frozen=True)
class EndpointSpec:
    name: str                                  # short token used in filenames + logs
    url: str                                   # full URL with {year} placeholder
    out_dir: Path                              # destination directory
    out_filename: str                          # filename template w/ {year}_{ymd} substitutions
    min_bytes: int = 5_000                     # validation threshold
    timeout: int = 60


def _spec_with_year(s: EndpointSpec, year: int) -> Tuple[str, str, Path]:
    today = date.today().strftime("%Y%m%d")
    fname = s.out_filename.format(year=year, ymd=today)
    return s.url.format(year=year), fname, s.out_dir


# ---------------------------------------------------------------------------
# Endpoint registry
# ---------------------------------------------------------------------------
# LOAD_CRITICAL = endpoints the build pipeline depends on (predict-time
# reads will be impaired without a fresh snapshot).  SUPPLEMENTARY = used
# when present, fall back to prior snapshot when missing.
# ---------------------------------------------------------------------------

LOAD_CRITICAL: List[EndpointSpec] = [
    # ===== BATTING =====
    EndpointSpec(
        name="bat-tracking",
        url=("https://baseballsavant.mlb.com/leaderboard/bat-tracking?"
             "attr=&min=q&type=batter&pitchHand=&bats=&hp_x=&hp_y=&team="
             "&season=&year={year}&csv=true"),
        out_dir=Path("data/savant_bat_tracking"),
        out_filename="bat_tracking_{year}_{ymd}.csv",
    ),
    EndpointSpec(
        name="expected-stats-batter",
        url=("https://baseballsavant.mlb.com/leaderboard/expected_statistics?"
             "type=batter&year={year}&position=&team=&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_expected-stats-batter_{ymd}.csv",
    ),
    EndpointSpec(
        name="expected-stats-pitcher",
        url=("https://baseballsavant.mlb.com/leaderboard/expected_statistics?"
             "type=pitcher&year={year}&position=&team=&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_expected-stats-pitcher_{ymd}.csv",
    ),
    # ===== DEFENSE =====
    EndpointSpec(
        name="outs-above-average",
        url=("https://baseballsavant.mlb.com/leaderboard/outs_above_average?"
             "type=Fielder&startYear={year}&endYear={year}&split=no&team="
             "&range=year&min=q&pos=&roles=&viz=hide&csv=true"),
        out_dir=Path("data/savant/outs-above-average"),
        out_filename="outs-above-average_{ymd}.csv",
    ),
    EndpointSpec(
        name="fielding-run-value",
        # Savant changed this endpoint's canonical params on 2026-05-09:
        # `?year=Y&csv=true` now 301-redirects to
        # `?type=fielder&seasonStart=Y&seasonEnd=Y` and Cloudflare drops the
        # `csv=true` query during the redirect, so the response comes back as
        # HTML and the CSV-header validator rejects it (run 25617120817).
        # Pin the new param shape directly so we never depend on the redirect
        # preserving query strings.
        url=("https://baseballsavant.mlb.com/leaderboard/fielding-run-value?"
             "type=fielder&seasonStart={year}&seasonEnd={year}&csv=true"),
        out_dir=Path("data/savant/fielding-run-value"),
        out_filename="fielding-run-value_{ymd}.csv",
    ),
]

SUPPLEMENTARY: List[EndpointSpec] = [
    # ===== BATTING (supplementary) =====
    EndpointSpec(
        name="bat-tracking-swing-path",
        url=("https://baseballsavant.mlb.com/leaderboard/bat-tracking/"
             "swing-path-attack-angle?type=batter&min=q&year={year}&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_bat-tracking-swing-path_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="batted-ball",
        url=("https://baseballsavant.mlb.com/leaderboard/batted-ball?"
             "type=batter&year={year}&team=&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_batted-ball-batter_{ymd}.csv",
    ),
    EndpointSpec(
        name="batted-ball-pitcher",
        url=("https://baseballsavant.mlb.com/leaderboard/batted-ball?"
             "type=pitcher&year={year}&team=&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_batted-ball-pitcher_{ymd}.csv",
    ),
    EndpointSpec(
        name="home-runs-batter",
        url=("https://baseballsavant.mlb.com/leaderboard/home-runs?"
             "type=batter&year={year}&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_home-runs-batter_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="home-runs-pitcher",
        url=("https://baseballsavant.mlb.com/leaderboard/home-runs?"
             "type=pitcher&year={year}&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_home-runs-pitcher_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="exit-velocity-barrels",
        url=("https://baseballsavant.mlb.com/leaderboard/statcast?"
             "type=batter&year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_exit-velocity-barrels_{ymd}.csv",
    ),
    EndpointSpec(
        name="exit-velocity-barrels-pitcher",
        url=("https://baseballsavant.mlb.com/leaderboard/statcast?"
             "type=pitcher&year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_exit-velocity-barrels-pitcher_{ymd}.csv",
    ),
    EndpointSpec(
        name="percentile-rankings",
        url=("https://baseballsavant.mlb.com/leaderboard/percentile-rankings?"
             "type=batter&year={year}&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_percentile-rankings-batter_{ymd}.csv",
    ),
    EndpointSpec(
        name="percentile-rankings-pitcher",
        url=("https://baseballsavant.mlb.com/leaderboard/percentile-rankings?"
             "type=pitcher&year={year}&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_percentile-rankings-pitcher_{ymd}.csv",
    ),
    EndpointSpec(
        name="statcast-year-to-year",
        url=("https://baseballsavant.mlb.com/leaderboard/statcast-year-to-year?"
             "type=batter&year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_year-to-year-batter_{ymd}.csv",
    ),
    EndpointSpec(
        name="swing-take",
        url=("https://baseballsavant.mlb.com/leaderboard/swing-take?"
             "year={year}&team=&min=q&type=batter&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_swing-take_{ymd}.csv",
        min_bytes=1_500,
    ),

    # ===== PITCHING =====
    EndpointSpec(
        name="pitch-arsenal-stats",
        url=("https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats?"
             "year={year}&team=&min=q&hand=&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_pitch-arsenal-stats_{ymd}.csv",
    ),
    EndpointSpec(
        name="pitch-arsenals",
        url=("https://baseballsavant.mlb.com/leaderboard/pitch-arsenals?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_pitch-arsenals_{ymd}.csv",
    ),
    EndpointSpec(
        name="pitch-movement",
        url=("https://baseballsavant.mlb.com/leaderboard/pitch-movement?"
             "year={year}&team=&min=q&hand=&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_pitch-movement_{ymd}.csv",
    ),
    EndpointSpec(
        name="pitch-tempo",
        url=("https://baseballsavant.mlb.com/leaderboard/pitch-tempo?"
             "year={year}&team=&min=q&hand=&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_pitch-tempo_{ymd}.csv",
    ),
    EndpointSpec(
        name="active-spin",
        url=("https://baseballsavant.mlb.com/leaderboard/active-spin?"
             "year={year}&min=q&hand=&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_active-spin_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="spin-direction",
        url=("https://baseballsavant.mlb.com/leaderboard/spin-direction-pitches?"
             "year={year}&min=q&hand=&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_spin-direction_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="pitcher-arm-angles",
        url=("https://baseballsavant.mlb.com/leaderboard/pitcher-arm-angles?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_pitcher-arm-angles_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="pitch-timer-infractions",
        url=("https://baseballsavant.mlb.com/leaderboard/pitch-timer-infractions?"
             "year={year}&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_pitch-timer-infractions_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="pitcher-running-game",
        url=("https://baseballsavant.mlb.com/leaderboard/pitcher-running-game?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_pitcher-running-game_{ymd}.csv",
        min_bytes=1_500,
    ),

    # ===== DEFENSE (supplementary) =====
    EndpointSpec(
        name="catch-probability",
        url=("https://baseballsavant.mlb.com/leaderboard/catch_probability?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_catch-probability_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="outfield-jump",
        url=("https://baseballsavant.mlb.com/leaderboard/outfield_jump?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_outfield-jump_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="outfield-directional-oaa",
        url=("https://baseballsavant.mlb.com/leaderboard/outfield_directional_outs_above_average?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_outfield-directional-oaa_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="arm-strength",
        url=("https://baseballsavant.mlb.com/leaderboard/arm-strength?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_arm-strength_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="catcher-framing",
        url=("https://baseballsavant.mlb.com/leaderboard/catcher-framing?"
             "year={year}&team=&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_catcher-framing_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="catcher-blocking",
        url=("https://baseballsavant.mlb.com/leaderboard/catcher-blocking?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_catcher-blocking_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="catcher-throwing",
        url=("https://baseballsavant.mlb.com/leaderboard/catcher-throwing?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_catcher-throwing_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="catcher-stance",
        url=("https://baseballsavant.mlb.com/leaderboard/catcher-stance?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_catcher-stance_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="poptime",
        url=("https://baseballsavant.mlb.com/leaderboard/poptime?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_poptime_{ymd}.csv",
        min_bytes=1_500,
    ),

    # ===== BASERUNNING =====
    EndpointSpec(
        name="sprint-speed",
        url=("https://baseballsavant.mlb.com/leaderboard/sprint_speed?"
             "year={year}&team=&min=q&position=&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_sprint-speed_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="baserunning-run-value",
        url=("https://baseballsavant.mlb.com/leaderboard/baserunning-run-value?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_baserunning-run-value_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="basestealing-run-value",
        url=("https://baseballsavant.mlb.com/leaderboard/basestealing-run-value?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_basestealing-run-value_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="baserunning",
        url=("https://baseballsavant.mlb.com/leaderboard/baserunning?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_baserunning_{ymd}.csv",
        min_bytes=1_500,
    ),
    EndpointSpec(
        name="running-splits",
        url=("https://baseballsavant.mlb.com/leaderboard/running_splits?"
             "year={year}&min=q&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_running-splits_{ymd}.csv",
        min_bytes=1_500,
    ),

    # ===== PARK / ENVIRONMENT =====
    EndpointSpec(
        name="park-factors",
        url=("https://baseballsavant.mlb.com/leaderboard/statcast-park-factors?"
             "type=year&year={year}&batSide=&stat=index_wOBA&condition=All&"
             "rolling=&csv=true"),
        out_dir=Path("data/savant/statcast-park-factors"),
        out_filename="park-factors_{year}_{ymd}.csv",
        min_bytes=1_500,
    ),

    # ===== ABS CHALLENGES =====
    EndpointSpec(
        name="abs-challenges",
        url=("https://baseballsavant.mlb.com/leaderboard/abs-challenges?"
             "year={year}&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_abs-challenges_{ymd}.csv",
        min_bytes=1_500,
    ),

    # ===== CUSTOM LEADERBOARD =====
    EndpointSpec(
        name="custom",
        url=("https://baseballsavant.mlb.com/leaderboard/custom?"
             "year={year}&csv=true"),
        out_dir=Path("data/savant_extra"),
        out_filename="savant_custom_{ymd}.csv",
        min_bytes=1_500,
    ),
]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate_csv(path: Path, min_bytes: int) -> Tuple[bool, str]:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return False, "missing"
    if size < min_bytes:
        return False, f"too small ({size} bytes < {min_bytes})"
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        header = f.readline().rstrip("\n")
        if not header:
            return False, "empty header"
        if header.count(",") < 2:
            return False, f"only {header.count(',')} commas in header"
        first_data = f.readline().rstrip("\n")
        if not first_data:
            return False, "no data rows"
    return True, "ok"


# ---------------------------------------------------------------------------
# Single-endpoint download
# ---------------------------------------------------------------------------
def _atomic_write(url: str, out_path: Path, timeout: int,
                  max_attempts: int = 3) -> bool:
    """Stream `url` to `out_path` atomically, with retry/backoff on transient
    network/server errors. Each retry sleeps `2 ** (attempt-1)` seconds, so
    1s, 2s, 4s. Fail-stops on 4xx (treated as a permanent endpoint problem,
    not a hiccup) so we don't spin on Cloudflare blocks."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    headers = {"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"}

    last_err: Optional[str] = None
    for attempt in range(1, max_attempts + 1):
        try:
            with requests.get(url, headers=headers, stream=True,
                              timeout=timeout, allow_redirects=True) as r:
                # Permanent client errors aren't worth retrying.
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    log.warning("[%s] download failed: %s (no retry on 4xx)",
                                url[:80], r.status_code)
                    return False
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            f.write(chunk)
            tmp.replace(out_path)
            return True
        except Exception as e:
            last_err = str(e)
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            if attempt < max_attempts:
                backoff = 2 ** (attempt - 1)
                log.info("[%s] transient download error (attempt %d/%d): %s "
                         "— retrying in %ds", url[:80], attempt, max_attempts,
                         last_err, backoff)
                time.sleep(backoff)
                continue
            log.warning("[%s] download failed after %d attempts: %s",
                        url[:80], max_attempts, last_err)
            return False
    return False


def fetch_endpoint(spec: EndpointSpec, year: int,
                    overwrite: bool = False) -> Optional[Path]:
    url, fname, out_dir = _spec_with_year(spec, year)
    out_path = out_dir / fname
    if out_path.exists() and not overwrite:
        ok, reason = _validate_csv(out_path, spec.min_bytes)
        if ok:
            log.info("[%s] cached at %s — skipping", spec.name, out_path.name)
            return out_path
        log.info("[%s] cached file invalid (%s) — refetching", spec.name, reason)

    log.info("[%s] fetching %s", spec.name, fname)
    if not _atomic_write(url, out_path, spec.timeout):
        return None
    ok, reason = _validate_csv(out_path, spec.min_bytes)
    if not ok:
        log.warning("[%s] downloaded file failed validation: %s", spec.name, reason)
        try:
            out_path.unlink(missing_ok=True)
        except (OSError, PermissionError) as e:
            log.warning("[%s] could not delete bad file (%s); leaving in place",
                        spec.name, e)
        return None
    log.info("[%s] OK -> %s (%d bytes)",
             spec.name, out_path, out_path.stat().st_size)
    return out_path


# ---------------------------------------------------------------------------
# Bulk refresh
# ---------------------------------------------------------------------------
def refresh_all(year: int,
                include_supplementary: bool = True,
                overwrite: bool = False,
                pause_seconds: float = 1.0) -> Dict[str, Optional[Path]]:
    targets: List[EndpointSpec] = list(LOAD_CRITICAL)
    if include_supplementary:
        targets += SUPPLEMENTARY

    results: Dict[str, Optional[Path]] = {}
    for spec in targets:
        results[spec.name] = fetch_endpoint(spec, year, overwrite=overwrite)
        time.sleep(pause_seconds)
    n_ok = sum(1 for p in results.values() if p is not None)
    log.info("Savant scrape complete: %d/%d endpoints OK",
             n_ok, len(results))
    return results


def refresh_all_for_today(include_supplementary: bool = True,
                           overwrite: bool = False) -> Dict[str, Optional[Path]]:
    return refresh_all(date.today().year,
                       include_supplementary=include_supplementary,
                       overwrite=overwrite)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser(description="Savant Statcast leaderboard scraper")
    p.add_argument("--year", type=int, default=date.today().year)
    p.add_argument("--no-supplementary", action="store_true",
                   help="Only pull load-critical endpoints (5 instead of 42).")
    p.add_argument("--overwrite", action="store_true",
                   help="Re-download even if a cached file exists for today.")
    p.add_argument("--endpoint", help="Run a single endpoint by name.")
    p.add_argument("--list", action="store_true",
                   help="List all registered endpoints and exit.")
    args = p.parse_args(argv)

    if args.list:
        all_specs = LOAD_CRITICAL + SUPPLEMENTARY
        print(f"Registered endpoints ({len(all_specs)}):")
        for s in LOAD_CRITICAL:
            print(f"  [LOAD_CRITICAL]  {s.name:34s} -> {s.out_dir}")
        for s in SUPPLEMENTARY:
            print(f"  [supplementary]  {s.name:34s} -> {s.out_dir}")
        return

    if args.endpoint:
        all_specs = {s.name: s for s in LOAD_CRITICAL + SUPPLEMENTARY}
        if args.endpoint not in all_specs:
            raise SystemExit(f"unknown endpoint: {args.endpoint}. "
                             f"valid names: {list(all_specs)}")
        spec = all_specs[args.endpoint]
        path = fetch_endpoint(spec, args.year, overwrite=args.overwrite)
        print(path or "FAILED")
        return

    refresh_all(args.year,
                include_supplementary=not args.no_supplementary,
                overwrite=args.overwrite)


if __name__ == "__main__":
    main()
