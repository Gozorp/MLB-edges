#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI integrity gate -- refuses to publish a structurally broken docs/index.html.

Catches the failure classes that have actually bitten this repo:
  * file truncation (Edit-tool history: half a file pushed = dead dashboard)
  * doubled patch application (duplicate <script id>/<style id> blocks)
  * missing/duplicated critical element ids (renderers target these)
  * unbalanced <script>/<style> tags, missing </html> tail
  * merge-conflict markers
  * (when Node.js is available) JS syntax errors in inline scripts

Exit codes: 0 = pass (or overridden), 2 = FAIL.
Override:  PUBLISH_ALLOW_BROKEN_UI=1  (logs loudly, exits 0)
Usage:     python tools/ui_integrity_check.py [--file docs/index.html] [--selftest]
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

DEFAULT_FILE = os.path.join("docs", "index.html")

# every id a renderer or enhancer hard-targets; each must occur EXACTLY once.
SINGLETON_IDS = [
    "slate", "datePicker", "status", "hero-stat-games", "hero-stat-a-grades",
    "hero-stat-live", "side-drawer", "theme-toggle", "site-logo", "mx-agrades",
    "queryInput", "askBtn", "mode-simple-btn", "mode-advanced-btn",
    "top-outcomes", "bullpen-outlook", "parlay", "health-card", "metric-row",
    "queryCard",
]

MIN_BYTES = 400_000   # current healthy file ~530 KB; truncation catcher
MAX_BYTES = 2_500_000


def check_source(src):
    errs = []
    nbytes = len(src.encode("utf-8"))
    if nbytes < MIN_BYTES:
        errs.append("file suspiciously small: %d bytes (min %d) -- truncated?" % (nbytes, MIN_BYTES))
    if nbytes > MAX_BYTES:
        errs.append("file suspiciously large: %d bytes (max %d)" % (nbytes, MAX_BYTES))
    # real conflict markers are line-anchored ('='*7 alone; '<'*7/'>'*7 + space);
    # substring matching false-positives on '// ===== section =====' comments.
    lt7, gt7, eq7 = "<" * 7 + " ", ">" * 7 + " ", "=" * 7
    for line in src.splitlines():
        if line.startswith(lt7) or line.startswith(gt7) or line.rstrip() == eq7:
            errs.append("merge-conflict marker line %r present" % line[:24])
            break
    if not src.rstrip().endswith("</html>"):
        errs.append("file does not end with </html> -- truncated tail")
    ns_open, ns_close = src.count("<script"), src.count("</script")
    if ns_open != ns_close:
        errs.append("unbalanced script tags: %d open / %d close" % (ns_open, ns_close))
    nt_open, nt_close = src.count("<style"), src.count("</style")
    if nt_open != nt_close:
        errs.append("unbalanced style tags: %d open / %d close" % (nt_open, nt_close))
    for sid in SINGLETON_IDS:
        c = src.count('id="%s"' % sid)
        if c != 1:
            errs.append('critical id="%s" occurs %d times (want exactly 1)' % (sid, c))
    for kind in ("script", "style"):
        ids = re.findall(r"<%s id=\"([^\"]+)\"" % kind, src)
        for x in sorted(set(ids)):
            if ids.count(x) > 1:
                errs.append("duplicate <%s id=\"%s\"> block (patch applied twice?)" % (kind, x))
    return errs


def check_js_syntax(src):
    """Best-effort: only when Node.js exists on the box. Never blocks otherwise."""
    node = shutil.which("node")
    if not node:
        return [], 0
    errs, checked = [], 0
    scripts = re.findall(r"<script([^>]*)>(.*?)</script>", src, re.S)
    for i, (attrs, body) in enumerate(scripts):
        if not body.strip():
            continue
        if re.search(r"\bsrc\s*=", attrs):
            continue
        tm = re.search(r'type\s*=\s*"([^"]+)"', attrs)
        if tm and tm.group(1).lower() != "text/javascript":
            continue  # speculationrules / JSON / module blocks are not classic JS
        tf = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8")
        try:
            tf.write(body)
            tf.close()
            r = subprocess.run([node, "--check", tf.name], capture_output=True,
                               text=True, timeout=30)
            if r.returncode != 0:
                errs.append("inline script #%d fails node --check: %s"
                            % (i, (r.stderr or "").strip().splitlines()[-1][:160]))
            checked += 1
        except Exception:
            pass
        finally:
            try:
                os.unlink(tf.name)
            except OSError:
                pass
    return errs, checked


def run_checks(path):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        src = f.read()
    errs = check_source(src)
    js_errs, js_checked = check_js_syntax(src)
    errs += js_errs
    return errs, js_checked


def selftest():
    """Plant each corruption class into a copy; every plant must FAIL."""
    with io.open(DEFAULT_FILE, "r", encoding="utf-8", newline="") as f:
        good = f.read()
    base = check_source(good)
    assert not base, "clean file must pass, got: %s" % base
    plants = {
        "truncation": good[: len(good) // 2],
        "no-html-tail": good.rstrip()[:-7],
        "merge-marker": good.replace("</head>", ("<" * 7) + " HEAD\n</head>", 1)
                        if "\r\n" not in good.split("</head>", 1)[0][-200:]
                        else good.replace("</head>", ("<" * 7) + " HEAD\r\n</head>", 1),
        "dup-script-id": good.replace("</body>",
                                      '<script id="cc-widgets">/*dup*/</script></body>', 1),
        "dup-critical-id": good.replace("</body>", '<div id="slate"></div></body>', 1),
        "unbalanced-script": good.replace("</body>", "<script></body>", 1),
    }
    for name, bad in plants.items():
        errs = check_source(bad)
        assert errs, "planted %s NOT caught" % name
        print("  planted %-18s -> caught: %s" % (name, errs[0][:70]))
    print("SELFTEST PASS (%d corruption classes caught, clean file passes)" % len(plants))
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    path = DEFAULT_FILE
    if "--file" in sys.argv:
        path = sys.argv[sys.argv.index("--file") + 1]
    if not os.path.exists(path):
        print("ui_integrity_check: %s not found -- nothing to check" % path)
        return 0
    errs, js_checked = run_checks(path)
    if errs:
        print("ui_integrity_check FAIL -- %s:" % path)
        for e in errs:
            print("  * %s" % e)
        if os.environ.get("PUBLISH_ALLOW_BROKEN_UI") == "1":
            print("::warning:: PUBLISH_ALLOW_BROKEN_UI=1 -- overriding %d failures" % len(errs))
            return 0
        return 2
    extra = " (+%d inline scripts node-checked)" % js_checked if js_checked else ""
    print("ui_integrity_check PASS -- %s%s" % (path, extra))
    return 0


if __name__ == "__main__":
    sys.exit(main())
