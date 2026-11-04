import os,json,numpy as np,hashlib
base='/data/video2rig/projects/lizard'
rig=json.load(open(base+'/rig.json')); active=[i for i,b in enumerate(rig['bones']) if b['active']]
for i,k in enumerate(rig['keypoints']):
 print('KP',i,k)
for i,b in enumerate(rig['bones']):
 print('BONE',i,b['name'],'parent',b['parent'],'active_index',active.index(i) if i in active else None,'limits',b['limits'])
for out in ['xiyi2','xiyi2_temporal_web']:
 for f in ['reading.npz','motion.npz','observation_weights.npz']:
  p=base+'/out/'+out+'/'+f
  if not os.path.isfile(p):continue
  z=np.load(p)
  print(out,f,hashlib.sha256(open(p,'rb').read()).hexdigest())
  for k in z.files:
   a=z[k];print(k,a.shape,a.dtype,'min',a.min(),'max',a.max())
for f in ['solve_temporal.py','observations.py','pipeline.py','geom.py','solve.py']:
 p='/data/video2rig/code/v2r/'+f
 print('SOURCE_HASH',f,hashlib.sha256(open(p,'rb').read()).hexdigest())
