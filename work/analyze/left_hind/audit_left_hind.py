"""Read-only CPU audit; remote outputs restricted to /tmp/left_hind_audit*."""
import os,sys,json,csv,hashlib
os.environ['CUDA_VISIBLE_DEVICES']=''
import numpy as np
sys.path.insert(0,'/data/video2rig/code')
import torch
torch.set_num_threads(1)
from v2r.geom import Cams,Skeleton,euler_xyz_np
P='/data/video2rig/projects/lizard'; O='/tmp/left_hind_audit'
rig=json.load(open(P+'/rig.json')); studio=json.load(open(P+'/studio.json'))
r={x:dict(np.load(P+'/out/'+y+'/reading.npz')) for x,y in [('baseline','xiyi2'),('temporal','xiyi2_temporal_web')]}
m={x:dict(np.load(P+'/out/'+y+'/motion.npz')) for x,y in [('baseline','xiyi2'),('temporal','xiyi2_temporal_web')]}
w=dict(np.load(P+'/out/xiyi2_temporal_web/observation_weights.npz')); cams=Cams(studio,device='cpu');sk=Skeleton(rig,device='cpu')
uv=r['temporal']['uv']; weights=w['weights']; fps=float(m['temporal']['fps']); T=len(uv); late=np.arange(T)/fps>=3
proj={x:cams.project_np(v['kp']).transpose(1,0,2,3)[...,:2] for x,v in m.items()}
rep={x:np.linalg.norm(p-uv,axis=-1) for x,p in proj.items()}
X=r['temporal']['X_fit']; xp=cams.project_np(X).transpose(1,0,2,3)[...,:2]
scale=max(float(np.median(np.ptp(uv.reshape(-1,uv.shape[2],2),axis=1).max(-1))),1.)
norm=max(float(weights.mean()),.05)
factors=['bounds_factor','peak_factor','temporal_factor','bone_factor','ray_factor','ambiguity_factor','spread_factor']
def stats(a):
 a=np.asarray(a,float);return dict(n=int(a.size),min=float(a.min()),median=float(np.median(a)),mean=float(a.mean()),p90=float(np.percentile(a,90)),max=float(a.max()))
def vecstats(a):
 a=np.asarray(a,float);return dict(mean=a.mean(0).tolist(),min=a.min(0).tolist(),max=a.max(0).tolist())
def angle(a,b):
 return np.degrees(np.arccos(np.clip(np.sum(a*b,axis=-1)/(np.linalg.norm(a,axis=-1)*np.linalg.norm(b,axis=-1)).clip(1e-15),-1,1)))
def chain(X):
 return {'upper_len':np.linalg.norm(X[:,21]-X[:,20],axis=-1),'lower_len':np.linalg.norm(X[:,22]-X[:,21],axis=-1),'knee_inner_deg':angle(X[:,20]-X[:,21],X[:,22]-X[:,21])}
result={'fps':fps,'T':T,'late_frames':[int(np.flatnonzero(late)[0]),int(np.flatnonzero(late)[-1])],'scale_px':scale,'rig_size':sk.size,'weight_norm':norm,'indices':{'hip.L':{'kp':20,'bone':16,'active':13},'knee.L':{'kp':21,'bone':17,'active':14},'ankle.L':{'kp':22,'bone':18,'active':None}},'same_reading':{k:float(np.max(np.abs(r['baseline'][k]-r['temporal'][k]))) for k in ['uv_reader','uv_raw','uv','conf','res','X','depth']},'joints':{},'chains':{},'rotations':{},'gradient':{}}
for k in [20,21,22,23]:
 name=rig['keypoints'][k]['name'];d={}
 for v in range(2):
  q={};sl=(late,v,k)
  for key in ['peak_raw','weights','temporal_distance_px','temporal_z','ray_residual_px','ambiguity_ratio','spread_px']+factors:q[key]=stats(w[key][sl])
  q['weight_ge_0.5_fraction']=float(np.mean(weights[sl]>=.5));q['uv_px']=vecstats(uv[sl]);q['reproj_temporal_px']=stats(rep['temporal'][sl]);q['reproj_baseline_px']=stats(rep['baseline'][sl]);q['reproj_offset_temporal_xy']=vecstats((proj['temporal']-uv)[sl]);q['anchor_reproj_px']=stats(np.linalg.norm(xp-uv,axis=-1)[sl]);q['uv_vs_reader_max_px']=float(np.linalg.norm(uv-r['temporal']['uv_reader'],axis=-1)[sl].max())
  vel=np.diff(uv[:,v,k],axis=0);acc=np.diff(uv[:,v,k],n=2,axis=0)
  q['step_px']=stats(np.linalg.norm(vel[np.arange(1,T)/fps>=3],axis=-1));q['second_difference_px']=stats(np.linalg.norm(acc[np.arange(1,T-1)/fps>=3],axis=-1))
  q['baseline_weights']=stats(r['baseline']['conf_fit'][sl]);q['early_reproj_px']=stats(rep['temporal'][~late,v,k]);q['early_weight']=stats(weights[~late,v,k]);d[studio['cams'][v]['name']]=q
 d['anchor_distance_world']=stats(np.linalg.norm(m['temporal']['kp'][:,k]-X[:,k],axis=-1)[late]);d['temporal_vs_baseline_world']=stats(np.linalg.norm(m['temporal']['kp'][:,k]-m['baseline']['kp'][:,k],axis=-1)[late]);result['joints'][name]=d
for label,xx in [('baseline',m['baseline']['kp']),('temporal',m['temporal']['kp']),('anchor',X)]:result['chains'][label]={k:stats(v[late]) for k,v in chain(xx).items()}
for b,a in [(16,13),(17,14)]:
 name=rig['bones'][b]['name'];z={}
 for label in ['baseline','temporal']:
  e=np.degrees(m[label]['eul'][:,a]);R=euler_xyz_np(m[label]['eul'][:,a]);dR=R[:-1].transpose(0,2,1)@R[1:];step=np.degrees(np.arccos(np.clip((np.trace(dR,axis1=-2,axis2=-1)-1)/2,-1,1)))
  z[label]={'euler_xyz_deg':vecstats(e[late]),'rotation_step_deg':stats(step[np.arange(1,T)/fps>=3])}
  z[label]['near_limit_1deg_fraction']=float(np.mean(np.min(np.abs(e[late,:,:,None]-np.array(rig['bones'][b]['limits'])[None,:,:]),axis=-1)<1)) if False else None
 R1=euler_xyz_np(m['baseline']['eul'][:,a]);R2=euler_xyz_np(m['temporal']['eul'][:,a]);rd=R1.transpose(0,2,1)@R2
 z['difference_deg']=stats(np.degrees(np.arccos(np.clip((np.trace(rd,axis1=-2,axis2=-1)-1)/2,-1,1)))[late]);result['rotations'][name]=z
# Actual solver data terms and autograd, no optimisation.
td=lambda x:torch.tensor(np.asarray(x),dtype=torch.float32,device='cpu')
E=td(m['temporal']['eul']).requires_grad_(True);G=td(m['temporal']['G']);_,kp=sk.fk(G,E);pred,z=cams.project_t(kp)
e2=((pred-td(uv))/scale).square().sum(-1);im=td(weights)*(e2/(e2+.03**2))*.03**2*1000/norm
tri=td(weights.min(1))*((kp-td(X)).square().sum(-1)/sk.size**2).clamp(max=.04)*.5/norm
for label,ks in [('all',list(range(sk.K))),('left_chain',[20,21,22,23])]:
 # Keep original denominators, masking rather than averaging subset.
 mask2=torch.zeros_like(im);mask2[:,:,ks]=1;mask3=torch.zeros_like(tri);mask3[:,ks]=1
 image=(im*mask2).mean();pos3=(tri*mask3).mean()
 gi=torch.autograd.grad(image,E,retain_graph=True)[0].detach().numpy();gt=torch.autograd.grad(pos3,E,retain_graph=True)[0].detach().numpy()
 q={'image_term':float(image),'tri3d_term':float(pos3),'bones':{}}
 for b,a in [(16,13),(17,14)]:
  ii=gi[late,a];tt=gt[late,a];ni=np.linalg.norm(ii,axis=-1);nt=np.linalg.norm(tt,axis=-1);cos=(ii*tt).sum(-1)/(ni*nt).clip(1e-20)
  q['bones'][rig['bones'][b]['name']]={'image_gradient_norm':stats(ni),'tri3d_gradient_norm':stats(nt),'tri_image_norm_ratio':stats(nt/ni.clip(1e-20)),'gradient_cosine':stats(cos),'same_direction_fraction':float(np.mean(cos>0))}
 result['gradient'][label]=q
# Individual point loss terms and world-space gradient alignment.
gip=torch.autograd.grad(im.mean(),kp,retain_graph=True)[0].detach().numpy();gtp=torch.autograd.grad(tri.mean(),kp,retain_graph=True)[0].detach().numpy()
for k in [20,21,22,23]:
 ii=gip[late,k];tt=gtp[late,k];ni=np.linalg.norm(ii,axis=-1);nt=np.linalg.norm(tt,axis=-1);co=(ii*tt).sum(-1)/(ni*nt).clip(1e-20)
 result['joints'][rig['keypoints'][k]['name']]['data_terms']={'late_image_sum_over_full_denominator':float(im.detach().numpy()[late,:,k].sum()/im.numel()),'late_tri3d_sum_over_full_denominator':float(tri.detach().numpy()[late,k].sum()/tri.numel()),'world_grad_norm_ratio':stats(nt/ni.clip(1e-20)),'world_grad_cosine':stats(co),'aligned_fraction':float(np.mean(co>0))}
# Full per-observation evidence and exact selected frame values.
rows=[]
for t in range(T):
 for k in [20,21,22,23]:
  for v in range(2):
   row={'frame':t,'time_s':t/fps,'joint':rig['keypoints'][k]['name'],'view':studio['cams'][v]['name'],'u':float(uv[t,v,k,0]),'v':float(uv[t,v,k,1]),'reader_u':float(r['temporal']['uv_reader'][t,v,k,0]),'reader_v':float(r['temporal']['uv_reader'][t,v,k,1]),'reproj_px':float(rep['temporal'][t,v,k]),'baseline_reproj_px':float(rep['baseline'][t,v,k]),'pred_u':float(proj['temporal'][t,v,k,0]),'pred_v':float(proj['temporal'][t,v,k,1]),'anchor_u':float(xp[t,v,k,0]),'anchor_v':float(xp[t,v,k,1])}
   for key in ['peak_raw','weights','temporal_distance_px','temporal_z','ray_residual_px','ambiguity_ratio','spread_px']+factors:row[key]=float(w[key][t,v,k])
   rows.append(row)
with open(O+'_observations.csv','w') as f:
 dw=csv.DictWriter(f,fieldnames=list(rows[0]));dw.writeheader();dw.writerows(rows)
pose=[]
for t in range(T):
 row={'frame':t,'time_s':t/fps}
 for label in ['baseline','temporal']:
  for b,a in [(16,13),(17,14)]:
   for j,ax in enumerate('xyz'):row[label+'_'+rig['bones'][b]['name']+'_'+ax+'_deg']=float(np.degrees(m[label]['eul'][t,a,j]))
  row[label+'_knee_inner_deg']=float(chain(m[label]['kp'])['knee_inner_deg'][t])
 row['anchor_knee_inner_deg']=float(chain(X)['knee_inner_deg'][t]);pose.append(row)
with open(O+'_poses.csv','w') as f:
 dw=csv.DictWriter(f,fieldnames=list(pose[0]));dw.writeheader();dw.writerows(pose)
result['selected_observations']=[x for x in rows if x['frame'] in [72,84,96,108,120] and x['joint'] in ['hip.L','knee.L','ankle.L']]
result['selected_poses']=[x for x in pose if x['frame'] in [72,84,96,108,120]]
with open(O+'_summary.json','w') as f:json.dump(result,f,indent=2)
print(json.dumps({k:v for k,v in result.items() if k not in ['selected_observations','selected_poses']},indent=2))
