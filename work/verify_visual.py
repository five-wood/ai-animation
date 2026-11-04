import json,sys,numpy as np,pathlib
sys.path.insert(0,'/data/video2rig/code')
from v2r.geom import Skeleton,Cams
p=pathlib.Path('/data/video2rig/projects/lizard');r=json.loads((p/'rig.json').read_text());sk=Skeleton(r)
a=dict(np.load(p/'out/xiyi2_temporal_web/motion.npz'));b=dict(np.load(p/'out/xiyi2_visual/motion.npz'))
rep=json.loads((p/'out/xiyi2_visual/visual_refinement.json').read_text())
print('metrics')
for tag in ('before','after'):
 print(tag,{k:v for k,v in rep[tag].items() if k!='per_bone'})
print('interval left hind', [i for i in rep['intervals'] if i['name'] in ['ankle.L','ankle.L.tip']])
for name in ['xiyi2_stable','xiyi2_temporal_web','xiyi2_visual']:
 mo=dict(np.load(p/'out'/name/'motion.npz'))
 for k in [22,23]:
  x=mo['kp'][96:,k];v=np.linalg.norm(np.diff(x,axis=0),axis=-1)
  print(name,r['keypoints'][k]['name'],'late world span pct',np.linalg.norm(np.ptp(x,axis=0))/sk.size*100,'step90',np.percentile(v,90)/sk.size*100)
bl=np.array(json.loads((p/'out/xiyi2_visual/blender_kp3d.json').read_text()))
assert np.max(abs(bl-b['kp']))<1e-5
assert np.all(b['eul']>=sk.limits[sk.active,:,0]-1e-6) and np.all(b['eul']<=sk.limits[sk.active,:,1]+1e-6)
print('limits and export match',np.max(abs(bl-b['kp'])))
rd0=dict(np.load(p/'out/xiyi2_temporal_web/reading.npz'));rd1=dict(np.load(p/'out/xiyi2_visual/reading.npz'))
assert all(np.array_equal(rd0[k],rd1[k]) for k in rd0)
print('raw observations unchanged')
print('max angle change',np.max(abs(a['eul']-b['eul']))*180/np.pi)
