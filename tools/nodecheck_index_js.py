#!/usr/bin/env python3
"""
nodecheck_index_js.py
---------------------
Push gate: extract the inline application <script> block from docs/index.html
(the one that defines renderBullpenEdge) and run `node --check` on it, so a
syntax error in an index.html JS edit blocks the commit. Exits nonzero on
failure. Run from the repo root.
"""
import os
import re
import subprocess
import sys

html = open("docs/index.html", encoding="utf-8").read().replace("\r\n", "\n")
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
target = [b for b in blocks if "renderBullpenEdge" in b]
if not target:
    print("nodecheck: could not locate the app <script> block")
    sys.exit(1)

tmp = "_index_appjs_check.js"
with open(tmp, "w", encoding="utf-8") as f:
    f.write(target[0])
try:
    rc = subprocess.run(["node", "--check", tmp]).returncode
finally:
    try:
        os.remove(tmp)
    except OSError:
        pass

print("nodecheck: OK" if rc == 0 else "nodecheck: SYNTAX ERROR")
sys.exit(rc)
