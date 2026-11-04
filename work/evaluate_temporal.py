import sys,os,json,numpy as np,torch
sys.path.insert(0,'/data/video2rig/code')
from v2r.geom import Skeleton,quat_to_mat_np,euler_xyz_np
from v2r.stabilize import temporal_metrics
p='/data/video2rig/projects/lizard'
rig=json.load(open(p+'/rig.json'));st=json.load(open(p+'/studio.json'));sk=Skeleton(rig)
gt=json.load(open(p+'/exam/frames/motion_gt.json'))
G=np.array([f['G'] for f in gt['frames']]);R=quat_to_mat_np(np.array([f['quat'] for f in gt['frames']]))
with torch.no_grad():wgt,kgt=sk.fk_rot(torch.tensor(G,dtype=torch.float32),torch.tensor(R,dtype=torch.float32))
wgt,kgt=wgt.numpy(),kgt.numpy()
report={}
for name in ['exam','exam_temporal','xiyi','xiyi_temporal','xiyi2','xiyi2_stable','xiyi2_temporal']:
 m=dict(np.load(p+'/out/'+name+'/motion.npz'));rd=dict(np.load(p+'/out/'+name+'/reading.npz'))
 a=temporal_metrics(rig,m,st,rd);a.pop('per_bone')
 if name.startswith('exam'):
  w,k=sk.fk_np(m['G'],m['eul']);d0=sk.bone_dirs(wgt)[:,sk.active];d1=sk.bone_dirs(w)[:,sk.active]
  ang=np.degrees(np.arccos(np.clip((d0*d1).sum(-1),-1,1)))
  a.update(gt_joint_mean_pct_size=float(np.linalg.norm(k-kgt,axis=-1).mean()/sk.size*100),gt_bone_mean_deg=float(ang.mean()))
 report[name]=a
 print(name,json.dumps(a))
json.dump(report,open(p+'/temporal_comparison.json','w'),indent=1)
