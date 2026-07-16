#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline_alert.py -- best-effort failure alert to a Discord/Slack webhook.

Reads ALERT_WEBHOOK_URL from the environment or repo-root .env (never
hardcode or commit the URL; never paste it in chat). Payload carries both
Discord's "content" and Slack's "text" keys, so one URL of either kind works.
Without a URL it no-ops with instructions (chain-safe). Always exits 0 --
an alert failure must never break the job that is trying to report a failure.

Usage: python tools/pipeline_alert.py "CRITICAL: message here"
       python tools/pipeline_alert.py --selftest
"""
import io
import json
import os
import sys
import urllib.request

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))


def read_url():
    u = os.environ.get("ALERT_WEBHOOK_URL")
    if u:
        return u.strip()
    env = os.path.join(ROOT, ".env")
    if os.path.exists(env):
        for line in io.open(env, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line.startswith("ALERT_WEBHOOK_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def send(msg):
    url = read_url()
    if not url:
        print("[alert] no ALERT_WEBHOOK_URL in env/.env -- alert not sent. "
              "Create a Discord/Slack webhook and add the line "
              "ALERT_WEBHOOK_URL=... to the repo-root .env to enable alerts.")
        return 0
    body = json.dumps({"content": msg[:1900], "text": msg[:1900]}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "mlb_edge-alert/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("[alert] delivered (HTTP %s)" % r.status)
    except Exception as e:
        print("[alert] delivery failed (best-effort): %r" % (e,))
    return 0


def selftest():
    # payload shape
    body = json.loads(json.dumps({"content": "x" * 2500}))  # sanity
    assert isinstance(body["content"], str)
    # no-key path must be silent success
    old = os.environ.pop("ALERT_WEBHOOK_URL", None)
    root_old = globals()["ROOT"]
    try:
        globals()["ROOT"] = os.path.join(root_old, "nonexistent_dir_for_test")
        rc = send("selftest message (should NOT deliver)")
        assert rc == 0
    finally:
        globals()["ROOT"] = root_old
        if old:
            os.environ["ALERT_WEBHOOK_URL"] = old
    print("SELFTEST PASS -- no-key no-op, exit-0 contract")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    msg = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or "mlb_edge pipeline alert (no message)"
    sys.exit(send(msg))
