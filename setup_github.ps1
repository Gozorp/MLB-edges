# =============================================================================
# setup_github.ps1
# -----------------------------------------------------------------------------
# One-time setup script to push mlb_edge to a public GitHub repo.
# Run this from PowerShell in D:\mlb_edge\mlb_edge\
#
# Prereqs: git installed and authenticated (you said this is set up).
#          GitHub username known (set GH_USER below).
# =============================================================================

# --- Configuration ---
$GH_USER = "saladin-alfaatih"        # <-- change if your GitHub username differs
$REPO_NAME = "mlb_edge"
$REMOTE_URL = "https://github.com/$GH_USER/$REPO_NAME.git"

# --- Step 1: clean up any broken .git from the Linux-side init ---
if (Test-Path ".git") {
    Write-Host "Removing existing .git directory (was left in a broken state)..."
    Remove-Item -Recurse -Force ".git"
}

# --- Step 2: initialize repo ---
git init -b main
git config user.name "Saladin Alfaatih"
git config user.email "saladin.alfaatih@gmail.com"

# --- Step 3: stage everything (respects .gitignore we just wrote) ---
Write-Host "Staging files (this respects the new .gitignore)..."
git add -A

# --- Step 4: sanity check before commit ---
$staged = (git status --short | Measure-Object -Line).Lines
Write-Host ""
Write-Host "About to commit $staged file changes."
Write-Host "Inspect with: git status"
Write-Host ""
Read-Host "Press Enter to commit (Ctrl+C to abort and inspect first)"

# --- Step 5: commit ---
git commit -m "Initial commit: mlb_edge model + dashboard

- Two-stage XGBoost (F5 + Full game) with calibration
- Eight-rule parlay grader (Odds-API guard, PQI, team quality,
  F-signal cap, compound-small-sample cap, diversity reserve)
- Savant + FanGraphs scrapers, ESPN odds fallback
- Static dashboard in /docs for GitHub Pages
- See README.md for the full architecture"

# --- Step 6: create the remote repo via gh CLI if available, otherwise prompt ---
$ghAvailable = Get-Command gh -ErrorAction SilentlyContinue
if ($ghAvailable) {
    Write-Host "Creating public repo via gh CLI..."
    gh repo create $REPO_NAME --public --source=. --remote=origin --push
} else {
    Write-Host ""
    Write-Host "==========================================================="
    Write-Host "  GitHub CLI (gh) not found. Manual remote setup required:"
    Write-Host "==========================================================="
    Write-Host "  1. Go to https://github.com/new"
    Write-Host "  2. Repository name: $REPO_NAME"
    Write-Host "  3. Public, no README/license/gitignore (we have all three)"
    Write-Host "  4. Click 'Create repository'"
    Write-Host "  5. Then run these two commands:"
    Write-Host ""
    Write-Host "     git remote add origin $REMOTE_URL"
    Write-Host "     git push -u origin main"
    Write-Host ""
    Write-Host "==========================================================="
}

# --- Step 7: enable GitHub Pages from /docs ---
Write-Host ""
Write-Host "==========================================================="
Write-Host "  After push, enable GitHub Pages:"
Write-Host "==========================================================="
Write-Host "  1. Go to https://github.com/$GH_USER/$REPO_NAME/settings/pages"
Write-Host "  2. Source: Deploy from a branch"
Write-Host "  3. Branch: main, folder: /docs"
Write-Host "  4. Save"
Write-Host ""
Write-Host "  Dashboard will be live at:"
Write-Host "  https://$GH_USER.github.io/$REPO_NAME/"
Write-Host "==========================================================="
