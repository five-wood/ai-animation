"""Read-only NumPy audit. Writes JSON only to explicitly provided /tmp output.
No model imports/inference/training/GPU. thresholds fixed a priori as requested.
"""
from pathlib import Path
import argparse,json,hashlib
import numpy as np
TH=[0,.01,.02,.04,.08]
def stat(x):
 x=np.asarray(x);x=x[np.isfinite(x)]
 return {'n':int(x.size),'mean':float(x.mean()) if x.size else None,'median':float(np.median(x)) if x.size else None,'p90':float(np.percentile(x,90)) if x.size else None}
def tables(d,g,mask):
 mask=mask & np.isfinite(d)&np.isfinite(g)&(np.abs(g)>1e-10)
 n=int(mask.sum()); out=[]
 for th in TH:
  sel=mask&(np.abs(d)>th); k=int(sel.sum());wrong=int(((d*g<=0)&sel).sum())
  out.append({'threshold':th,'n_base':n,'n':k,'coverage':k/n if n else None,'accuracy':1-wrong/k if k else None,'error_rate':wrong/k if k else None,'wrong':wrong,'error_coverage':wrong/n if n else None,'pair_diff_MAE':float(np.abs(d-g)[sel].mean()) if k else None})
 return out
def infer_perm(a,rig):
 raw=a['uv_raw'];uv=a['uv'];T,V,K=raw.shape[:3];perm=np.broadcast_to(np.arange(K),(T,V,K)).copy();unknown=[]
 for k,kp in enumerate(rig['keypoints']):
  m=kp['mirror'];same=np.linalg.norm(uv[:,:,k]-raw[:,:,k],axis=-1)
  if m>=0:
   other=np.linalg.norm(uv[:,:,k]-raw[:,:,m],axis=-1); take=(other<1e-4)&(same>1e-4);perm[:,:,k][take]=m
   bad=(same>1e-4)&~take
  else:bad=same>1e-4
  if bad.any():unknown.append([k,int(bad.sum())])
 return perm,unknown
def analyze(p):
 rig=json.loads((p/'rig.json').read_text());studio=json.loads((p/'studio.json').read_text());K=len(rig['keypoints']);size=rig['size']
 pairs=[(i,k['mirror']) for i,k in enumerate(rig['keypoints']) if k['mirror']>i]
 li=np.array([x[0] for x in pairs]);ri=np.array([x[1] for x in pairs]);pn=[rig['keypoints'][i]['name']+'/'+rig['keypoints'][j]['name'] for i,j in pairs]
 ai=pn.index('ankle.L/ankle.R'); R=np.array([c['R'] for c in studio['cams']]);t=np.array([c['t'] for c in studio['cams']]);views=[c['name'] for c in studio['cams']]
 gt=json.loads((p/'exam/frames/motion_gt.json').read_text())['frames'];z=np.array([[np.array(f[v]['uvz'])[:,2] for v in views] for f in gt]);gz=(z-z.mean(-1,keepdims=True))/size;vis=np.array([[f[v]['vis'] for v in views] for f in gt])
 results={};arrays={}
 for f in sorted((p/'out').glob('*/reading.npz')):
  a=dict(np.load(f));arrays[f.parent.name]=a
  perm,unknown=infer_perm(a,rig);sw=perm!=np.arange(K);d=a['depth'].astype(float);dp=d[:,:,li]-d[:,:,ri];allmask=np.ones(dp.shape,bool)
  summary=json.loads((f.parent/'summary.json').read_text()) if (f.parent/'summary.json').exists() else {};check=json.loads((f.parent/'check.json').read_text()) if (f.parent/'check.json').exists() else {}
  r={'solver':summary.get('solver'),'saved_swap_count':check.get('label_swaps_fixed'),'swapped_keypoint_slots':int(sw.sum()),'mapping_unknown':unknown,'depth_sha256':hashlib.sha256(a['depth'].tobytes()).hexdigest(),'conf':stat(a['conf']),'conf_fit':stat(a['conf_fit']) if 'conf_fit'in a else None,'depth_frame_mean_abs':stat(abs(d.mean(-1))),'depth_temporal_step':stat(abs(np.diff(d,axis=0))),'depth_temporal_second_diff':stat(abs(np.diff(d,n=2,axis=0))),'pair_diff_temporal_step':stat(abs(np.diff(dp,axis=0)))}
  # same-two-view triangulation is a correlated consistency comparator, NOT GT
  tri=np.einsum('vij,tkj->tvki',R,a['X'])+t[None,:,None,:];tz=tri[:,:,:,2]/size;td=tz[:,:,li]-tz[:,:,ri]
  r['triang_consistency_NOT_GT']={'all_pairs':tables(dp,td,allmask),'ankle':tables(dp[:,:,ai],td[:,:,ai],allmask[:,:,ai]),'by_view':{v:tables(dp[:,vi],td[:,vi],allmask[:,vi]) for vi,v in enumerate(views)}}
  r['stability']={}
  for label,ds in [('all_pairs',dp),('ankle',dp[:,:,ai])]:
   rr=[]
   for th in TH:
    strong=abs(ds)>th;both=strong[1:]&strong[:-1];flips=(ds[1:]*ds[:-1]<0)&both
    rr.append({'threshold':th,'sample_coverage':float(strong.mean()),'adjacent_kept':int(both.sum()),'adjacent_sign_flips':int(flips.sum()),'flip_rate':float(flips.sum()/both.sum()) if both.any() else None})
   r['stability'][label]=rr
  if f.parent.name in ('exam','exam_temporal'):
   diff=dp;gd=gz[:,:,li]-gz[:,:,ri];err=abs(d-gz)
   point_both=(vis==0).all(axis=1);point_both=np.broadcast_to(point_both[:,None,:],vis.shape)
   pmasks={'all':np.ones(d.shape,bool),'visible_same_view':vis>0,'occluded_same_view':vis==0,'keypoint_both_views_occluded':point_both,'not_swapped_slots':~sw,'swapped_slots':sw}
   r['GT_point_MAE']={name:stat(err[mask]) for name,mask in pmasks.items()};r['GT_point_MAE']['by_view']={v:stat(err[:,vi]) for vi,v in enumerate(views)};r['GT_point_MAE']['ankle_L']={v:stat(err[:,vi,22]) for vi,v in enumerate(views)};r['GT_point_MAE']['ankle_R']={v:stat(err[:,vi,26]) for vi,v in enumerate(views)}
   pair_both=(vis[:,:,li]==0)&(vis[:,:,ri]==0);either=(vis[:,:,li]==0)|(vis[:,:,ri]==0)
   pair_involves_biview=(point_both[:,:,li]|point_both[:,:,ri]);noswap=~(sw[:,:,li]|sw[:,:,ri])
   masks={'all':allmask,'pair_both_occluded_same_view':pair_both,'pair_either_occluded_same_view':either,'pair_has_keypoint_occluded_both_views':pair_involves_biview,'pair_all_four_vis_zero':pair_both.all(axis=1)[:,None,:]&allmask,'not_swapped_pair_samples':noswap,'swapped_pair_samples':~noswap,'conf_pair_min_ge_0.5':np.minimum(a['conf'][:,:,li],a['conf'][:,:,ri])>=.5}
   if 'conf_fit'in a:masks['conf_fit_pair_min_ge_0.5']=np.minimum(a['conf_fit'][:,:,li],a['conf_fit'][:,:,ri])>=.5
   r['GT_pair_order']={n:tables(diff,gd,m) for n,m in masks.items()};r['GT_pair_order']['by_view']={v:tables(diff[:,vi],gd[:,vi],allmask[:,vi]) for vi,v in enumerate(views)}
   r['GT_ankle_order']={n:tables(diff[:,:,ai],gd[:,:,ai],m[:,:,ai]) for n,m in masks.items()};r['GT_ankle_order']['by_view']={v:tables(diff[:,vi,ai],gd[:,vi,ai],allmask[:,vi,ai]) for vi,v in enumerate(views)}
   r['GT_by_pair']={pn[j]:tables(diff[:,:,j],gd[:,:,j],allmask[:,:,j]) for j in range(len(pairs))}
   r['GT_ankle_confidence']={v:{'wrong_conf':stat(a['conf'][:,vi,[22,26]].min(-1)[diff[:,vi,ai]*gd[:,vi,ai]<=0]),'correct_conf':stat(a['conf'][:,vi,[22,26]].min(-1)[diff[:,vi,ai]*gd[:,vi,ai]>0])} for vi,v in enumerate(views)}
  results[f.parent.name]=r
 # Compare same reader legacy/temporal artifacts to infer actual saved-depth alignment.
 comparisons=[]
 for ln,tn in [('exam','exam_temporal'),('xiyi','xiyi_temporal'),('xiyi2','xiyi2_temporal_web'),('xiyi3','xiyi3_temporal'),('xiyi3_clean','xiyi3_clean_temporal')]:
  if ln not in arrays or tn not in arrays:continue
  a,b=arrays[ln],arrays[tn];perm,unknown=infer_perm(a,rig);expected=np.take_along_axis(a['depth'],perm,axis=2);sw=perm!=np.arange(K)
  rr={'legacy':ln,'temporal':tn,'uv_raw_equal':bool(np.array_equal(a['uv_raw'],b['uv_raw'])),'raw_depth_equal':bool(np.array_equal(a['depth'],b['depth'])),'temporal_equals_permuted_legacy':bool(np.array_equal(expected,b['depth'])),'max_expected_depth_delta':float(abs(expected-b['depth']).max()),'swapped_slots':int(sw.sum()),'locations':[]}
  for ti,vi,ki in np.argwhere(sw):rr['locations'].append({'frame':int(ti),'view':views[vi],'kp':rig['keypoints'][ki]['name'],'raw_depth':float(a['depth'][ti,vi,ki]),'expected_aligned':float(expected[ti,vi,ki]),'temporal_saved':float(b['depth'][ti,vi,ki])})
  comparisons.append(rr)
 return {'size':size,'views':views,'mirror_pairs':pn,'rig_mirror_reciprocal':all(rig['keypoints'][j]['mirror']==i for i,j in pairs),'GT_frames':len(gt),'runs':results,'artifact_alignment_comparisons':comparisons}
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--output',default='/tmp/v2r_depth_audit.json');args=ap.parse_args()
 out={'thresholds_fixed_a_priori':TH,'method':'GT=(uvz[:,:,2]-mean_over_all_K)/rig.size; positive z = farther; strictly |pred pair difference| > threshold; denominator per subset; ties in GT excluded, ties in prediction incorrect. Real-video triang is NOT GT. Temporal stability does not imply accuracy.','projects':{n:analyze(Path('/data/video2rig/projects')/n) for n in ['lizard','lizard_clean']}}
 Path(args.output).write_text(json.dumps(out,indent=2,allow_nan=False));print(args.output)
 for p,d in out['projects'].items():
  print(p,'alignment',d['artifact_alignment_comparisons'])
  for n,r in d['runs'].items():
   if 'GT_point_MAE'in r:print(p,n,'GT point',r['GT_point_MAE'],'pair',r['GT_pair_order']['all'],'ankle',r['GT_ankle_order']['all'],'double',r['GT_pair_order']['pair_both_occluded_same_view'],'ankle double',r['GT_ankle_order']['pair_both_occluded_same_view'])
