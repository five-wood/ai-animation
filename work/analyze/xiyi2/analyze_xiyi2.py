#!/usr/bin/env python3
"""Read-only xiyi2 motion/reading/export audit. Writes only --output (default /tmp).
Core code is never imported or modified; independent NumPy FK and SO(3) checks.
"""
import argparse, json, hashlib, os
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from scipy.signal import periodogram, butter, sosfiltfilt, detrend

FPS=24.0

def summary(x):
    x=np.asarray(x,float)
    return dict(rms=float(np.sqrt(np.mean(x*x))),median=float(np.median(x)),p95=float(np.percentile(x,95)),max=float(np.max(x)))

def spectrum(x):
    """Linear detrend, Hann periodogram, all vector components summed; no DC."""
    x=np.asarray(x,float)
    f,p=periodogram(x,fs=FPS,window='hann',detrend='linear',axis=0,scaling='density')
    p=p.reshape(len(f),-1).sum(1); p[0]=0
    df=f[1]-f[0]; total=float(p.sum()*df)
    out={'total_variance':total,'df_Hz':float(df)}
    for c in (3,4,6):
        en=float(p[f>=c].sum()*df)
        out[f'ge{c}_energy']=en;out[f'ge{c}_pct']=100*en/max(total,1e-30)
        out[f'ge{c}_rms']=float(np.sqrt(en))
    out['3to6_pct']=100*float(p[(f>=3)&(f<6)].sum()*df)/max(total,1e-30)
    out['peak_Hz']=float(f[np.argmax(p)])
    return out

def nearest_rotation(M):
    u,s,v=np.linalg.svd(M); d=np.linalg.det(u@v)
    u=u.copy(); u[...,:,-1]*=d[...,None]
    return u@v

def rv(M):
    return Rotation.from_matrix(nearest_rotation(M).reshape(-1,3,3)).as_rotvec().reshape(M.shape[:-2]+(3,))

def rotmetrics(R):
    R=nearest_rotation(R)
    # Spatial angular velocity vectors in the coordinate system where R is expressed.
    vel=rv(R[1:]@R[:-1].swapaxes(-1,-2))*FPS*180/np.pi
    acc=np.diff(vel,axis=0)*FPS
    traj=rv(R@R[0].T)*180/np.pi
    return {'speed_deg_s':summary(np.linalg.norm(vel,axis=-1)),
            'acc_deg_s2':summary(np.linalg.norm(acc,axis=-1)),
            'trajectory_deg':spectrum(traj),'velocity_deg_s':spectrum(vel),
            'max_ref_angle_deg':float(np.max(np.linalg.norm(traj,axis=-1))),
            'peak_acc_frame0':int(np.argmax(np.linalg.norm(acc,axis=-1))+1)}

def posmetrics(x):
    vel=np.diff(x,axis=0)*FPS; acc=np.diff(vel,axis=0)*FPS
    return {'position':spectrum(x),'speed':summary(np.linalg.norm(vel,axis=-1)),
            'acc':summary(np.linalg.norm(acc,axis=-1)),
            'peak_acc_frame0':int(np.argmax(np.linalg.norm(acc,axis=-1))+1)}

def lowpass(x,cut=3):
    return sosfiltfilt(butter(4,cut,fs=FPS,output='sos'),x,axis=0)

def smooth_rotation(R):
    r=rv(R@R[0].T)
    return Rotation.from_rotvec(lowpass(r)).as_matrix()@R[0]

def fk(rig,G,R):
    B=len(rig['bones']);T=len(G); W=np.array(rig['world_matrix']); M=np.array([b['rest'] for b in rig['bones']])
    A=np.zeros((T,B,4,4));R4=np.zeros((T,B,4,4));R4[...,:3,:3]=R;R4[...,3,3]=1
    for i,b in enumerate(rig['bones']):
        p=b['parent']
        A[:,i]=(np.linalg.inv(W)@G@W@M[i]@R4[:,i] if p<0 else A[:,p]@np.linalg.inv(M[p])@M[i]@R4[:,i])
    world=W@A
    pts=[]
    for k in rig['keypoints']:
        loc=np.array([0,k.get('tail_len',rig['bones'][k['bone']]['length']) if k['type']=='tail' else 0,0,1])
        pts.append((world[:,k['bone']]@loc)[:,:3])
    return world,np.stack(pts,1)

def project(studio,x):
    out=[]
    for c in studio['cams']:
        xc=x@np.array(c['R']).T+np.array(c['t']); h=xc@np.array(c['K']).T
        out.append(h[...,:2]/h[...,2:])
    return np.stack(out,1)

def main():
    p=argparse.ArgumentParser();p.add_argument('--project',default='/data/video2rig/projects/lizard');p.add_argument('--name',default='xiyi2');p.add_argument('--video',default='/data/video2rig/videos/xiyi2.mp4');p.add_argument('--output',default='/tmp/xiyi2_jitter_audit');a=p.parse_args()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True);base=Path(a.project)/'out'/a.name
    rig=json.loads((Path(a.project)/'rig.json').read_text());studio=json.loads((Path(a.project)/'studio.json').read_text())
    mot=np.load(base/'motion.npz');rd=np.load(base/'reading.npz');bl=np.array(json.loads((base/'blender_kp3d.json').read_text()))
    G=np.asarray(mot['G'],float);q=mot['quat'];e=mot['eul'];kp=mot['kp'];T,B=q.shape[:2]; K=len(rig['keypoints'])
    assert T==121 and float(mot['fps'])==FPS
    R=Rotation.from_quat(q[...,[1,2,3,0]].reshape(-1,4)).as_matrix().reshape(T,B,3,3)
    world,kfk=fk(rig,G,R);Rg=nearest_rotation(G[:,:3,:3]);center=np.array(rig['center']); ct=np.einsum('tij,j->ti',Rg,center)+G[:,:3,3]
    body=np.einsum('tji,tkj->tki',Rg,kp-G[:,None,:3,3])
    names=[b['name'] for b in rig['bones']];kn=[k['name'] for k in rig['keypoints']];active=[i for i,b in enumerate(rig['bones']) if b['active']]
    report={'metadata':{'frames':T,'fps':FPS,'sample_span_s':(T-1)/FPS,'duration_s':T/FPS,'rig_size':rig['size'],'bone_count':B,'active_bones':len(active),'kp_count':K,'array_shapes':{'motion':{k:list(mot[k].shape) for k in mot.files},'reading':{k:list(rd[k].shape) for k in rd.files}},'source_sha256':{fn:hashlib.sha256((base/fn).read_bytes()).hexdigest() for fn in ['motion.npz','reading.npz','blender_kp3d.json']}},
    'method':{'rotations':'SO(3) shortest-log spatial angular increments (no Euler subtraction); acceleration is vector velocity first difference. Local expressed in pre-local bone axes, world in fixed world axes. All frames included, maxima zero-based.',
    'spectra':'Hann one-sided periodogram after linear detrend, components summed. 3/4/6 Hz inclusive through Nyquist 12Hz; % denominators exclude DC. Position spectra and angular-velocity spectra are different, not interchangeable.',
    'attribution':'Diagnostic-only counterfactual replaces one component by 4th-order zero-phase 3Hz Butterworth smoothing in SO(3) log coordinates, or removes all local/global high frequency jointly. Never writes motion. Magnitudes are not additive causal shares; high frequency is not proof of error; no GT amplitude fidelity claimed.',
    'units':'Angles deg; speed deg/s; acceleration deg/s^2. Keypoint and translation coordinates rig world units, normalized values explicitly use rig.size. UV studio-panel px.'}}
    d=np.linalg.norm(kp-bl,axis=-1); d2=np.linalg.norm(kfk-kp,axis=-1)
    er=Rotation.from_euler('xyz',e.reshape(-1,3)).as_matrix().reshape(T,len(active),3,3)
    report['consistency']={'motion_vs_blender_distance':summary(d),'motion_vs_blender_max_pct_size':float(d.max()/rig['size']*100),'independent_numpy_fk_vs_motion':summary(d2),'local_euler_quat_geodesic_max_deg':float(np.max(np.linalg.norm(rv(er@R[:,active].swapaxes(-1,-2)),axis=-1))*180/np.pi),'quat_norm_max_abs_error':float(np.max(abs(np.linalg.norm(q,axis=-1)-1))),'quat_consecutive_dot_min':float(np.min(np.sum(q[1:]*q[:-1],axis=-1))),'G_det_range':[float(np.min(np.linalg.det(G[:,:3,:3]))),float(np.max(np.linalg.det(G[:,:3,:3])))],'best_frame_shift_rmse':{str(s):float(np.sqrt(np.mean((kp[max(0,s):min(T,T+s)]-bl[max(0,-s):min(T,T-s)])**2))) for s in range(-2,3)}}
    report['global']={'rotation':rotmetrics(Rg),'center_translation':posmetrics(ct),'G_matrix_translation':posmetrics(G[:,:3,3])}
    report['bones']=[]
    for i,b in enumerate(rig['bones']):
        report['bones'].append({'index':i,'name':b['name'],'active':b['active'],'parent':b['parent'],'local':rotmetrics(R[:,i]),'world':rotmetrics(world[:,i,:3,:3]),'world_without_G':rotmetrics(Rg.swapaxes(-1,-2)@world[:,i,:3,:3])})
    report['keypoints']=[]
    uv=rd['uv'];cf=rd['conf'];fit=rd['conf_fit'] if 'conf_fit' in rd.files else cf; proj=project(studio,kp)
    for i,k in enumerate(rig['keypoints']):
        rec={'index':i,'name':k['name'],'bone':names[k['bone']],'world':posmetrics(kp[:,i]),'body':posmetrics(body[:,i]),'blender':posmetrics(bl[:,i]),'views':[]}
        for v,c in enumerate(studio['cams']):
            d2=np.linalg.norm(proj[:,v,i]-uv[:,v,i],axis=-1)
            rec['views'].append({'view':c['name'],'uv':posmetrics(uv[:,v,i]),'conf':summary(cf[:,v,i]),'conf_below_03_pct':float(np.mean(cf[:,v,i]<.3)*100),'fit_conf_mean':float(fit[:,v,i].mean()),'fit_downweighted_pct':float(np.mean(fit[:,v,i]<cf[:,v,i]*.99)*100),'reprojection_px':summary(d2),'ray_miss_px':summary(rd['res'][:,i,v]),'reader_uv':posmetrics(rd['uv_reader'][:,v,i]) if 'uv_reader' in rd.files else None})
        report['keypoints'].append(rec)
    report['reading_aggregate']={}
    for key in ['uv_reader','uv_raw','uv']:
        if key in rd.files: report['reading_aggregate'][key]={'spectrum':spectrum(rd[key]),'second_diff_px':summary(np.linalg.norm(np.diff(rd[key],n=2,axis=0),axis=-1))}
    report['reading_aggregate']['confidence']={'mean':float(cf.mean()),'below03_pct':float(np.mean(cf<.3)*100),'downweighted_pct':float(np.mean(fit<cf*.99)*100),'fit_mean':float(fit.mean())}
    report['reading_aggregate']['kp_world']=spectrum(kp);report['reading_aggregate']['kp_body']=spectrum(body)
    for key in ['X','X_fit']:
        if key in rd.files:
            report['reading_aggregate'][key]={'spectrum':spectrum(rd[key]),'second_diff_world':summary(np.linalg.norm(np.diff(rd[key],n=2,axis=0),axis=-1))}
    report['reading_aggregate']['uv_raw_vs_uv_max_px']=float(np.max(np.abs(rd['uv_raw']-rd['uv'])))
    report['reading_aggregate']['euler_active_deg']=spectrum(e*180/np.pi)
    # Same-coordinate high-pass spatial angular velocity decomposition for G vs body.
    # Finite increments are approximately additive at these small per-frame angles.
    gvel=rv(Rg[1:]@Rg[:-1].swapaxes(-1,-2))*FPS*180/np.pi
    ghp=gvel-lowpass(gvel)
    for i,rec in enumerate(report['bones']):
        wr=nearest_rotation(world[:,i,:3,:3]);wvel=rv(wr[1:]@wr[:-1].swapaxes(-1,-2))*FPS*180/np.pi
        ahp=(wvel-gvel)-lowpass(wvel-gvel)
        rec['G_vs_articulated_velocity_HF_cosine']=float(np.sum(ghp*ahp)/max(np.linalg.norm(ghp)*np.linalg.norm(ahp),1e-20))
    report['reading_aggregate']['reprojection_px']=summary(np.linalg.norm(proj-uv,axis=-1))
    # Counterfactual ranking of local/global high-frequency propagation to position.
    Rs=np.stack([smooth_rotation(R[:,i]) for i in range(B)],1)
    Gs=G.copy();Gs[:,:3,:3]=smooth_rotation(Rg);cts=lowpass(ct);Gs[:,:3,3]=cts-np.einsum('tij,j->ti',Gs[:,:3,:3],center)
    Grot=G.copy();Grot[:,:3,:3]=Gs[:,:3,:3];Grot[:,:3,3]=ct-np.einsum('tij,j->ti',Grot[:,:3,:3],center)
    Gtrans=G.copy();Gtrans[:,:3,3]=cts-np.einsum('tij,j->ti',Rg,center)
    configs=[('global_rotation',Grot,R),('global_translation',Gtrans,R),('all_global',Gs,R),('all_local',G,Rs),('all',Gs,Rs)]
    for i in active:
        r=R.copy();r[:,i]=Rs[:,i]; configs.append((names[i],G,r))
    report['counterfactual_3Hz']=[]
    for label,g,r in configs:
        _,x=fk(rig,g,r);delta=kfk-x; sp=spectrum(delta); after=spectrum(x)
        report['counterfactual_3Hz'].append({'component':label,'delta_rms_pct_size':float(np.sqrt(np.mean(np.sum(delta**2,axis=-1)))/rig['size']*100),'delta_spectrum':sp,'after_position_spectrum':after,'delta_acc_rms_pct_size_s2':float(np.sqrt(np.mean(np.sum((np.diff(delta,n=2,axis=0)*FPS**2)**2,axis=-1)))/rig['size']*100),'delta_per_kp_rms_pct_size':{kn[k]:float(np.sqrt(np.mean(np.sum(delta[:,k]**2,axis=-1)))/rig['size']*100) for k in range(K)}})
    # Temporal windows of peak acceleration and common-mode vs articulated trajectories.
    ka=np.linalg.norm(np.diff(kp,n=2,axis=0),axis=-1)*FPS**2
    ga=np.linalg.norm(np.diff(rv(Rg[1:]@Rg[:-1].swapaxes(-1,-2)),axis=0),axis=-1)*FPS**2*180/np.pi
    report['peak_frames']={'kp_acc_top': [{'frame0':int(t+1),'time_s':float((t+1)/FPS),'mean_acc_pct_size_s2':float(ka[t].mean()/rig['size']*100),'worst_kp':kn[int(np.argmax(ka[t]))]} for t in np.argsort(ka.mean(1))[-10:][::-1]],'global_ang_acc_top':[{'frame0':int(t+1),'deg_s2':float(ga[t])} for t in np.argsort(ga)[-10:][::-1]]}
    # Original video metadata/contact sheet and sparse image-flow sanity check.
    try:
        import cv2
        cap=cv2.VideoCapture(a.video);frames=[]
        while True:
            ok,f=cap.read()
            if not ok:break
            frames.append(f)
        report['video']={'frames':len(frames),'fps':float(cap.get(cv2.CAP_PROP_FPS)),'width':int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),'height':int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))};cap.release()
        h,w=frames[0].shape[:2];acc=np.zeros(w)
        for f in frames[::max(1,len(frames)//20)]:acc+=np.r_[np.abs(np.diff(cv2.cvtColor(f,cv2.COLOR_BGR2GRAY).astype(float),axis=1)).mean(0),0]
        lo,hi=int(.4*w),int(.6*w);c=lo+int(np.argmax(acc[lo:hi]));split=c+1 if acc[c]/(np.median(acc[lo:hi])+1e-6)>4 else w//2
        report['video']['auto_split']=int(split)
        panels=[]
        for f in frames:
            row=[]
            for x0,x1 in [(0,split),(split,w)]:
                crop=f[:,x0:x1]; s=min(studio['panel_w']/crop.shape[1],studio['panel_h']/h);nw,nh=round(crop.shape[1]*s),round(h*s)
                im=cv2.resize(crop,(nw,nh),interpolation=cv2.INTER_AREA);can=np.full((studio['panel_h'],studio['panel_w'],3),int(np.median(im[:4].reshape(-1,3),0).mean()),np.uint8);ox=(can.shape[1]-nw)//2;oy=(can.shape[0]-nh)//2;can[oy:oy+nh,ox:ox+nw]=im;row.append(cv2.cvtColor(can,cv2.COLOR_BGR2GRAY))
            panels.append(row)
        uv0=rd['uv_reader'] if 'uv_reader' in rd.files else uv
        flow=[]
        for v in range(len(studio['cams'])):
            residual=[]; errs=[];valids=[]
            for t in range(T-1):
                pts=uv0[t,v].astype(np.float32).reshape(-1,1,2)
                nxt,st,err=cv2.calcOpticalFlowPyrLK(panels[t][v],panels[t+1][v],pts,None,winSize=(21,21),maxLevel=3)
                back,st2,err2=cv2.calcOpticalFlowPyrLK(panels[t+1][v],panels[t][v],nxt,None,winSize=(21,21),maxLevel=3)
                fb=np.linalg.norm(back[:,0]-pts[:,0],axis=-1);valid=(st[:,0]>0)&(st2[:,0]>0)&(fb<1.5)&(cf[t,v]>.3)&(cf[t+1,v]>.3)
                residual.append(np.linalg.norm(uv0[t+1,v]-nxt[:,0],axis=-1));valids.append(valid)
            resid=np.array(residual);valid=np.array(valids)
            flow.append({'view':studio['cams'][v]['name'],'valid_pct':float(valid.mean()*100),'reader_vs_LK_step_residual_px':summary(resid[valid]),'per_kp':[{'name':kn[k],'valid_pct':float(valid[:,k].mean()*100),'residual_px':summary(resid[:,k][valid[:,k]]) if valid[:,k].any() else None} for k in range(K)]})
        report['video']['flow_check']=flow
        # 8 evenly spaced source frames + 4 worst 3D acceleration frames. No GT labels.
        chosen=sorted(set(np.linspace(0,T-1,8,dtype=int).tolist()+[int(t+1) for t in np.argsort(ka.mean(1))[-4:]]))
        thumbs=[]
        for t in chosen:
            f=cv2.resize(frames[t],(640,360));cv2.putText(f,f'frame {t} | {t/FPS:.3f}s',(8,23),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,255,255),2);thumbs.append(f)
        while len(thumbs)%3:thumbs.append(np.zeros_like(thumbs[0]))
        sheet=np.concatenate([np.concatenate(thumbs[i:i+3],1) for i in range(0,len(thumbs),3)],0);cv2.imwrite(str(out/'source_contact_sheet.jpg'),sheet)
    except Exception as exc:report['video_error']=str(exc)
    (out/'audit.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    lines=['XIYI2 JITTER AUDIT (24 fps / 121 frames)','Consistency: '+json.dumps(report['consistency']), 'Global rotation: '+json.dumps(report['global']['rotation'])]
    for label,metric in [('LOCAL angular acc RMS',lambda b:b['local']['acc_deg_s2']['rms']),('WORLD angular acc RMS',lambda b:b['world']['acc_deg_s2']['rms']),('LOCAL >=3Hz velocity energy',lambda b:b['local']['velocity_deg_s']['ge3_energy'])]:
        lines.append('\n'+label)
        for b in sorted(report['bones'],key=metric,reverse=True)[:12]:lines.append(b['name']+' '+json.dumps({'acc':b['local']['acc_deg_s2']['rms'],'speed':b['local']['speed_deg_s']['rms'],'world_acc':b['world']['acc_deg_s2']['rms'],'ge3_vel_pct':b['local']['velocity_deg_s']['ge3_pct'],'ge6_vel_pct':b['local']['velocity_deg_s']['ge6_pct'],'pos_ge3_rms_deg':b['local']['trajectory_deg']['ge3_rms']}))
    lines.append('\nCounterfactual positional jitter contribution (non-additive)')
    for r in sorted(report['counterfactual_3Hz'],key=lambda x:x['delta_spectrum']['ge3_energy'],reverse=True):lines.append(r['component']+' '+json.dumps({k:r[k] for k in ['delta_rms_pct_size','delta_acc_rms_pct_size_s2','delta_spectrum','after_position_spectrum']}))
    lines.append('\nReading aggregates '+json.dumps(report['reading_aggregate']));lines.append('\nPeaks '+json.dumps(report['peak_frames']));lines.append('\nVideo '+json.dumps(report.get('video',report.get('video_error'))))
    (out/'audit_summary.txt').write_text('\n'.join(lines));print('\n'.join(lines[:4]));print('OUTPUT',out)

if __name__=='__main__':main()
