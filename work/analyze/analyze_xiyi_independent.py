import sys,json,os,csv
import numpy as np
ROOT='/data/video2rig'
sys.path.insert(0,ROOT+'/code')
from v2r.geom import Cams
from v2r.solve import triangulate_all,consistency_report
import cv2
P=ROOT+'/projects/lizard'; OUT='/tmp/xiyi_independent';os.makedirs(OUT,exist_ok=True)
z=np.load(P+'/out/xiyi/reading.npz');uv=z['uv'];cf=z['conf'];res=z['res'];rig=json.load(open(P+'/rig.json'));studio=json.load(open(P+'/studio.json'));cams=Cams(studio)
T,V,K,_=uv.shape;size=json.load(open(P+'/out/xiyi/check.json'))['size_px']
groups={'all':list(range(K)),'axis':[i for i,k in enumerate(rig['keypoints']) if k['side']=='M'],'limbs':[i for i,k in enumerate(rig['keypoints']) if k['side']!='M'],'head_trunk':list(range(7)),'tail':list(range(15,20))}
rmax=res.max(-1);good=cf.min(1)>.3
# Exact epipolar signed Sampson error; sign consistent from common F.
C0=-cams.R[0].T@cams.t[0];e=cams.P[1]@np.r_[C0,1];ex=np.array([[0,-e[2],e[1]],[e[2],0,-e[0]],[-e[1],e[0],0]]);F=ex@cams.P[1]@np.linalg.pinv(cams.P[0]);F/=np.linalg.norm(F)
def signed_epi(a,b):
 a=np.concatenate([a,np.ones(a.shape[:-1]+(1,))],-1);b=np.concatenate([b,np.ones(b.shape[:-1]+(1,))],-1);fa=a@F.T;fb=b@F
 return np.sum(b*fa,-1)/np.sqrt(np.sum(fa[...,:2]**2,-1)+np.sum(fb[...,:2]**2,-1))
e0=signed_epi(uv[0,0],uv[0,1]);scan=[]
segments={'all_overlap':np.arange(12,T-12),'motion31_89':np.arange(31,90),'early_motion31_59':np.arange(31,60),'late_motion60_89':np.arange(60,90)}
for d in range(-12,13):
 for seg,tt in segments.items():
  u=uv[tt].copy();c=cf[tt].copy();u[:,1]=uv[tt+d,1];c[:,1]=cf[tt+d,1];X,r=triangulate_all(cams,u,c);gm=c.min(1)>.3;rm=np.where(gm,r.max(-1),np.nan);ep=signed_epi(u[:,0],u[:,1])-e0
  # Fixed frame-0 displacement; each view retains its own frame-0 reference for every d.
  disp=(u[:,0,:,1]-uv[0,0,:,1])-(u[:,1,:,1]-uv[0,1,:,1])
  for group,idx in groups.items():
   rr=rm[:,idx];dd=np.where(gm[:,idx],disp[:,idx],np.nan);ee=np.where(gm[:,idx],ep[:,idx],np.nan)
   scan.append(dict(delay=d,segment=seg,group=group,ray_frame_median_px=float(np.nanmedian(np.nanmedian(rr,axis=1))),ray_point_median_px=float(np.nanmedian(rr)),ray_rms_px=float(np.sqrt(np.nanmean(rr**2))),dy0_mae_px=float(np.nanmedian(np.abs(dd))),dy0_rmse_px=float(np.sqrt(np.nanmean(dd**2))),epi0_mae_px=float(np.nanmedian(abs(ee))),epi0_rmse_px=float(np.sqrt(np.nanmean(ee**2)))))
with open(OUT+'/delay_scan.csv','w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=scan[0].keys());w.writeheader();w.writerows(scan)
check=json.load(open(P+'/out/xiyi/check.json'));stats={}
for seg,tt in {'all':np.arange(T),'motion31_89':np.arange(31,90),'static0_29':np.arange(30),'return95_120':np.arange(95,121)}.items():
 stats[seg]={}
 for group,idx in groups.items():
  rr=np.where(good[tt][:,idx],rmax[tt][:,idx],np.nan);fr=np.nanmedian(rr,axis=1)
  stats[seg][group]={'point_median_px':float(np.nanmedian(rr)),'point_p90_px':float(np.nanpercentile(rr,90)),'frame_median_px':float(np.nanmedian(fr)),'frame_median_pct':float(np.nanmedian(fr)/size*100),'frames_above3pct':int((fr>size*.03).sum()),'n_frames':len(tt)}
pointstats=[]
for i,k in enumerate(rig['keypoints']):
 tt=np.arange(31,90);r=rmax[tt,i];pointstats.append({'id':i,'name':k['name'],'motion_median_px':float(np.median(r)),'motion_p90_px':float(np.percentile(r,90)),'motion_median_pct':float(np.median(r)/size*100),'motion_conf_median':float(np.median(cf[tt,:,i])),'static_median_px':float(np.median(rmax[:30,i])),'max_frame':int(rmax[:,i].argmax())})
result={'size_px':size,'groups':groups,'stats':stats,'point_stats':sorted(pointstats,key=lambda d:-d['motion_median_px']),'bad_frames':np.flatnonzero(check['bad_mask']).tolist(),'bonelen_median':float(np.median(check['frame_bonelen_err'])),'bonelen_max':float(np.max(check['frame_bonelen_err'])),'conf_min':float(cf.min()),'conf_median':float(np.median(cf)),'delay_definition':'front(t) paired with side(t+d); positive d means use later side image; fixed time sets for all d; frame0 reference not shifted','best_scan':{}}
for seg in segments:
 result['best_scan'][seg]={}
 for group in ['all','axis','limbs','head_trunk','tail']:
  rows=[r for r in scan if r['segment']==seg and r['group']==group];zero=[r for r in rows if r['delay']==0][0];result['best_scan'][seg][group]={'zero':zero,'best_ray':min(rows,key=lambda r:r['ray_frame_median_px']),'best_dy0':min(rows,key=lambda r:r['dy0_rmse_px']),'best_epi0':min(rows,key=lambda r:r['epi0_rmse_px'])}
json.dump(result,open(OUT+'/analysis.json','w'),indent=2)
# Draw source frames with post-swap reader labels, not fitted skeleton.
cap=cv2.VideoCapture(ROOT+'/videos/xiyi.mp4');print('video',cap.get(3),cap.get(4),cap.get(5),cap.get(7));frames=[]
while True:
 ok,f=cap.read()
 if not ok:break
 frames.append(f)
cap.release()
colors=[(40,230,255) if k['side']=='M' else ((40,220,40) if k['side']=='L' else (255,130,60)) for k in rig['keypoints']]
for t in [0,24,31,45,60,69,75,82,85,89,96,120]:
 img=frames[t].copy();cv2.putText(img,'SOURCE + READER (not solve), frame %d t=%.3fs'%(t,t/24),(15,30),cv2.FONT_HERSHEY_SIMPLEX,.7,(255,255,255),2)
 for v in range(2):
  for a,b in rig['edges']:
   p=tuple(np.round(uv[t,v,a]+[v*640,0]).astype(int));q=tuple(np.round(uv[t,v,b]+[v*640,0]).astype(int));cv2.line(img,p,q,(160,160,160),1,cv2.LINE_AA)
  for i in range(K):
   p=tuple(np.round(uv[t,v,i]+[v*640,0]).astype(int));cv2.circle(img,p,3,colors[i],-1);cv2.putText(img,str(i),(p[0]+4,p[1]-3),cv2.FONT_HERSHEY_SIMPLEX,.36,colors[i],1,cv2.LINE_AA)
 cv2.imwrite(OUT+'/overlay_%03d.jpg'%t,img);cv2.imwrite(OUT+'/source_%03d.jpg'%t,frames[t])
# Three large comparison sheets: include full source at half scale and overlay full scale.
for bi,ts in enumerate([[0,31,45],[60,69,75],[82,85,89],[96,120]]):
 sheet=np.concatenate([cv2.imread(OUT+'/overlay_%03d.jpg'%t) for t in ts],axis=0);cv2.imwrite(OUT+'/sheet_%d.jpg'%bi,sheet)
print('written',OUT);print(json.dumps({'stats':stats,'point_stats':result['point_stats'][:10],'bonelen_median':result['bonelen_median'],'bonelen_max':result['bonelen_max']}))
