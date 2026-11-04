import json
x=json.load(open('work/analyze/left_hind/left_hind_audit_summary.json'))
for k,d in x['joints'].items():
 print('\nJOINT',k)
 for v in ['front','side']:
  q=d[v];print(v,'uv',q['uv_px'],'weight',q['weights'],'basew',q['baseline_weights'],'rep',q['reproj_temporal_px'],'base_rep',q['reproj_baseline_px'],'ray',q['ray_residual_px'])
  print('factors',{a:round(q[a]['median'],5) for a in ['bounds_factor','peak_factor','temporal_factor','bone_factor','ray_factor','ambiguity_factor','spread_factor']},'tfmin',q['temporal_factor']['min'],'innov',q['temporal_distance_px'],'dd',q['second_difference_px'],'offset',q['reproj_offset_temporal_xy'])
 print('3Ddist',d['anchor_distance_world'],'delta',d['temporal_vs_baseline_world'],'terms',d['data_terms'])
for k in ['same_reading','chains','rotations','gradient','selected_poses']: print('\n',k,json.dumps(x[k],indent=2))
print('\nSELECT ankle')
for q in x['selected_observations']:
 if q['joint']=='ankle.L':print(q)
