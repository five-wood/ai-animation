from pathlib import Path
import json
p=Path(__file__).parent;d=json.loads((p/'depth_audit.json').read_text());s=json.loads((p/'depth_supplement.json').read_text())
print('COUNT',sum(len(x['runs']) for x in d['projects'].values()))
for pn,x in d['projects'].items():
 r=x['runs']['exam_temporal' if pn=='lizard' else 'exam']; print(pn,'all4zero',r['GT_pair_order']['pair_all_four_vis_zero']);print('conf05',r['GT_pair_order']['conf_pair_min_ge_0.5']);print('conffit05',r['GT_pair_order']['conf_fit_pair_min_ge_0.5']);print('ankle conf',r['GT_ankle_confidence'])
 for n in ['xiyi2_temporal_web','xiyi3_temporal','xiyi3_clean_temporal']:
  if n in s[pn]:
   for v in ['front','side']:print(pn,n,v,'ankle',s[pn][n][v]['ankle']['rows'][3:])
