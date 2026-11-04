import sys,json,numpy as np
sys.path.insert(0,'/data/video2rig/code')
from v2r.stabilize import stabilize_arrays,temporal_metrics,regularize
p='/data/video2rig/projects/lizard'
rig=json.load(open(p+'/rig.json')); st=json.load(open(p+'/studio.json'))
m=dict(np.load(p+'/out/xiyi2/motion.npz')); rd=dict(np.load(p+'/out/xiyi2/reading.npz'))
b=temporal_metrics(rig,m,st,rd)
print('BEFORE',json.dumps({k:v for k,v in b.items() if k!='per_bone'}))
for hz in [1,1.5,2,2.5,3]:
 s,n=stabilize_arrays(rig,m,hz); a=temporal_metrics(rig,s,st,rd)
 delta=np.linalg.norm(s['kp']-m['kp'],axis=-1)
 print(hz,'clip',n,'accel',a['local_euler_accel_rms_deg_per_frame2'],'step99',a['local_step_p99_deg'],'amp%',100*a['root_height_range']/b['root_height_range'],'time shift',a['low_hold_centre_seconds']-b['low_hold_centre_seconds'],'reproj',a['reprojection_median_px'],a['reprojection_p90_px'],'kpDeltaP90',np.percentile(delta,90))
# frequency/constant/linear invariance tests
fps=24; t=np.arange(240)/fps
for hz in [.5,6]:
 y=np.sin(2*np.pi*hz*t); s=regularize(y,fps,2)
 print('sine',hz,'gain',np.linalg.norm(s[24:-24])/np.linalg.norm(y[24:-24]))
assert np.max(abs(regularize(np.ones(121),24,2)-1))<1e-9
assert np.max(abs(regularize(np.arange(121),24,2)-np.arange(121)))<1e-9
