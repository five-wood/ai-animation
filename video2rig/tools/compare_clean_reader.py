"""Compare legacy/clean readers on old clips without claiming a video-generation benefit.
Also evaluate both on clean known-motion exam via a matching, synthetic-held-out target.
No retraining or overwriting of old project outputs. GPU inference must be run after train.
"""
import os,sys,json,time
import numpy as np
import torch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.read import load_reader,run_reader,read_video_frames,split_views,find_split
from v2r.geom import Cams
from v2r.solve import triangulate_all,consistency_report

root='/data/video2rig';projects=[root+'/projects/lizard',root+'/projects/lizard_clean']
with open(projects[0]+'/rig.json') as f:rig=json.load(f)
with open(projects[0]+'/studio.json') as f:studio=json.load(f)
cams=Cams(studio)
report={'note':'Old videos retain shadows. These tests are compatibility only, not isolated causality or real-video GT accuracy. Synthetic exam is clean-domain; no same-pose legacy-light exam is used here.', 'runs':[]}
clips=[('xiyi',root+'/videos/xiyi.mp4'),('xiyi2',root+'/videos/xiyi2.mp4'),('clean_exam',projects[1]+'/exam/exam_video.mp4')]
for project in projects:
    model,state=load_reader(project)
    for label,path in clips:
        if not os.path.isfile(path):continue
        frames,fps=read_video_frames(path)
        split=640 if label=='clean_exam' else find_split(frames)
        panels=[split_views(f,split,studio)[0] for f in frames]
        t=time.time();uv,conf,depth=run_reader(model,state,panels)
        X,res=triangulate_all(cams,uv,conf)
        c=consistency_report(cams,rig,X,res,conf,uv)
        row={'reader':os.path.basename(project),'clip':label,'frames':len(frames),'fps':fps,
             'seconds':time.time()-t,'ray_median_pct':100*c['median_residual_rel'],
             'bad_frames':c['bad_frames'],'same_move':c['same_move'],
             'uv_accel_median_px':float(np.median(np.linalg.norm(np.diff(uv,n=2,axis=0),axis=-1))),
             'uv_accel_p90_px':float(np.percentile(np.linalg.norm(np.diff(uv,n=2,axis=0),axis=-1),90))}
        if label=='clean_exam':
            with open(projects[1]+'/exam/frames/motion_gt.json') as f:gt=json.load(f)
            true=np.array([[r[c['name']]['uvz'] for c in studio['cams']] for r in gt['frames']])[...,:2]
            err=np.linalg.norm(uv-true,axis=-1)
            row.update(gt_2d_median_px=float(np.median(err)),gt_2d_mean_px=float(err.mean()),gt_2d_p90_px=float(np.percentile(err,90)))
        report['runs'].append(row)
        print(json.dumps(row),flush=True)
    del model
    torch.cuda.empty_cache()
out=projects[1]+'/reader_comparison.json'
with open(out,'w') as f:json.dump(report,f,indent=1)
print('wrote',out)
