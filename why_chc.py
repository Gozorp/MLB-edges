import pandas as pd 
from datetime import date 
from mlb_edge import build_pipeline as bp 
g = bp.build_slate_frame(date(2026,4,22)) 
r = g[(g.home_team=='CHC') & (g.away_team=='PHI')].iloc[0] 
cols = ['sp_xera_gap','sp_xwoba_allowed_gap','sp_fip_gap','team_woba_gap','team_wrcplus_gap','bullpen_siera_gap','park_runs_factor','home_sp_luck','away_sp_luck'] 
for c in cols: 
    v = r.get(c) 
    print(f'{c:25s} = {v:+.3f}' if pd.notna(v) else f'{c:25s} = NaN') 
