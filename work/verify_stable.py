import json,numpy as np,pathlib,sys
sys.path.insert(0,'/data/video2rig/code')
from v2r.geom import Skeleton
p=pathlib.Path('/data/video2rig/projects/lizard')
r=json.loads((p/'rig.json').read_text()); s=Skeleton(r)
a=dict(np.load(p/'out/xiyi2/motion.npz')); b=dict(np.load(p/'out/xiyi2_stable/motion.npz'))
c=json.loads((p/'out/xiyi2_stable/stability.json').read_text())
for key in ('before','after'): print(key,{k:v for k,v in c[key].items() if k!='per_bone'})
bl=np.array(json.loads((p/'out/xiyi2_stable/blender_kp3d.json').read_text()))
print('render FK max error',abs(bl-b['kp']).max())
print('rotation orthogonal max error',abs(b['G'][:,:3,:3].transpose(0,2,1)@b['G'][:,:3,:3]-np.eye(3)).max())
assert abs(bl-b['kp']).max()<1e-5
assert np.all(b['eul']>=s.limits[s.active,:,0]-1e-6) and np.all(b['eul']<=s.limits[s.active,:,1]+1e-6)
print('all joint limits respected')
print('changed joint pos p90 pct size',np.percentile(np.linalg.norm(a['kp']-b['kp'],axis=-1),90)/s.size*100)
print('local accel reduction%',100*(1-c['after']['local_euler_accel_rms_deg_per_frame2']/c['before']['local_euler_accel_rms_deg_per_frame2']))
