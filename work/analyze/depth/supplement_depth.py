from pathlib import Path
import json,numpy as np
base=Path('/data/video2rig/projects');out={}
for name in ['lizard','lizard_clean']:
 p=base/name;gt=json.loads((p/'exam/frames/motion_gt.json').read_text())['frames'];rig=json.loads((p/'rig.json').read_text());studio=json.loads((p/'studio.json').read_text());views=[c['name'] for c in studio['cams']];d={}
 for n in ['exam','exam_temporal']:
  f=p/'out'/n/'reading.npz'
  if not f.exists():continue
  a=np.load(f);t=30;v=1;inds=[20,21,22,23,24,25,26,27];gu=np.array(gt[t]['side']['uvz'])[:,:2];gz=np.array(gt[t]['side']['uvz'])[:,2];gz=(gz-gz.mean())/rig['size']
  d[n]={'frame30_side':[{'kp':rig['keypoints'][k]['name'],'vis':gt[t]['side']['vis'][k],'GT_depth':float(gz[k]),'saved_depth':float(a['depth'][t,v,k]),'raw_uv_error':float(np.linalg.norm(a['uv_raw'][t,v,k]-gu[k])),'corrected_uv_error':float(np.linalg.norm(a['uv'][t,v,k]-gu[k])),'conf':float(a['conf'][t,v,k]),'conf_fit':float(a['conf_fit'][t,v,k])} for k in inds]}
 # for each real clip produce per-view stability and conflict at t=.04,.08
 for n in ['xiyi_temporal','xiyi2_temporal_web','xiyi3_temporal','xiyi3_clean_temporal']:
  f=p/'out'/n/'reading.npz'
  if not f.exists():continue
  a=np.load(f);dep=a['depth'];pairs=[(i,k['mirror']) for i,k in enumerate(rig['keypoints']) if k['mirror']>i];L=[i for i,j in pairs];R=[j for i,j in pairs];ai=L.index(22);D=dep[:,:,L]-dep[:,:,R];camsR=np.array([c['R'] for c in studio['cams']]);camst=np.array([c['t'] for c in studio['cams']]);z=(np.einsum('vij,tkj->tvki',camsR,a['X'])+camst[None,:,None,:])[...,2]/rig['size'];td=z[:,:,L]-z[:,:,R];d[n]={}
  for vi,v in enumerate(views):
   d[n][v]={}
   for label,j in [('all',slice(None)),('ankle',ai)]:
    ds=D[:,vi,j];ts=td[:,vi,j];rows=[]
    for th in [0,.01,.02,.04,.08]:
     keep=abs(ds)>th;both=keep[1:]&keep[:-1];flip=(ds[1:]*ds[:-1]<0)&both
     strong_tri=abs(ts)>.02
     rows.append({'th':th,'coverage':float(keep.mean()),'tri_conflict':float((ds[keep]*ts[keep]<0).mean()) if keep.any() else None,'tri_strong_n':int((keep&strong_tri).sum()),'tri_strong_conflict':float((ds[keep&strong_tri]*ts[keep&strong_tri]<0).mean()) if (keep&strong_tri).any() else None,'adjacent_n':int(both.sum()),'flips':int(flip.sum()),'flip_rate':float(flip.sum()/both.sum()) if both.any() else None})
    d[n][v][label]={'rows':rows,'step_mean':float(abs(np.diff(ds,axis=0)).mean()),'step_p90':float(np.percentile(abs(np.diff(ds,axis=0)),90))}
 out[name]=d
Path('/tmp/v2r_depth_supplement.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
