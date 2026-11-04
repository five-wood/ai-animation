"""Conservative source-image stationarity evidence; NOT automatic 3D contact.

Works for any keypoint name, but optical flow cannot prove a hidden joint is still.
All evidence is optional, inspectable, and zero for unsupported texture/ambiguous flow.
"""
import cv2
import numpy as np


def stationary_evidence(panels, projections, fps, radius=18., window_seconds=.25):
    p=np.asarray(projections,float)
    T,V,K,_=p.shape
    if len(panels)!=T or fps<=0: raise ValueError("Invalid frames/fps")
    speed=np.full((T-1,V,K),np.inf)
    count=np.zeros((T-1,V,K),int)
    fb_error=np.full((T-1,V,K),np.inf)
    for v in range(V):
        gray=[cv2.cvtColor(row[v],cv2.COLOR_BGR2GRAY) for row in panels]
        for t in range(T-1):
            corners=cv2.goodFeaturesToTrack(gray[t],maxCorners=1500,qualityLevel=.008,minDistance=3,blockSize=5)
            if corners is None: continue
            a=corners.reshape(-1,2)
            dist=np.linalg.norm(a[:,None,:]-p[t,v][None,:,:],axis=-1)
            near=dist<radius
            chosen=np.flatnonzero(near.any(1))
            if not len(chosen):continue
            aa=a[chosen].astype(np.float32).reshape(-1,1,2)
            kw=dict(winSize=(15,15),maxLevel=2,criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,20,.01))
            bb,s1,_=cv2.calcOpticalFlowPyrLK(gray[t],gray[t+1],aa,None,**kw)
            if bb is None:continue
            cc,s2,_=cv2.calcOpticalFlowPyrLK(gray[t+1],gray[t],bb,None,**kw)
            if cc is None:continue
            fb=np.linalg.norm(cc-aa,axis=-1).ravel()
            flow=np.linalg.norm(bb-aa,axis=-1).ravel()*fps
            valid=s1.ravel().astype(bool)&s2.ravel().astype(bool)&np.isfinite(flow)&(fb<.35)
            for k in range(K):
                use=near[chosen,k]&valid
                count[t,v,k]=int(use.sum())
                if use.sum()>=4:
                    speed[t,v,k]=np.percentile(flow[use],90)
                    fb_error[t,v,k]=np.percentile(fb[use],90)
    rad=max(1,int(round(window_seconds*fps/2)))
    factor=np.zeros_like(speed)
    window_speed=np.full_like(speed,1e9)
    for t in range(T-1):
        lo,hi=max(0,t-rad),min(T-1,t+rad+1)
        values=speed[lo:hi]
        supported=np.isfinite(values).all(0)
        sp=np.max(np.where(np.isfinite(values),values,1e9),axis=0)
        window_speed[t]=sp
        factor[t]=np.where(supported,np.clip((1.-sp)/.75,0,1),0)
    return factor,{"flow_speed_px_per_second":np.where(np.isfinite(speed),speed,1e9),
                   "flow_support_count":count,"flow_fb_error_px":np.where(np.isfinite(fb_error),fb_error,1e9),
                   "window_max_speed_px_per_second":window_speed,"stationary_factor":factor,
                   "window_seconds":np.asarray(window_seconds),"radius_px":np.asarray(radius)}


def stationary_segments(evidence, fps, threshold_px_s=1.0, minimum_seconds=.35):
    """Independent per-view/keypoint intervals; cumulative drift <=0.6px.

    No extrapolation across unsupported pairs. An interval anchor constrains projection
    drift, not the absolute correctness of its 2D position or 3D foot/ground contact.
    """
    speed=np.asarray(evidence['flow_speed_px_per_second'])
    counts=np.asarray(evidence['flow_support_count'])
    T,V,K=speed.shape
    starts=np.full((T+1,V,K),-1,int)
    weights=np.zeros((T+1,V,K))
    intervals=[]
    minimum=max(2,int(np.ceil(minimum_seconds*fps)))
    for v in range(V):
        for k in range(K):
            good=(speed[:,v,k]<threshold_px_s)&(counts[:,v,k]>=4)
            bounds=np.diff(np.r_[False,good,False].astype(int))
            for a,b in zip(np.flatnonzero(bounds==1),np.flatnonzero(bounds==-1)):
                if b-a<minimum or np.sum(speed[a:b,v,k])/fps>.6: continue
                # Ramp over 0.1s near the segment start, no artificial extension.
                ids=np.arange(a,b+1)
                ramp=np.clip((ids-a)/max(fps*.1,1),0,1)
                starts[ids,v,k]=a
                weights[ids,v,k]=ramp*ramp*(3-2*ramp)
                intervals.append({'view':v,'keypoint':k,'start_frame':int(a),'end_frame':int(b),
                                  'duration_seconds':float((b-a)/fps)})
    return starts,weights,intervals
