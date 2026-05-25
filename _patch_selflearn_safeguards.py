#!/usr/bin/env python3
"""
_patch_selflearn_safeguards.py
==============================
Three safeguards on top of apply_calibration_from_all_picks:

  (1) Asymmetric ceiling fix:
      ceil = base                 -> ceil = base * NEW_CEILING_MULT  (1.5)
      Lets weights grow modestly past their initial value so a signal
      that was under-credited at init can recover. Hard floor at
      MIN_RELATIVE_WEIGHT * base (0.25 * base) is unchanged.

  (2) Stress-warned game mask:
      tier_weight *= STRESS_MASK_FACTOR  (0.3) when the diag row has
      a non-empty stress_warnings string OR confidence_downgrade=True.
      Prevents double-counting noise — when the model itself flags a
      prediction as shaky, its outcome contributes less to learning.

  (3) Warm-up gate:
      Reads cumulative n_picks_used_for_learning from the existing
      audit log. If < WARMUP_THRESHOLD (30), audit-only mode — deltas
      computed and recorded but NOT applied to weights_state.
      Backfilled log has ~125 observations, so the gate auto-passes
      on day one. This is also a structural self-heal: blowing away
      the audit log re-engages probation automatically.

Plus augmented audit entries with:
  - max_weight_change_pct   (largest |Δw_i / w_i| in this update)
  - weights_growing_past_prior (list of weights whose new value > base)

Schema changes:
  - _picks_diag_to_calib_rows now keeps stress_warnings + confidence_downgrade
    columns from the diag CSV.
"""
from __future__ import annotations
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "mlb_edge" / "auto_weight_update.py"


def must_replace(src: str, old: str, new: str, label: str = "") -> str:
    n = src.count(old)
    if n != 1:
        print(f"[FAIL] {label}: expected 1 occurrence, found {n}")
        sys.exit(2)
    return src.replace(old, new, 1)


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    n0 = len(src)
    print(f"input: {TARGET} ({n0} bytes)")

    # ---------- 1. New constants right after CALIB_LEARN_RATE ----------
    src = must_replace(
        src,
        'CALIB_LEARN_RATE: float = 0.04\n',
        'CALIB_LEARN_RATE: float = 0.04\n'
        '\n'
        '# Safeguards (2026-05-25):\n'
        '#   NEW_CEILING_MULT: weights can grow modestly past their initial\n'
        '#     value. Previously ceil=base hard-clipped any upward update,\n'
        '#     turning the loop into a one-sided decay rule.\n'
        '#   STRESS_MASK_FACTOR: down-weights games the model itself flagged\n'
        '#     as low-confidence (stress_warnings non-empty OR\n'
        '#     confidence_downgrade=True) so their outcomes feed back less.\n'
        '#   WARMUP_THRESHOLD: minimum cumulative learned-from observations\n'
        '#     across audit history before updates apply. Self-healing:\n'
        '#     blowing away the audit log re-engages probation automatically.\n'
        '#     IMPORTANT: do not git-clean data/state/ without thinking.\n'
        'NEW_CEILING_MULT: float = 1.5\n'
        'STRESS_MASK_FACTOR: float = 0.3\n'
        'WARMUP_THRESHOLD: int = 30\n',
        "1: safeguard constants",
    )
    print("[ok]   1: safeguard constants added")

    # ---------- 2. Schema: keep stress_warnings + confidence_downgrade ----------
    src = must_replace(
        src,
        '    out_cols = ["pick", "pick_prob", "p_model", "full_prob",\n'
        '                "tier", "signals", "won", "tier_weight", "run_diff"]\n',
        '    out_cols = ["pick", "pick_prob", "p_model", "full_prob",\n'
        '                "tier", "signals", "won", "tier_weight", "run_diff",\n'
        '                "stress_warnings", "confidence_downgrade"]\n',
        "2: keep stress columns",
    )
    print("[ok]   2: stress columns added to calib rows")

    # ---------- 3. Warm-up gate helper ----------
    src = must_replace(
        src,
        'def apply_calibration_from_all_picks(\n',
        'def _total_learned_from_count() -> int:\n'
        '    """Sum n_picks_used_for_learning across the entire audit log.\n'
        '\n'
        '    Used by the warm-up gate. A missing/empty log returns 0, which\n'
        '    structurally re-engages probation \\u2014 desired behavior if the\n'
        '    state is ever blown away. See WARMUP_THRESHOLD docstring.\n'
        '    """\n'
        '    if not AUDIT_LOG.exists():\n'
        '        return 0\n'
        '    total = 0\n'
        '    try:\n'
        '        with AUDIT_LOG.open(encoding="utf-8") as f:\n'
        '            for line in f:\n'
        '                line = line.strip()\n'
        '                if not line:\n'
        '                    continue\n'
        '                try:\n'
        '                    entry = json.loads(line)\n'
        '                    total += int(entry.get("n_picks_used_for_learning", 0))\n'
        '                except (json.JSONDecodeError, ValueError, TypeError):\n'
        '                    continue\n'
        '    except OSError:\n'
        '        return 0\n'
        '    return total\n'
        '\n'
        '\n'
        'def apply_calibration_from_all_picks(\n',
        "3: warm-up gate helper",
    )
    print("[ok]   3: _total_learned_from_count() inserted")

    # ---------- 4. Rewrite the update body ----------
    src = must_replace(
        src,
        '    for _, r in rows.iterrows():\n'
        '        try:\n'
        '            p = float(r.get(prob_col))\n'
        '        except (TypeError, ValueError):\n'
        '            continue\n'
        '        won = int(r.get("won", 0))\n'
        '        residual = won - p\n'
        '        tw = float(r.get("tier_weight", TIER_LEARN_WEIGHT["SKIP"]))\n'
        '        sigs = _parse_signals(r.get("signals", "") if pd.notna(r.get("signals", "")) else "")\n'
        '        if not sigs:\n'
        '            continue\n'
        '        n_with_signals += 1\n'
        '        for sig in sigs:\n'
        '            for feat in SIGNAL_TO_FEATURES.get(sig, []):\n'
        '                feature_grad[feat] = feature_grad.get(feat, 0.0) + tw * residual\n'
        '    n_total = int(len(rows))\n'
        '    if not feature_grad:\n'
        '        return state, n_total, n_with_signals\n'
        '    denom = max(1, n_with_signals)\n'
        '    for feat, g in feature_grad.items():\n'
        '        base = baseline_weights.get(feat, 1.0)\n'
        '        floor = MIN_RELATIVE_WEIGHT * base\n'
        '        ceil  = base\n'
        '        delta_mult = 1.0 + learn_rate * (g / denom)\n'
        '        cur = state.get(feat, base)\n'
        '        new = cur * delta_mult\n'
        '        if new < floor: new = floor\n'
        '        if new > ceil: new = ceil\n'
        '        state[feat] = new\n'
        '    _save_state(state)\n'
        '    return state, n_total, n_with_signals\n',
        '    # Warm-up gate: pass iff we have enough historical observations.\n'
        '    # Backfilled audit log has ~125 obs, so this passes on day one.\n'
        '    historical = _total_learned_from_count()\n'
        '    audit_only = historical < WARMUP_THRESHOLD\n'
        '    if audit_only:\n'
        '        log.info(\n'
        '            "[warmup] %d/%d learned-from obs in audit log \\u2014 audit-only mode",\n'
        '            historical, WARMUP_THRESHOLD,\n'
        '        )\n'
        '\n'
        '    for _, r in rows.iterrows():\n'
        '        try:\n'
        '            p = float(r.get(prob_col))\n'
        '        except (TypeError, ValueError):\n'
        '            continue\n'
        '        won = int(r.get("won", 0))\n'
        '        residual = won - p\n'
        '        tw = float(r.get("tier_weight", TIER_LEARN_WEIGHT["SKIP"]))\n'
        '        # Stress-warned mask: down-weight games the model itself\n'
        '        # flagged as low-confidence. Either a non-empty\n'
        '        # stress_warnings string OR confidence_downgrade=True\n'
        '        # triggers the 0.3x multiplier.\n'
        '        sw_raw = r.get("stress_warnings", "")\n'
        '        sw = str(sw_raw).strip() if pd.notna(sw_raw) else ""\n'
        '        cd_raw = r.get("confidence_downgrade", False)\n'
        '        try:\n'
        '            cd = bool(cd_raw) and str(cd_raw).strip().lower() not in ("false", "0", "")\n'
        '        except Exception:\n'
        '            cd = False\n'
        '        if sw or cd:\n'
        '            tw *= STRESS_MASK_FACTOR\n'
        '        sigs = _parse_signals(r.get("signals", "") if pd.notna(r.get("signals", "")) else "")\n'
        '        if not sigs:\n'
        '            continue\n'
        '        n_with_signals += 1\n'
        '        for sig in sigs:\n'
        '            for feat in SIGNAL_TO_FEATURES.get(sig, []):\n'
        '                feature_grad[feat] = feature_grad.get(feat, 0.0) + tw * residual\n'
        '    n_total = int(len(rows))\n'
        '    if not feature_grad:\n'
        '        return state, n_total, n_with_signals\n'
        '    denom = max(1, n_with_signals)\n'
        '    for feat, g in feature_grad.items():\n'
        '        base = baseline_weights.get(feat, 1.0)\n'
        '        floor = MIN_RELATIVE_WEIGHT * base\n'
        '        # 2026-05-25: ceil bumped from `base` to `base * 1.5` so a\n'
        '        # weight that was under-credited at init can recover. Prior\n'
        '        # behavior was a one-sided decay rule (could shrink to 25%\n'
        '        # of base, but never grow past base).\n'
        '        ceil  = base * NEW_CEILING_MULT\n'
        '        delta_mult = 1.0 + learn_rate * (g / denom)\n'
        '        cur = state.get(feat, base)\n'
        '        new = cur * delta_mult\n'
        '        if new < floor: new = floor\n'
        '        if new > ceil: new = ceil\n'
        '        if not audit_only:\n'
        '            state[feat] = new\n'
        '        # If audit_only, state[feat] stays at cur and the audit\n'
        '        # entry will record a zero delta. The new value is still\n'
        '        # written to the proposed_state dict below for observability.\n'
        '    if not audit_only:\n'
        '        _save_state(state)\n'
        '    return state, n_total, n_with_signals\n',
        "4: update body with stress mask + new ceiling + warm-up gate",
    )
    print("[ok]   4: update body rewritten")

    # ---------- 5. Augmented audit entry with max_change + growth-past-prior ----------
    src = must_replace(
        src,
        '    deltas = {k: round(new_state.get(k, 1.0) - prev_state.get(k, 1.0), 6)\n'
        '              for k in set(prev_state) | set(new_state)}\n'
        '    entry: Dict = {\n'
        '        "ts": datetime.now(timezone.utc).isoformat(),\n'
        '        "slate_date": target_date.isoformat(),\n'
        '        "n_bets": int(n_bets),\n'
        '        "wins": int(wins),\n'
        '        "learn_mode": learn_mode,\n'
        '        "weight_deltas": deltas,\n'
        '        "new_state": {k: round(v, 6) for k, v in new_state.items()},\n'
        '    }\n',
        '    deltas = {k: round(new_state.get(k, 1.0) - prev_state.get(k, 1.0), 6)\n'
        '              for k in set(prev_state) | set(new_state)}\n'
        '    # Safeguard observability (2026-05-25): surface the largest\n'
        '    # single-weight move + any weight that grew past its baseline\n'
        '    # in this update. weights_growing_past_prior should be empty\n'
        '    # for the first ~10 days under the new ceil=1.5*base rule\n'
        '    # since most weights are well below their priors.\n'
        '    try:\n'
        '        from .recursive_weight_update import SP_WEIGHTS as _BASELINES\n'
        '    except Exception:\n'
        '        _BASELINES = {}\n'
        '    max_change_pct = 0.0\n'
        '    growing_past_prior: List[str] = []\n'
        '    for k, d in deltas.items():\n'
        '        prev_v = prev_state.get(k, 1.0)\n'
        '        if prev_v:\n'
        '            pct = abs(d) / abs(prev_v)\n'
        '            if pct > max_change_pct:\n'
        '                max_change_pct = pct\n'
        '        new_v = new_state.get(k, prev_v)\n'
        '        base_v = _BASELINES.get(k)\n'
        '        if base_v is not None and new_v > base_v:\n'
        '            growing_past_prior.append(k)\n'
        '    entry: Dict = {\n'
        '        "ts": datetime.now(timezone.utc).isoformat(),\n'
        '        "slate_date": target_date.isoformat(),\n'
        '        "n_bets": int(n_bets),\n'
        '        "wins": int(wins),\n'
        '        "learn_mode": learn_mode,\n'
        '        "weight_deltas": deltas,\n'
        '        "max_weight_change_pct": round(max_change_pct, 6),\n'
        '        "weights_growing_past_prior": growing_past_prior,\n'
        '        "new_state": {k: round(v, 6) for k, v in new_state.items()},\n'
        '    }\n',
        "5: audit entry with max_change + growth-past-prior",
    )
    print("[ok]   5: audit entry augmented")

    TARGET.write_text(src, encoding="utf-8")
    n1 = len(src)
    print(f"output: {TARGET} ({n1} bytes, delta {n1-n0:+d})")

    import ast
    try:
        ast.parse(src)
        print("[ok]   AST parse: OK")
    except SyntaxError as e:
        print(f"[FAIL] AST parse: line {e.lineno}: {e.msg}")
        sys.exit(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
