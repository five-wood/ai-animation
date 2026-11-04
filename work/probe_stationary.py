import json,sys,numpy as np,torch,time
sys.path.insert(0,'/data/video2rig/code');torch.set_num_threads(2)
from v2r.geom import Cams
from v2r.read import read_video_frames,split_views,find_split
from v2r.image_motion import stationary_evidence
p='/data/video2rig/projects/lizard';rig=json.load(open(p+'/rig.json'));st=json.load(open(p+'/studio.json'));m=dict(np.load(p+'/out/xiyi2_temporal_web/motion.npz'))
proj=np.moveaxis(Cams(st).project_np(m['kp']),0,1)[...,:2]
fr,fps=read_video_frames('/data/video2rig/videos/xiyi2.mp4');sp=find_split(fr);panels=[split_views(f,sp,st)[0] for f in fr]
t=time.time();f,d=stationary_evidence(panels,proj,fps)
print('time',time.time()-t,'split',sp,'all',np.mean(f>0))
for i,k in enumerate(rig['keypoints']):
 if any(s in k['name'] for s in ['hip.L','knee.L','ankle.L']):
  print(k['name'],'factor late',np.mean(f[84:,:,i],axis=0),'counts late',np.median(d['flow_support_count'][84:,:,i],axis=0),'speed late',np.median(d['flow_speed_px_per_second'][84:,:,i],axis=0))
np.savez('/tmp/v2r_stationary_xiyi2.npz',**d)
