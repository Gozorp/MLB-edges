# Static dashboard

This folder is published via GitHub Pages and serves the `index.html`
dashboard. The dashboard fetches `picks_YYYY-MM-DD_diag.csv` and
`parlay_YYYY-MM-DD.txt` directly from the repo root and renders them
client-side — no backend, no build step.

## Setup (one-time)

In the GitHub repo settings:

1. **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: **main**, folder: **/docs**
4. Save

The dashboard will be live at `https://<username>.github.io/mlb_edge/`
within a minute or two of the next push.

## How updates work

Every time `predict.py` writes a new `picks_YYYY-MM-DD_diag.csv` and you
push it to the repo, the dashboard immediately picks it up — pick a date
in the date picker and the page re-fetches.

No build, no rebuild, no deployment. Just commit and push.
