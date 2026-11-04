import json,sys,numpy as np,time
sys.path.insert(0,'/data/video2rig/code')
from v2r.contact import contact_refine
from v2r.stabilize import temporal_metrics
p='/data/video2rig/projects/lizard'
r=json.load(open(p+'/rig.json'));s=json.load(open(p+'/studio.json'))
m=dict(np.load(p+'/out/xiyi2_stable/motion.npz'));rd=dict(np.load(p+'/out/xiyi2_stable/reading.npz'))
t=time.time();out,report=contact_refine(r,m,iters=1200)
print('seconds',time.time()-t)
print(json.dumps(report,indent=1))
print('after', {k:v for k,v in temporal_metrics(r,out,s,rd).items() if k!='per_bone'})
for k in ['G']:
 print(k,'exact',np.array_equal(m[k],out[k]))
print('root height exact',np.array_equal(m['kp'][:,0],out['kp'][:,0]))
