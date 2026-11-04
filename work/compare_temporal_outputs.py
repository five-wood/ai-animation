import os,sys,json,numpy as np
sys.path.insert(0,'/data/video2rig/code')
from v2r.stabilize import temporal_metrics
p='/data/video2rig/projects/lizard'
r=json.load(open(p+'/rig.json'));st=json.load(open(p+'/studio.json'))
for name in ['xiyi2','xiyi2_stable','xiyi2_temporal']:
 m=dict(np.load(p+'/out/'+name+'/motion.npz'));rd=dict(np.load(p+'/out/'+name+'/reading.npz'))
 a=temporal_metrics(r,m,st,rd)
 print(name,json.dumps({k:v for k,v in a.items() if k!='per_bone'}))
 if name=='xiyi2':b=a
 else:print('amplitude ratio',a['root_height_range']/b['root_height_range'],'low shift',a['low_hold_centre_seconds']-b['low_hold_centre_seconds'])
 if name.endswith('temporal'):
  q=dict(np.load(p+'/out/'+name+'/observation_weights.npz'))
  for kn in ['head.tip','tail4.tip','ankle.L.tip','wrist.L']:
   ki=next((i for i,k in enumerate(r['keypoints']) if k['name']==kn),None)
   if ki is not None: print(kn,'weights by view',np.median(q['weights'][:,:,ki],axis=0).tolist())
