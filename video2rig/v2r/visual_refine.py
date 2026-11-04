"""Opt-in source-image stationary-segment refinement; CPU, no contact or bone-name rules.

Uses an existing temporal solution as initialisation. Does not change observations,
checks or detector weights, nor convert a rejected clip into a passed clip.
"""
import os
import re
import time
import json
import shutil
import numpy as np
import torch
from v2r.geom import Cams, Skeleton
from v2r.read import read_video_frames,split_views,find_split
from v2r.image_motion import stationary_evidence,stationary_segments
from v2r.solve_temporal import fit_temporal
from v2r.stabilize import continuous_quats,temporal_metrics


def visual_refine(project,source,name,video=None,strength=1000.,render=True):
    project=os.path.abspath(project)
    for n in (source,name):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',n): raise ValueError('Unsafe output name')
    src,dst=[os.path.join(project,'out',n) for n in (source,name)]
    if os.path.exists(dst): raise ValueError('Output exists; originals are never overwritten')
    with open(os.path.join(project,'rig.json')) as f:rig=json.load(f)
    with open(os.path.join(project,'studio.json')) as f:studio=json.load(f)
    with open(os.path.join(src,'summary.json')) as f:summary=json.load(f)
    with np.load(os.path.join(src,'motion.npz')) as z:motion={k:z[k].copy() for k in z.files}
    with np.load(os.path.join(src,'reading.npz')) as z:reading={k:z[k].copy() for k in z.files}
    video=video or summary.get('source_video')
    if not video or not os.path.isfile(video):raise ValueError('Provide the original source video')
    frames,vfps=read_video_frames(video)
    fps=float(motion['fps'])
    if len(frames)!=len(motion['G']) or abs(vfps-fps)>.01:
        raise ValueError('Source video must have the same frames and fps as the solved result')
    split=summary.get('split_x') or find_split(frames)
    panels=[split_views(f,split,studio,summary.get('swap',False))[0] for f in frames]
    cams=Cams(studio)
    proj=np.moveaxis(cams.project_np(motion['kp']),0,1)[...,:2]
    t0=time.time()
    _,evidence=stationary_evidence(panels,proj,fps)
    starts,segment_weights,intervals=stationary_segments(evidence,fps)
    torch.set_num_threads(min(torch.get_num_threads(),2))
    if intervals:
        sol=fit_temporal(rig,studio,reading['uv'],reading['conf_fit'],reading['X_fit'],fps,
                         depth=reading.get('depth'),device='cpu',initial=motion,iters=500,verbose=False,
                         stationary_starts=starts,stationary_segment_weights=segment_weights,
                         stationary_anchor_strength=strength)
        sk=sol['skeleton']
        refined=dict(G=sol['G'],eul=sol['eul'],kp=sol['kp'],quat=continuous_quats(sk.local_quats(sol['eul'])),fps=fps)
    else:
        # Exact no-op when evidence is absent (not even an extra optimizer pass).
        refined={k:motion[k].copy() for k in ('G','eul','kp','quat','fps')}
    before=temporal_metrics(rig,motion,studio,reading)
    after=temporal_metrics(rig,refined,studio,reading)
    per_keypoint=[]
    for k,kp in enumerate(rig['keypoints']):
        relevant=[i for i in intervals if i['keypoint']==k]
        for interval in relevant:
            a,b=interval['start_frame'],interval['end_frame']
            v=interval['view']
            # These are screen-space constraints, report screen drift and world steps
            # on exactly the same automatically detected intervals, not cherry-picked.
            pp=np.moveaxis(cams.project_np(refined['kp']),0,1)[...,:2]
            old=proj[a:b+1,v,k];new=pp[a:b+1,v,k]
            old_steps=np.linalg.norm(np.diff(motion['kp'][a:b+1,k],axis=0),axis=-1)
            new_steps=np.linalg.norm(np.diff(refined['kp'][a:b+1,k],axis=0),axis=-1)
            per_keypoint.append({**interval,'name':kp['name'],
                'before_screen_span_px':float(np.linalg.norm(np.ptp(old,axis=0))),
                'after_screen_span_px':float(np.linalg.norm(np.ptp(new,axis=0))),
                'before_world_step_p90_pct_size':float(np.percentile(old_steps,90)/rig['size']*100),
                'after_world_step_p90_pct_size':float(np.percentile(new_steps,90)/rig['size']*100)})
    report={'source':source,'method':'source-image stationary projection segments, experimental',
            'strength':strength,'source_video':os.path.abspath(video),'frames':len(frames),'fps':fps,
            'intervals':per_keypoint,'before':before,'after':after,
            'root_height_amplitude_ratio':after['root_height_range']/max(before['root_height_range'],1e-12),
            'no_contact_constraints':True,'manual_bone_names':False,'manual_frame_intervals':False,
            'note':'Local image texture stationarity is not proof of joint visibility or true 3D position. Occlusion/background features can mislead it. No automatic foot-contact claim.'}
    os.makedirs(dst)
    np.savez(os.path.join(dst,'motion.npz'),**refined)
    np.savez(os.path.join(dst,'visual_evidence.npz'),segment_starts=starts,segment_weights=segment_weights,**evidence)
    for fn in ('reading.npz','check.json','diagnostics.json','diagnostic.mp4','observation_weights.npz'):
        f=os.path.join(src,fn)
        if os.path.isfile(f):shutil.copy2(f,os.path.join(dst,fn))
    with open(os.path.join(dst,'visual_refinement.json'),'w') as f:json.dump(report,f,indent=1)
    from v2r.cli import run_blender
    run_blender('export_anim.py',project,os.path.join(dst,'motion.npz'),dst,
                json.dumps({'name':name,'fps':fps,'render':render}),log=os.path.join(dst,'export.log'))
    if render:
        from v2r.pipeline import make_lab_video
        pp=np.moveaxis(cams.project_np(refined['kp']),0,1)[...,:2]
        make_lab_video(project,dst,panels,reading['uv'],reading['conf'],pp,fps,rig,studio)
    summary.update({'status':'forced_preview' if summary.get('forced') else 'visual_refined',
                    'visual_refined_from':source,'experimental':True,'seconds':time.time()-t0,
                    'reproj_px_median':after['reprojection_median_px'],'reproj_px_p90':after['reprojection_p90_px']})
    with open(os.path.join(dst,'summary.json'),'w') as f:json.dump(summary,f,indent=1)
    print('[v2r] visual refine',source,'->',name,'segments',len(intervals))
    print('[v2r] root height ratio',report['root_height_amplitude_ratio'],'image error median',before['reprojection_median_px'],'->',after['reprojection_median_px'])
    return report
