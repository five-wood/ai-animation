import sys,json,numpy as np,torch,time
sys.path.insert(0,'/data/video2rig/code');torch.set_num_threads(2)
from v2r.solve_temporal import fit_temporal
from v2r.stabilize import temporal_metrics
from v2r.image_motion import stationary_segments
p='/data/video2rig/projects/lizard';r=json.load(open(p+'/rig.json'));st=json.load(open(p+'/studio.json'))
n='xiyi2_temporal_web';m=dict(np.load(p+'/out/'+n+'/motion.npz'));rd=dict(np.load(p+'/out/'+n+'/reading.npz'));e=dict(np.load('/tmp/v2r_stationary_xiyi2.npz'))
starts,w,ints=stationary_segments(e,24)
print('segments',len(ints),[q for q in ints if q['keypoint'] in [22,23]])
for strength in [100.,1000.,10000.]:
 t=time.time();sol=fit_temporal(r,st,rd['uv'],rd['conf_fit'],rd['X_fit'],24,rd['depth'],device='cpu',initial=m,iters=500,verbose=False,stationary_starts=starts,stationary_segment_weights=w,stationary_anchor_strength=strength)
 sk=sol['skeleton'];out={'G':sol['G'],'eul':sol['eul'],'kp':sol['kp'],'quat':sk.local_quats(sol['eul']),'fps':24}
 a=temporal_metrics(r,out,st,rd);a.pop('per_bone');print('STRENGTH',strength,'time',time.time()-t,json.dumps(a))
 for i in [22,23]:
  x=out['kp'][96:,i];print('foot',i,'range%',np.ptp(x,axis=0)/sk.size*100,'step90%',np.percentile(np.linalg.norm(np.diff(x,axis=0),axis=-1),90)/sk.size*100)
 np.savez('/tmp/xiyi2_segments_'+str(strength)+'.npz',**out)
np.savez('/tmp/xiyi2_segment_evidence.npz',starts=starts,segment_weights=w,**e)
json.dump(ints,open('/tmp/xiyi2_segments.json','w'),indent=1)
