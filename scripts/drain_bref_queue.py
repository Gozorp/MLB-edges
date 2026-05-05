"""Drain the B-R scrape pending queue via direct HTTP fetch.

Reads data/.bref_scrape_pending.json, fetches each game's box-score page from
B-R (Chrome UA seems to be enough — Cloudflare currently lets us through),
extracts the batting/pitching/top_plays/play_by_play tables (B-R hides them in
HTML comments to defeat scrapers), and writes one JSON per game to
data/bref/boxes/. Successful entries are removed from the queue file; failures
are left for the next run.
"""
import json
import logging
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup, Comment

ROOT = Path(__file__).resolve().parents[1]
QUEUE_FILE = ROOT / "data" / ".bref_scrape_pending.json"
OUT_DIR = ROOT / "data" / "bref" / "boxes"
LOG_DIR = ROOT / "logs"

# pipeline team abbr -> B-R retrosheet-style code used in /boxes/ URLs
TEAM_TO_BREF = {
    "AZ": "ARI", "ARI": "ARI",
    "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHC": "CHN", "CHN": "CHN",
    "CWS": "CHA", "CHW": "CHA", "CHA": "CHA",
    "CIN": "CIN", "CLE": "CLE", "COL": "COL", "DET": "DET",
    "HOU": "HOU",
    "KC": "KCA", "KCR": "KCA", "KCA": "KCA",
    "LAA": "ANA", "ANA": "ANA",
    "LAD": "LAN", "LAN": "LAN",
    "MIA": "MIA", "MIL": "MIL", "MIN": "MIN",
    "NYM": "NYN", "NYN": "NYN",
    "NYY": "NYA", "NYA": "NYA",
    "ATH": "ATH", "OAK": "ATH",
    "PHI": "PHI", "PIT": "PIT",
    "SD": "SDN", "SDN": "SDN", "SDP": "SDN",
    "SEA": "SEA",
    "SF": "SFN", "SFN": "SFN", "SFG": "SFN",
    "STL": "SLN", "SLN": "SLN",
    "TB": "TBA", "TBR": "TBA", "TBA": "TBA",
    "TEX": "TEX", "TOR": "TOR",
    "WSH": "WAS", "WSN": "WAS", "WAS": "WAS",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"bref_scraper_{datetime.now().strftime('%Y%m%d')}.log"
    fmt = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt,
                        handlers=[logging.FileHandler(log_path, encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    return logging.getLogger("drain_bref")


def fetch(url: str, retries: int = 2, sleep: float = 2.0) -> str:
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(sleep * (i + 1))
    raise RuntimeError(f"fetch failed for {url}: {last!r}")


def csv_cell(s: str) -> str:
    s = s.replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return '"' + s.replace('"', '""') + '"'


def table_to_csv(tbl) -> str:
    lines = []
    for row in tbl.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        lines.append(",".join(csv_cell(c.get_text(separator=" ")) for c in cells))
    return "\n".join(lines)


def discover_tables(soup: BeautifulSoup):
    """Yield (table_id, table_element) in document order, dedup by id.

    B-R hides most data tables inside HTML comments wrapped in
    <div id="all_..."> placeholders. Walk the body once; whenever we hit a
    placeholder div, parse its comment children and emit any table[id] inside.
    """
    seen = set()

    def walk(node):
        if not getattr(node, "name", None):
            return
        if node.name == "table" and node.get("id"):
            tid = node.get("id")
            if tid not in seen:
                seen.add(tid)
                yield tid, node
            return
        if node.name == "div" and (node.get("id") or "").startswith("all_"):
            for c in node.find_all(string=lambda x: isinstance(x, Comment)):
                inner = BeautifulSoup(c, "lxml")
                for t in inner.find_all("table", id=True):
                    tid = t.get("id")
                    if tid not in seen:
                        seen.add(tid)
                        yield tid, t
            for child in node.children:
                yield from walk(child)
            return
        for child in getattr(node, "children", []):
            yield from walk(child)

    if soup.body is not None:
        yield from walk(soup.body)


KEEP_SUFFIXES = ("batting", "pitching")
KEEP_IDS = {"top_plays", "play_by_play"}


def extract_box(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title else ""
    out = {"url": url, "title": title, "tables": {}}

    idx = 0
    for tid, tbl in discover_tables(soup):
        idx += 1
        if not (tid.endswith(KEEP_SUFFIXES) or tid in KEEP_IDS):
            continue
        cap_el = tbl.find("caption")
        if cap_el:
            cap = re.sub(r"[^A-Za-z0-9]+", "_", cap_el.get_text().strip()).strip("_")
        else:
            cap = tid
        out["tables"][f"idx{idx}__{cap}"] = table_to_csv(tbl)

    sb = soup.find("div", class_="scorebox_meta")
    if sb:
        # Each fact (date, attendance, venue, ...) lives in its own immediate
        # <div> child — within a div, "<strong>Key</strong>: value" should
        # render as one line, so join its inner text with spaces.
        lines = []
        for child in sb.find_all("div", recursive=False):
            line = child.get_text(separator=" ").strip()
            line = re.sub(r"\s+", " ", line)
            line = re.sub(r"\s+:", ":", line)  # "Attendance :" -> "Attendance:"
            if line:
                lines.append(line)
        out["scorebox_meta"] = "\n".join(lines)
    else:
        out["scorebox_meta"] = ""
    return out


def url_for(home_team: str, date_str: str) -> str:
    home_bref = TEAM_TO_BREF.get(home_team.upper())
    if not home_bref:
        raise ValueError(f"unknown team abbr: {home_team!r}")
    yyyymmdd = date_str.replace("-", "")
    return (f"https://www.baseball-reference.com/boxes/{home_bref}/"
            f"{home_bref}{yyyymmdd}0.shtml")


def filename_for(url: str) -> str:
    m = re.search(r"/([A-Z]{3}\d{8}\d)\.shtml$", url)
    if not m:
        raise ValueError(f"unexpected url: {url}")
    return f"bref_boxscore_{m.group(1)}.json"


def load_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))


def save_queue(entries: list) -> None:
    QUEUE_FILE.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    log = setup_logging()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    queue = load_queue()
    if not queue:
        log.info("queue empty — nothing to do")
        return 0

    log.info("queue has %d pending entries", len(queue))
    remaining = list(queue)
    n_ok = 0
    n_fail = 0

    for entry in queue:
        try:
            url = url_for(entry["home"], entry["date"])
            html = fetch(url)
            data = extract_box(html, url)
            n_tables = len(data["tables"])
            if n_tables < 4:
                raise RuntimeError(f"only {n_tables} tables extracted (expected >=4)")
            out_path = OUT_DIR / filename_for(url)
            out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                encoding="utf-8")
            log.info("OK  game_pk=%s %s@%s -> %s (tables=%d, %d bytes)",
                     entry.get("game_pk"), entry.get("away"), entry.get("home"),
                     out_path.name, n_tables, out_path.stat().st_size)
            remaining = [e for e in remaining if e is not entry]
            n_ok += 1
        except Exception as e:
            log.error("FAIL game_pk=%s %s@%s on %s: %r",
                      entry.get("game_pk"), entry.get("away"), entry.get("home"),
                      entry.get("date"), e)
            n_fail += 1
        # Polite pacing — don't hammer B-R
        time.sleep(1.5)

    save_queue(remaining)
    log.info("=== summary === ok=%d fail=%d remaining=%d", n_ok, n_fail, len(remaining))
    return 0


if __name__ == "__main__":
    sys.exit(main())
