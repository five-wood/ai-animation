import json,numpy as np,sys,pathlib
sys.path.insert(0,'/data/video2rig/code')
from v2r.geom import Cams,Skeleton
p=pathlib.Path('/data/video2rig/projects/lizard');rig=json.loads((p/'rig.json').read_text());studio=json.loads((p/'studio.json').read_text());cams=Cams(studio);sk=Skeleton(rig)
n='xiyi2_temporal_web';q=dict(np.load(p/'out'/n/'observation_weights.npz'));rd=dict(np.load(p/'out'/n/'reading.npz'));m=dict(np.load(p/'out'/n/'motion.npz'));proj=np.moveaxis(cams.project_np(m['kp']),0,1)[...,:2]
for i,k in enumerate(rig['keypoints']):
 if any(s in k['name'] for s in ['hip.L','knee.L','ankle.L']):
  print('\n',i,k['name'])
  for v in range(2):
   ix=slice(72,None)
   print('view',v,'range obs',np.round(np.ptp(rd['uv'][ix,v,i],axis=0),2),'range proj',np.round(np.ptp(proj[ix,v,i],axis=0),2))
   print('weight med',np.median(q['weights'][ix,v,i]),'conf',np.median(rd['conf'][ix,v,i]),'reproj',np.median(np.linalg.norm(proj[ix,v,i]-rd['uv'][ix,v,i],axis=-1)))
   for fac in ['temporal_factor','bone_factor','ambiguity_factor','spread_factor','ray_factor']:
    print(fac,np.round(np.percentile(q[fac][ix,v,i],[10,50,90]),3))
   print('uv sample',[(t,np.round(rd['uv'][t,v,i],1).tolist(),round(float(q['weights'][t,v,i]),3)) for t in [72,78,84,90,96,108,120]])
print('temporal info',json.loads((p/'out'/n/'solver.json').read_text()))
