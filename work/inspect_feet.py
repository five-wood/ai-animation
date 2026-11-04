import sys,json,numpy as np
sys.path.insert(0,'/data/video2rig/code')
from v2r.geom import Skeleton,Cams
p='/data/video2rig/projects/lizard'
r=json.load(open(p+'/rig.json')); s=Skeleton(r)
m=dict(np.load(p+'/out/xiyi2_stable/motion.npz'))
kp=m['kp']; fps=float(m['fps'])
for i,k in enumerate(r['keypoints']):
 if any(w in k['name'] for w in ('wrist','ankle')):
  print(i,k['name'])
  for start in [0,72,84,96]:
   x=kp[start:,i];v=np.linalg.norm(np.diff(x,axis=0),axis=-1)
   print(' t',start/fps,'rangeXYZ%',np.round(np.ptp(x,axis=0)/s.size*100,3),'step90%',round(np.percentile(v,90)/s.size*100,3),'mean',np.round(np.mean(x,0),4))
print('active',[(i,r['bones'][b]['name']) for i,b in enumerate(s.active)])
print('ground',r['ground_z'])
