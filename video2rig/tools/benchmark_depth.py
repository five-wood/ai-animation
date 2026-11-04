"""Controlled existing-reader depth ablation. No GT in targets or initialisation.

Each case shares exact UV, reliability, triangulated points, iteration budget and a
single freshly generated depth-OFF initial pose. Only the final depth term changes.
The same raw input is used across off/numeric/order; source projects are read-only.
"""
import os,sys,json,time,hashlib,argparse
import numpy as np
import torch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.geom import Skeleton,Cams,quat_to_mat_np
from v2r.solve import fit,triangulate_all
from v2r.solve_temporal import fit_temporal
from v2r.depth_order import order_targets
from v2r.stabilize import temporal_metrics,continuous_quats


def evaluate(rig,studio,motion,reading,gt=None):
    sk=Skeleton(rig);cams=Cams(studio)
    report=temporal_metrics(rig,motion,studio,reading);report.pop('per_bone',None)
    if gt:
        G=np.array([f['G'] for f in gt['frames']]);R=quat_to_mat_np(np.array([f['quat'] for f in gt['frames']]))
        with torch.no_grad():wg,kg=sk.fk_rot(torch.tensor(G,dtype=torch.float32),torch.tensor(R,dtype=torch.float32))
        world,kp=sk.fk_np(motion['G'],motion['eul'])
        a,b=sk.bone_dirs(wg.numpy())[:,sk.active],sk.bone_dirs(world)[:,sk.active]
        ang=np.degrees(np.arccos(np.clip((a*b).sum(-1),-1,1)))
        error=np.linalg.norm(kp-kg.numpy(),axis=-1)/sk.size*100
        report.update(gt_joint_mean_pct_size=float(error.mean()),gt_joint_p90_pct_size=float(np.percentile(error,90)),
                      gt_bone_mean_deg=float(ang.mean()),gt_bone_p90_deg=float(np.percentile(ang,90)))
        vis=np.array([[f[c['name']]['vis'] for c in studio['cams']] for f in gt['frames']])
        both_hidden=(vis.max(1)==0)
        if both_hidden.any():report['gt_both_views_hidden_joint_mean_pct_size']=float(error[both_hidden].mean())
        # Near/far identity of matching mirror pairs, after fixed nonzero true gap mask.
        pred_z=np.moveaxis(cams.project_np(kp),0,1)[...,2]
        true_z=np.array([[f[c['name']]['uvz'] for c in studio['cams']] for f in gt['frames']])[...,2]
        pairs=order_targets(rig,reading['depth'],reading['conf_fit'],float(motion['fps']))['pairs']
        pg=pred_z[:,:,pairs[:,0]]-pred_z[:,:,pairs[:,1]]
        tg=true_z[:,:,pairs[:,0]]-true_z[:,:,pairs[:,1]]
        valid=np.abs(tg)/sk.size>.02
        report['gt_order_accuracy_nontrivial']=float((np.sign(pg[valid])==np.sign(tg[valid])).mean())
        report['gt_order_samples_nontrivial']=int(valid.sum())
    if 'ankle.L' in [k['name'] for k in rig['keypoints']]:
        # Evaluation only, no named joint or interval enters the solver.
        i=next(i for i,k in enumerate(rig['keypoints']) if k['name']=='ankle.L')
        start=int(round(4*float(motion['fps'])))
        if len(motion['kp'])>start+2:
            foot=motion['kp'][start:,i]
            report['left_hind_late_step_p90_pct_size']=float(np.percentile(np.linalg.norm(np.diff(foot,axis=0),axis=-1),90)/sk.size*100)
    return report


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);ap.add_argument('--device',default='cuda');a=ap.parse_args()
    os.makedirs(a.out,exist_ok=True)
    torch.set_num_threads(2)
    cases=[('lizard','exam_temporal'),('lizard','xiyi2_temporal_web'),('lizard','xiyi3_temporal'),('lizard_clean','xiyi3_clean_temporal')]
    report={'config':{'modes':['off','numeric','order'],'numeric_strength':.025,'order_strength':.025,
                      'order_gate':{'min_gap':.02,'min_weight':.2,'window_seconds':.2,'agreement':.8,'max_margin':.03},
                      'same_depth_off_initialisation':True,'warm_iters':900,'refine_iters':850,
                      'note':'Existing joint-depth head only. No Depth Anything, GT initialisation, contact or post-filter. Real clips have no GT.'},'cases':[]}
    for project,clip in cases:
        p='/data/video2rig/projects/'+project
        with open(p+'/rig.json') as f:rig=json.load(f)
        with open(p+'/studio.json') as f:studio=json.load(f)
        with np.load(p+'/out/'+clip+'/reading.npz') as z:rd={k:z[k].copy() for k in z.files}
        with np.load(p+'/out/'+clip+'/motion.npz') as z:fps=float(z['fps'])
        w=rd['conf_fit'];X=rd['X_fit'];uv=rd['uv'];depth=rd['depth']
        gt=None
        if clip.startswith('exam'):
            with open(p+'/exam/frames/motion_gt.json') as f:gt=json.load(f)
        case={'project':project,'clip':clip,'fps':fps,'hashes':{},'results':{}}
        for fn in ('reading.npz','motion.npz'):
            case['hashes'][fn]=hashlib.sha256(open(p+'/out/'+clip+'/'+fn,'rb').read()).hexdigest()
        print('CASE',project,clip,flush=True)
        initial=fit(rig,studio,uv,w,X,depth=None,iters=900,device=a.device,verbose=False,fps=fps)
        for mode in ('off','numeric','order'):
            t=time.time()
            sol=fit_temporal(rig,studio,uv,w,X,fps,depth=depth,iters=850,device=a.device,initial=initial,
                             depth_mode=mode,verbose=False)
            sk=sol['skeleton'];motion=dict(G=sol['G'],eul=sol['eul'],kp=sol['kp'],quat=continuous_quats(sk.local_quats(sol['eul'])),fps=fps)
            metrics=evaluate(rig,studio,motion,rd,gt)
            metrics['seconds']=time.time()-t;metrics['solver']=sol['solver_info']
            case['results'][mode]=metrics
            np.savez(os.path.join(a.out,project+'_'+clip+'_'+mode+'.npz'),**motion)
            print(mode,json.dumps({k:v for k,v in metrics.items() if k!='solver'}),flush=True)
        report['cases'].append(case)
        with open(os.path.join(a.out,'comparison.json'),'w') as f:json.dump(report,f,indent=1)
    print('DONE',a.out,flush=True)
if __name__=='__main__':main()
