import pandas as pd 
from datetime import date 
from mlb_edge import build_pipeline as bp, model as md 
from mlb_edge.data_ingestion import OddsClient 
stage1, stage2 = md.load('models/latest.pkl') 
games = bp.build_slate_frame(date(2026,4,22)) 
feats1 = [c for c in stage1.feature_cols if c in games.columns] 
games['f5_model_output'] = stage1.booster.predict_proba(games[feats1].values)[:,1] 
feats2 = [c for c in stage2.feature_cols if c in games.columns] 
for c in set(stage2.feature_cols) - set(feats2): games[c] = 0.0 
games['model_prob'] = stage2.booster.predict_proba(games[stage2.feature_cols].values)[:,1] 
cols = ['home_team','away_team','model_prob','sp_xera_gap','team_woba_gap','swing_take_gap','home_sp_luck','away_sp_luck'] 
cols = [c for c in cols if c in games.columns] 
print(games[cols].sort_values('model_prob', ascending=False).to_string(index=False)) 
