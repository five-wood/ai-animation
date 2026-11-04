import json,sys,numpy as np,torch,time
sys.path.insert(0,'/data/video2rig/code');torch.set_num_threads(2)
from v2r.geom import Cams,Skeleton,quat_to_mat_np
from v2r.image_motion import stationary_evidence,stationary_segments
from v2r.read import read_video_frames,split_views,find_split
from v2r.solve_temporal import fit_temporal
p='/data/video2rig/projects/lizard';r=json.load(open(p+'/rig.json'));st=json.load(open(p+'/studio.json'));sk=Skeleton(r);c=Cams(st)
n='exam_temporal';m=dict(np.load(p+'/out/'+n+'/motion.npz'));rd=dict(np.load(p+'/out/'+n+'/reading.npz'))
frames,fps=read_video_frames(p+'/exam/exam_video.mp4');panels=[split_views(f,640,st)[0] for f in frames]
proj=np.moveaxis(c.project_np(m['kp']),0,1)[...,:2]
t=time.time();_,e=stationary_evidence(panels,proj,fps);starts,w,ints=stationary_segments(e,fps);print('segments',len(ints),'weighted fraction',np.mean(w>0),'time',time.time()-t)
sol=fit_temporal(r,st,rd['uv'],rd['conf_fit'],rd['X_fit'],fps,rd['depth'],device='cpu',initial=m,iters=300,verbose=False,stationary_starts=starts,stationary_segment_weights=w,stationary_anchor_strength=1000)
gt=json.load(open(p+'/exam/frames/motion_gt.json'));G=np.array([f['G'] for f in gt['frames']]);R=quat_to_mat_np(np.array([f['quat'] for f in gt['frames']]))
with torch.no_grad():wg,kg=sk.fk_rot(torch.tensor(G,dtype=torch.float32),torch.tensor(R,dtype=torch.float32))
for tag,mo in [('before',m),('after',sol)]:
 _,kp=sk.fk_np(mo['G'],mo['eul']);world,_=sk.fk_np(mo['G'],mo['eul'])
 ang=np.degrees(np.arccos(np.clip((sk.bone_dirs(world)[:,sk.active]*sk.bone_dirs(wg.numpy())[:,sk.active]).sum(-1),-1,1)))
 print(tag,'joint%',np.linalg.norm(kp-kg.numpy(),axis=-1).mean()/sk.size*100,'bone',ang.mean())
np.savez('/tmp/exam_stationary_evidence.npz',starts=starts,segment_weights=w,**e)
