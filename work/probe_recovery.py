import json,sys,time,numpy as np,torch
sys.path.insert(0,'/data/video2rig/code');torch.set_num_threads(2)
from v2r.geom import Skeleton,Cams
from v2r.recovery import recovery_evidence,bone_support
from v2r.solve_temporal import fit_temporal
from v2r.stabilize import temporal_metrics
p='/data/video2rig/projects/lizard';name=sys.argv[1] if len(sys.argv)>1 else 'xiyi2_temporal_web'
r=json.load(open(p+'/rig.json'));st=json.load(open(p+'/studio.json'));sk=Skeleton(r)
rd=dict(np.load(p+'/out/'+name+'/reading.npz'));m=dict(np.load(p+'/out/'+name+'/motion.npz'));fps=float(m['fps'])
proj=np.moveaxis(Cams(st).project_np(m['kp']),0,1)[...,:2]
scale=np.median(np.ptp(rd['uv'].reshape(-1,sk.K,2),axis=1).max(-1))
w,d=recovery_evidence(rd['uv'],rd['conf_fit'],proj,scale)
bs=bone_support(sk,d['point_support']); initial={'G':m['G'],'eul':m['eul']}
print('weights ankle',[(k['name'],np.median(rd['conf_fit'][72:,:,i],axis=0).tolist(),np.median(w[72:,:,i],axis=0).tolist()) for i,k in enumerate(r['keypoints']) if k['name'] in ['ankle.L','ankle.L.tip']])
for strength in [10.,100.]:
 t=time.time();sol=fit_temporal(r,st,rd['uv'],w,rd.get('X_fit',rd['X']),fps,depth=rd['depth'],device='cpu',iters=300,initial=initial,verbose=False,triangulation_factor=d['triangulation_factor'],orientation_support=bs,speed_strength=strength)
 mot={'G':sol['G'],'eul':sol['eul'],'kp':sol['kp'],'quat':sk.local_quats(sol['eul']),'fps':fps}
 a=temporal_metrics(r,mot,st,rd);a.pop('per_bone');print('STRENGTH',strength,'time',time.time()-t,json.dumps(a))
 for ki in [22,23]:
  x=mot['kp'][84:,ki];print('foot',ki,'range%',np.ptp(x,axis=0)/sk.size*100,'step90%',np.percentile(np.linalg.norm(np.diff(x,axis=0),axis=-1),90)/sk.size*100)
 np.savez('/tmp/'+name+'_recovery_'+str(strength)+'.npz',**mot)
