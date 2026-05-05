import pandas as pd, sys 
from datetime import date, datetime 
from mlb_edge import build_pipeline as bp, model as md 
from mlb_edge.data_ingestion import OddsClient 
from mlb_edge.stadiums import normalize_team 
d = datetime.strptime(sys.argv[1], '%Y-%m-%d').date() if len(sys.argv) > 1 else date.today() 
print(f'Slate for {d}') 
stage1, stage2 = md.load('models/latest.pkl') 
games = bp.build_slate_frame(d) 
feats1 = [c for c in stage1.feature_cols if c in games.columns] 
games['f5_model_output'] = stage1.booster.predict_proba(games[feats1].values)[:,1] 
for c in set(stage2.feature_cols) - set(games.columns): games[c] = 0.0 
games['model_prob'] = stage2.booster.predict_proba(games[stage2.feature_cols].values)[:,1] 
client = OddsClient() 
odds = client.current_lines() 
odds['home_team_abbr'] = odds['home_team'].apply(normalize_team) 
odds['away_team_abbr'] = odds['away_team'].apply(normalize_team) 
odds['outcome_abbr'] = odds['outcome'].apply(normalize_team) 
h2h = odds[odds['market'] == 'h2h'] 
piv = h2h.groupby(['home_team_abbr','away_team_abbr','outcome_abbr'])['price'].median().reset_index() 
rows = [] 
for _, g in games.iterrows(): 
    h, a = g['home_team'], g['away_team'] 
    hm = piv[(piv['home_team_abbr']==h)&(piv['away_team_abbr']==a)&(piv['outcome_abbr']==h)] 
    am = piv[(piv['home_team_abbr']==h)&(piv['away_team_abbr']==a)&(piv['outcome_abbr']==a)] 
    hp = hm['price'].iloc[0] if len(hm) else None 
    ap = am['price'].iloc[0] if len(am) else None 
    pick = h if g['model_prob']>=0.5 else a 
    rows.append({'home':h,'away':a,'pick':pick,'model_prob':round(g['model_prob'],3),'home_odds':hp,'away_odds':ap,'xera_gap':round(g.get('sp_xera_gap',0) or 0,2)}) 
print(pd.DataFrame(rows).sort_values('model_prob',ascending=False).to_string(index=False)) 
