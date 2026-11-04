import json,sys,os
import numpy as np
from scipy.spatial.transform import Rotation
sys.path.insert(0,'/data/video2rig/code')
from v2r.geom import Skeleton,Cams
p='/data/video2rig/projects/lizard'
rig=json.load(open(p+'/rig.json')); studio=json.load(open(p+'/studio.json'))
m=np.load(p+'/out/xiyi2/motion.npz'); r=np.load(p+'/out/xiyi2/reading.npz')
fps=float(m['fps']); e=m['eul']; sk=Skeleton(rig)
print('fps',fps,'keys',m.files,'reading',r.files)
bl=np.array(json.load(open(p+'/out/xiyi2/blender_kp3d.json')))
print('export mismatch',np.abs(bl-m['kp']).max())
Rg=m['G'][:,:3,:3]
rv=Rotation.from_matrix(Rg).as_rotvec()
print('global rotation step deg',np.percentile(np.linalg.norm(np.diff(rv,axis=0),axis=-1)*180/np.pi,[50,90,99]))
print('local euler delta deg',np.percentile(np.linalg.norm(np.diff(e,axis=0),axis=-1)*180/np.pi,[50,90,99]))
print('euler acceleration deg',np.percentile(np.linalg.norm(np.diff(e,n=2,axis=0),axis=-1)*180/np.pi,[50,90,99]))
print('root height range',np.ptp(m['kp'][:,0,2]),'tr z range',np.ptp(m['G'][:,2,3]))
for k in np.argsort(-np.mean(np.linalg.norm(np.diff(e,n=2,axis=0),axis=-1),axis=0))[:10]:
 print(rig['bones'][sk.active[k]]['name'],np.round(np.percentile(np.linalg.norm(np.diff(e[:,k],n=2,axis=0),axis=-1)*180/np.pi,[50,90,99]),2))
print('UV median acceleration px',np.median(np.linalg.norm(np.diff(r['uv'],n=2,axis=0),axis=-1)))
print('uv global p90 acceleration',np.percentile(np.linalg.norm(np.diff(r['uv'],n=2,axis=0),axis=-1),90))
print('kp Z:',np.round(m['kp'][::5,0,2],4).tolist())
