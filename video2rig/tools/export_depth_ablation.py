"""Export saved depth-only ablations as separate, correctly labelled Web results.
Original source files, checks and animations remain untouched. No new inference/training.
"""
import sys,os,json,shutil,numpy as np
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.cli import run_blender
from v2r.geom import Cams
from v2r.pipeline import make_lab_video
from v2r.read import read_video_frames,split_views,find_split

root='/data/video2rig';project=root+'/projects/lizard';base=root+'/experiments/depth_order_20261103'
source='xiyi2_temporal_web';src=project+'/out/'+source
with open(project+'/rig.json') as f:rig=json.load(f)
with open(project+'/studio.json') as f:studio=json.load(f)
with open(src+'/summary.json') as f:oldsummary=json.load(f)
with open(base+'/comparison.json') as f:report=json.load(f)
case=next(c for c in report['cases'] if c['project']=='lizard' and c['clip']==source)
frames,fps=read_video_frames(oldsummary['source_video']);split=oldsummary.get('split_x') or find_split(frames)
panels=[split_views(f,split,studio,oldsummary.get('swap',False))[0] for f in frames]
reading=dict(np.load(src+'/reading.npz'));cams=Cams(studio)
for mode in ('off','numeric','order'):
 name='xiyi2_depth_'+mode;dst=project+'/out/'+name
 if os.path.exists(dst):raise RuntimeError('Refusing overwrite '+dst)
 os.makedirs(dst)
 shutil.copy2(base+'/lizard_'+source+'_'+mode+'.npz',dst+'/motion.npz')
 for fn in ('reading.npz','check.json','diagnostics.json','diagnostic.mp4','observation_weights.npz'):
  if os.path.isfile(src+'/'+fn):shutil.copy2(src+'/'+fn,dst+'/'+fn)
 motion=dict(np.load(dst+'/motion.npz'));metrics=case['results'][mode]
 summary=dict(oldsummary);summary.update({'solver':'temporal','experimental':True,'depth_mode':mode,
  'ablation_source':source,'status':'complete','reproj_px_median':metrics['reprojection_median_px'],
  'reproj_px_p90':metrics['reprojection_p90_px'],'seconds':metrics['seconds']})
 with open(dst+'/summary.json','w') as f:json.dump(summary,f,indent=1)
 with open(dst+'/depth_ablation.json','w') as f:json.dump({'config':report['config'],'source':source,'mode':mode,'metrics':metrics},f,indent=1)
 run_blender('export_anim.py',project,dst+'/motion.npz',dst,json.dumps({'name':name,'fps':fps,'render':True}),log=dst+'/export.log')
 proj=np.moveaxis(cams.project_np(motion['kp']),0,1)[...,:2]
 make_lab_video(project,dst,panels,reading['uv'],reading['conf'],proj,fps,rig,studio)
 print('exported',name,flush=True)
