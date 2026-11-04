import sys,os,time,json,numpy as np
sys.path.insert(0,'/data/video2rig/code')
from v2r.solve_temporal import fit_temporal
from v2r.stabilize import temporal_metrics
from v2r.geom import Skeleton
p='/data/video2rig/projects/lizard'
rig=json.load(open(p+'/rig.json'));studio=json.load(open(p+'/studio.json'))
name=sys.argv[1] if len(sys.argv)>1 else 'xiyi2'
r=dict(np.load(p+'/out/'+name+'/reading.npz'));old=dict(np.load(p+'/out/'+name+'/motion.npz'))
fps=float(old['fps']);w=r.get('conf_fit',r['conf']);X=r.get('X_fit',r['X'])
initial={'G':old['G'],'eul':old['eul']}
t=time.time()
sol=fit_temporal(rig,studio,r['uv'],w,X,fps,r['depth'],initial=initial,verbose=False,
 position_strength=400.,local_strength=24.,root_strength=40.)
sk=sol['skeleton'];mot={'G':sol['G'],'eul':sol['eul'],'kp':sol['kp'],'quat':sk.local_quats(sol['eul']),'fps':fps}
for tag,m in [('old',old),('temporal',mot)]:
 a=temporal_metrics(rig,m,studio,r);print(tag,json.dumps({k:v for k,v in a.items() if k!='per_bone'}))
print('time',time.time()-t,sol['solver_info'])
np.savez('/tmp/'+name+'_temporal_probe.npz',**mot)
