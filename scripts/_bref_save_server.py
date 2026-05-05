"""
Tiny localhost POST sink for the Chrome MCP B-R scraper.
Receives {filename, content} JSON, writes data/bref/boxes/{filename}.
Listens on 127.0.0.1:9876. Designed for one-shot scraping runs;
kill it after the queue drains.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUT_DIR = Path("data/bref/boxes")
OUT_DIR.mkdir(parents=True, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n).decode("utf-8")
            obj = json.loads(raw)
            fname = obj["filename"]
            # basic sanitization: only allow filenames matching expected pattern
            if "/" in fname or "\\" in fname or ".." in fname:
                raise ValueError("bad filename")
            content = obj["content"]
            # validate content is parseable JSON
            json.loads(content)
            dest = OUT_DIR / fname
            dest.write_text(content, encoding="utf-8")
            print(f"[saved] {dest}  ({len(content)} bytes)", flush=True)
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "path": str(dest)}).encode())
        except Exception as e:
            print(f"[error] {e}", flush=True)
            self.send_response(500)
            self._cors()
            self.end_headers()
            self.wfile.write(str(e).encode())

    def log_message(self, fmt, *args):
        pass  # quiet


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9876
    print(f"bref save sink listening on 127.0.0.1:{port}", flush=True)
    print(f"writing to {OUT_DIR.resolve()}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
