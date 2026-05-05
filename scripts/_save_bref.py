"""Helper for the Chrome MCP B-R scraper.

Reads raw `get_page_text` output from stdin, extracts the JSON dump that the
in-browser extractor wrote into the page body, attaches the boxscore URL, and
writes a pretty-printed JSON file in the schema used by data/bref/boxes/.
"""
import json
import os
import sys
from collections import OrderedDict


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: _save_bref.py <url> <out_path>", file=sys.stderr)
        return 2
    url, out_path = sys.argv[1], sys.argv[2]

    raw = sys.stdin.read()
    start = raw.find('{"tables"')
    if start < 0:
        start = raw.find('{')
    end = raw.rfind('}')
    if start < 0 or end < start:
        print("could not locate JSON in input", file=sys.stderr)
        return 1
    payload = json.loads(raw[start:end + 1])

    out = OrderedDict()
    out["url"] = url
    out["title"] = payload.get("title", "")
    out["tables"] = payload.get("tables", {})
    out["scorebox_meta"] = payload.get("scorebox_meta", "")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {out_path} ({os.path.getsize(out_path)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
