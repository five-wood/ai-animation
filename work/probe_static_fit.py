import sys,json,numpy as np,torch,time
sys.path.insert(0,'/data/video2rig/code');torch.set_num_threads(2)
from v2r.solve_temporal import fit_temporal
from v2r.stabilize import temporal_metrics
p='/data/video2rig/projects/lizard';r=json.load(open(p+'/rig.json'));st=json.load(open(p+'/studio.json'))
n='xiyi2_temporal_web';m=dict(np.load(p+'/out/'+n+'/motion.npz'));rd=dict(np.load(p+'/out/'+n+'/reading.npz'));e=dict(np.load('/tmp/v2r_stationary_xiyi2.npz'))
for strength in [1000.,10000.,100000.]:
 t=time.time();sol=fit_temporal(r,st,rd['uv'],rd['conf_fit'],rd['X_fit'],24,rd['depth'],device='cpu',initial=m,iters=400,verbose=False,stationary_factor=e['stationary_factor'],stationary_strength=strength)
 sk=sol['skeleton'];out={'G':sol['G'],'eul':sol['eul'],'kp':sol['kp'],'quat':sk.local_quats(sol['eul']),'fps':24}
 a=temporal_metrics(r,out,st,rd);a.pop('per_bone');print('STRENGTH',strength,'time',time.time()-t,json.dumps(a))
 for i in [22,23]:
  x=out['kp'][84:,i];print('foot',i,'range%',np.ptp(x,axis=0)/sk.size*100,'step90%',np.percentile(np.linalg.norm(np.diff(x,axis=0),axis=-1),90)/sk.size*100)
 np.savez('/tmp/xiyi2_static_'+str(strength)+'.npz',**out)
