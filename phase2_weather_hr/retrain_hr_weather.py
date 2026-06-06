#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase-2 RETRAIN (shadow branch) -- train XGBoost with the weather x Savant
interaction features and validate OOS vs the incumbent (no-weather) model against
the LOCKED gates. NOT wired into production. Run in July; deploy to main ONLY if
every gate passes. See phase2_weather_hr/PHASE2_SPEC.md.
"""
import json

# LOCKED gates (mirror project_totals_recal_prereg / project_empbayes_sp_xera_prereg)
GATES = {
    "min_oos_logloss_gain": 0.0,    # must be > 0 AND statistically significant
    "require_significant": True,    # DeLong / bootstrap p < 0.05
    "max_thick_subset_dauc": 0.01,  # no degradation on the bulk
    "require_sign_correct": True,   # wind-out=+HR, wind-in=-HR, thin-air=+
    "burn_in_min_obs": 60,
}
WEATHER_FEATURES = ["effective_wind", "air_density_index", "ew_x_barrel",
                    "ew_x_flyball", "adi_x_la", "adi_x_flyball", "parkhr_x_ew"]

def load_labels(path="data/phase2/hr_weather_labels.csv"):
    """Load the backfill from build_hr_training_labels.py. TODO July."""
    raise NotImplementedError("July: load the label CSV")

def train_eval(df):
    """Dual OOS harness (walk-forward + K-fold). Train INCUMBENT (existing
    features) vs +WEATHER (existing + WEATHER_FEATURES). Return:
    {oos_logloss_delta, brier_delta, delong_p, thick_dauc,
     weather_importances:{feat:gain}, sign_check:{feat:bool}}.
    TODO July: import xgboost; reuse the model's feature pipeline; guard leakage."""
    raise NotImplementedError("July: implement the dual-harness train + eval")

def verdict(m):
    ok = (m["oos_logloss_delta"] > GATES["min_oos_logloss_gain"]
          and (m["delong_p"] < 0.05 if GATES["require_significant"] else True)
          and abs(m["thick_dauc"]) <= GATES["max_thick_subset_dauc"]
          and (all(m["sign_check"].values()) if GATES["require_sign_correct"] else True))
    return "PASS -> deploy + re-freeze" if ok else "NULL -> keep display-only, stay frozen"

if __name__ == "__main__":
    print("Phase-2 retrain skeleton -- run in July. Locked gates:", json.dumps(GATES))
    # df = load_labels(); m = train_eval(df); print(verdict(m))
