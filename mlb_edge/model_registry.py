"""
mlb_edge/model_registry.py
--------------------------
Versioned model archive.  Every time `model.save(...)` would overwrite an
existing pkl, we move the old one to ``models/archive/`` and record both
the displaced version and the new one in ``models/registry.json``.

Why bother:
    1. The active model file (``models/latest.pkl``) is referenced by ~6
       other modules (main_predict, main, predict.py, the cron task, etc.),
       so renaming it is invasive.  Keeping ``latest.pkl`` as the canonical
       active path while archiving displaced versions to the side gives us
       rollback without touching any consumer.
    2. We already have ad-hoc backups under names like ``v7_equiv.pkl``,
       ``v9_replica.pkl``, ``v12_backup.pkl`` — formalize the pattern so
       it's automatic and indexed.
    3. Before shipping calibration / learned-conviction upgrades, we want
       a single command that reverts to the previous model if the new one
       misbehaves on the next slate.

Data layout:
    models/
        latest.pkl                 (active — read by main_predict.run)
        f5_stage1.pkl              (legacy single-stage — leave alone)
        signal_meta.pkl            (small meta-LR — leave alone)
        archive/
            2026-04-29T01_42_18Z_a1b2c3d4.pkl
            2026-04-22T03_11_09Z_9f8e7d6c.pkl
            ...
        registry.json              (manifest — single source of truth)

Manifest schema (``models/registry.json``)::

    {
      "schema_version": 1,
      "versions": [
        {
          "id": "2026-04-29T01_42_18Z_a1b2c3d4",
          "path": "models/archive/2026-04-29T01_42_18Z_a1b2c3d4.pkl",
          "sha256": "a1b2c3d4...",
          "size_bytes": 1778096,
          "promoted_at": "2026-04-29T01:42:18+00:00",
          "is_active": false,
          "label": "v12-multi-year-sp-frame",
          "notes": "auto-archived when latest.pkl was overwritten",
          "metrics": {                 # optional, populated by trainer if known
              "n_train": null,
              "walk_forward_roi": null,
              "hit_rate": null
          }
        },
        ...
      ],
      "active_id": "2026-04-29T01_42_18Z_a1b2c3d4"
    }

Public API:
    archive_existing(path)            -> str | None       # archive-before-overwrite
    register(path, metadata)          -> str              # add to registry
    list_versions()                   -> list[dict]
    get_active() / set_active(id)
    load_version(id)                  -> (stage1, stage2)
    rollback(id)                      -> None             # set_active + copy to latest.pkl
    compare(id_a, id_b)               -> dict             # side-by-side metadata
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

MODELS_DIR    = Path("models")
ARCHIVE_DIR   = MODELS_DIR / "archive"
REGISTRY_PATH = MODELS_DIR / "registry.json"
ACTIVE_PATH   = MODELS_DIR / "latest.pkl"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Manifest read/write — defensive, so a corrupted registry doesn't kill saves
# ---------------------------------------------------------------------------
def _empty_registry() -> dict:
    return {"schema_version": SCHEMA_VERSION, "versions": [], "active_id": None}


def _load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return _empty_registry()
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            log.warning("registry schema mismatch (%s vs %s); rebuilding empty",
                        data.get("schema_version"), SCHEMA_VERSION)
            return _empty_registry()
        data.setdefault("versions", [])
        data.setdefault("active_id", None)
        return data
    except Exception as e:
        log.warning("registry parse failed (%s); starting with empty manifest", e)
        return _empty_registry()


def _save_registry(reg: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    tmp.replace(REGISTRY_PATH)


# ---------------------------------------------------------------------------
# Hashing & ID generation
# ---------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_id(sha: str, when: Optional[datetime] = None) -> str:
    when = when or datetime.now(timezone.utc)
    # ISO 8601 minus colons (filesystem-friendly) + first 8 hex of SHA.
    stamp = when.strftime("%Y-%m-%dT%H_%M_%SZ")
    return f"{stamp}_{sha[:8]}"


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------
def archive_existing(path: Path | str = ACTIVE_PATH,
                     label: Optional[str] = None,
                     notes: Optional[str] = None) -> Optional[str]:
    """If `path` exists, copy it into ``models/archive/`` and register the
    archived copy.  Returns the version id of the archived copy, or None
    if `path` didn't exist (first-time save).

    The active path is NOT touched; this is purely a "preserve before the
    caller overwrites" operation.  Idempotent: if the same content has
    already been archived (same SHA-256), return the existing id.
    """
    path = Path(path)
    if not path.exists():
        return None
    sha = _sha256(path)
    reg = _load_registry()
    # Idempotency — if a version with this hash already exists, reuse it.
    for v in reg["versions"]:
        if v.get("sha256") == sha:
            log.info("archive: existing version %s has same SHA -> skipping copy",
                     v["id"])
            return v["id"]

    when = datetime.now(timezone.utc)
    vid = _make_id(sha, when)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / f"{vid}.pkl"
    shutil.copy2(path, archive_path)

    entry = {
        "id": vid,
        "path": str(archive_path),
        "sha256": sha,
        "size_bytes": archive_path.stat().st_size,
        "promoted_at": when.isoformat(),
        "is_active": False,           # archived copy is by definition inactive
        "label": label or "auto-archived",
        "notes": notes or "preserved before overwrite",
        "metrics": {"n_train": None, "walk_forward_roi": None, "hit_rate": None},
    }
    reg["versions"].append(entry)
    _save_registry(reg)
    log.info("archive: %s (%d bytes) -> %s", path, entry["size_bytes"], vid)
    return vid


def register_active(path: Path | str = ACTIVE_PATH,
                    label: Optional[str] = None,
                    metrics: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Record that a freshly-saved file at `path` is now the active model.

    Always copies the file to ``models/archive/<id>.pkl`` so the manifest
    entry's ``path`` field points at a stable on-disk location that won't
    be clobbered by future saves.  ``models/latest.pkl`` continues to
    exist as the active-model symlink-equivalent.
    """
    path = Path(path)
    if not path.exists():
        log.warning("register_active: %s does not exist; skipping", path)
        return None
    sha = _sha256(path)
    reg = _load_registry()
    # Idempotency: if a stable-archive entry with this SHA already exists,
    # just bump the active flag — no need for a duplicate archive copy.
    for v in reg["versions"]:
        if v.get("sha256") == sha and not str(v.get("path","")).endswith("/latest.pkl"):
            for w in reg["versions"]: w["is_active"] = False
            v["is_active"] = True
            reg["active_id"] = v["id"]
            _save_registry(reg)
            return v["id"]

    when = datetime.now(timezone.utc)
    vid = _make_id(sha, when)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / f"{vid}.pkl"
    shutil.copy2(path, archive_path)

    entry = {
        "id": vid,
        "path": str(archive_path),       # STABLE archive location, not latest.pkl
        "sha256": sha,
        "size_bytes": archive_path.stat().st_size,
        "promoted_at": when.isoformat(),
        "is_active": True,
        "label": label or "active",
        "notes": "registered as active",
        "metrics": metrics or {"n_train": None, "walk_forward_roi": None,
                                "hit_rate": None},
    }
    for v in reg["versions"]: v["is_active"] = False
    reg["versions"].append(entry)
    reg["active_id"] = vid
    _save_registry(reg)
    log.info("register_active: %s -> %s (archived to %s)",
             path, vid, archive_path)
    return vid


def list_versions() -> List[dict]:
    """Return all manifest entries, newest first."""
    reg = _load_registry()
    return sorted(reg["versions"],
                  key=lambda v: v.get("promoted_at", ""),
                  reverse=True)


def get_active() -> Optional[dict]:
    reg = _load_registry()
    if not reg.get("active_id"):
        return None
    for v in reg["versions"]:
        if v["id"] == reg["active_id"]:
            return v
    return None


def _find(version_id: str) -> Optional[dict]:
    """Resolve `version_id` (full or unique prefix) to a manifest entry."""
    reg = _load_registry()
    matches = [v for v in reg["versions"]
               if v["id"] == version_id or v["id"].startswith(version_id)]
    if not matches:
        return None
    if len(matches) > 1:
        log.warning("ambiguous prefix %s — matches %s",
                    version_id, [m["id"] for m in matches])
        return None
    return matches[0]


def load_version(version_id: str) -> Tuple[Any, Any]:
    """Load (stage1, stage2) from any registered version. Caller can run
    side-by-side comparisons against the active model."""
    from . import model as md
    entry = _find(version_id)
    if entry is None:
        raise SystemExit(f"unknown version id (or ambiguous prefix): {version_id}")
    return md.load(entry["path"])


def rollback(version_id: str) -> str:
    """Swap ``models/latest.pkl`` to point at the archived version.

    The previous active file is archived first (so rollback is reversible).
    Returns the id that's now active.
    """
    entry = _find(version_id)
    if entry is None:
        raise SystemExit(f"unknown version id (or ambiguous prefix): {version_id}")
    # Archive whatever's currently at the active path so we can roll back FROM
    # the rollback if needed.
    archive_existing(ACTIVE_PATH, label="pre-rollback",
                     notes=f"displaced by rollback to {version_id}")
    # Copy the chosen archived pkl over latest.pkl
    src = Path(entry["path"])
    if not src.exists():
        raise SystemExit(f"archived file missing on disk: {src}")
    shutil.copy2(src, ACTIVE_PATH)
    # Update active pointer
    reg = _load_registry()
    for v in reg["versions"]: v["is_active"] = False
    for v in reg["versions"]:
        if v["id"] == entry["id"]:
            v["is_active"] = True
            reg["active_id"] = v["id"]
            break
    _save_registry(reg)
    log.info("rollback: latest.pkl now points at %s", entry["id"])
    return entry["id"]


def compare(id_a: str, id_b: str) -> dict:
    a = _find(id_a) or {}
    b = _find(id_b) or {}
    return {"a": a, "b": b}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _fmt_row(v: dict) -> str:
    active = "*" if v.get("is_active") else " "
    metrics = v.get("metrics") or {}
    roi = metrics.get("walk_forward_roi")
    hit = metrics.get("hit_rate")
    roi_s = f"{roi*100:+5.2f}%" if isinstance(roi, (int, float)) else "  --  "
    hit_s = f"{hit*100:5.1f}%" if isinstance(hit, (int, float)) else "  --  "
    return (f" {active} {v['id']:38s} "
            f"{(v.get('label') or '')[:22]:22s} "
            f"ROI={roi_s}  hit={hit_s}  "
            f"{(v.get('notes') or '')[:40]}")


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="model_registry")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Show all known versions, newest first.")

    p_roll = sub.add_parser("rollback",
                            help="Swap latest.pkl to a registered version.")
    p_roll.add_argument("version_id",
                        help="Full id or unique prefix.")

    p_cmp = sub.add_parser("compare", help="Side-by-side metadata diff.")
    p_cmp.add_argument("a"); p_cmp.add_argument("b")

    p_reg = sub.add_parser("register",
                           help="Record an existing pkl in the manifest.")
    p_reg.add_argument("path",
                       help="Path to the .pkl file")
    p_reg.add_argument("--label", default=None)
    p_reg.add_argument("--archive", action="store_true",
                       help="Copy into models/archive/ rather than registering "
                            "in place.")
    p_reg.add_argument("--active", action="store_true",
                       help="Mark this version as the active one.")

    args = p.parse_args(argv)

    if args.cmd == "list":
        rows = list_versions()
        if not rows:
            print("(registry empty)")
            return 0
        print(f"  active id  {'version_id':38s} {'label':22s} "
              f"{'metrics':18s}  notes")
        print("-" * 110)
        for r in rows:
            print(_fmt_row(r))
        active = get_active()
        if active:
            print()
            print(f"active: {active['id']}  ({active.get('path')})")
        return 0

    if args.cmd == "rollback":
        new_active = rollback(args.version_id)
        print(f"rolled back; latest.pkl now points at {new_active}")
        return 0

    if args.cmd == "compare":
        c = compare(args.a, args.b)
        print(json.dumps(c, indent=2))
        return 0

    if args.cmd == "register":
        if args.archive:
            vid = archive_existing(args.path, label=args.label,
                                   notes="registered via CLI --archive")
        else:
            vid = register_active(args.path, label=args.label)
        if args.active and vid:
            # rolling back to the just-registered version is the cleanest way
            # to make it active *and* update latest.pkl.
            rollback(vid)
        print(f"registered: {vid}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
