"""Optional relative joint-depth ordering from the existing per-creature reader.

Not Depth Anything, not visible-surface depth, not calibrated confidence/visibility.
Pairs come from the rig's mirror mapping, not fixed names. A sign-stability gate can
remove ambiguous near/far crossings, but a consistently wrong sign can still pass it.
No GT is used to construct targets. Predicted depth unit is body-size-normalized.
"""
import numpy as np
import torch


def order_targets(rig, depth, weights, fps, min_gap=.02, min_weight=.2,
                  window_seconds=.2, agreement=.8, max_margin=.03):
    d=np.asarray(depth,float);w=np.asarray(weights,float)
    if d.ndim!=3 or d.shape!=w.shape or d.shape[2]!=len(rig['keypoints']):
        raise ValueError('depth/weights must be T,V,K matching rig')
    if not np.isfinite(fps) or fps<=0:raise ValueError('fps must be positive finite')
    if not 0<min_gap or not 0<max_margin or not .5<agreement<=1:
        raise ValueError('Invalid order gate configuration')
    pairs=[]
    for i,k in enumerate(rig['keypoints']):
        j=k.get('mirror',-1)
        if j>i and j<len(rig['keypoints']) and rig['keypoints'][j].get('mirror')==i:
            pairs.append((i,j))
    pairs=np.array(pairs,dtype=int).reshape(-1,2)
    T,V,K=d.shape;P=len(pairs)
    gap=d[:,:,pairs[:,0]]-d[:,:,pairs[:,1]]
    finite=np.isfinite(gap)&np.isfinite(w[:,:,pairs[:,0]])&np.isfinite(w[:,:,pairs[:,1]])
    pair_weight=np.minimum(w[:,:,pairs[:,0]],w[:,:,pairs[:,1]])
    eligible=finite&(np.abs(gap)>=min_gap)&(pair_weight>=min_weight)
    sign=np.sign(np.nan_to_num(gap))
    rad=max(1,int(round(window_seconds*fps/2)))
    stable=np.zeros((T,V,P),dtype=bool)
    temporal_agreement=np.zeros((T,V,P))
    for t in range(T):
        lo,hi=max(0,t-rad),min(T,t+rad+1)
        # Count only finite, sufficiently strong pairs, and require a majority of
        # the whole window to be supported (not one surviving confident frame).
        supported=eligible[lo:hi].sum(0)
        same=(eligible[lo:hi]&(sign[lo:hi]==sign[t])).sum(0)
        frac=same/np.maximum(supported,1)
        temporal_agreement[t]=frac
        stable[t]=eligible[t]&(supported>=max(2,int(np.ceil((hi-lo)*.6))))&(frac>=agreement)
    gate=np.where(stable,np.clip(pair_weight,0,1),0.)
    # A modest margin, not full numerical depth regression. Small predicted gaps
    # have no constraint; large gaps don't force absolute depth/scale accuracy.
    margin=np.minimum(np.abs(np.nan_to_num(gap))*.5,max_margin)
    return {'pairs':pairs,'sign':sign,'margin':margin,'gate':gate,
            'predicted_gap':np.nan_to_num(gap),'eligible':eligible,
            'temporal_agreement':temporal_agreement,
            'min_gap':np.asarray(min_gap),'min_weight':np.asarray(min_weight),
            'window_seconds':np.asarray(window_seconds),'agreement':np.asarray(agreement)}


def ordering_loss(camera_depth, targets, body_size):
    """Hinge-square order loss; z=T,V,K tensor, actual z increases away from camera.
    Targets may contain tensors on matching device (constructed once by solver).
    Inactive terms contribute zero, no division by active count that amplifies rare gates.
    """
    pairs=targets['pairs']
    if len(pairs)==0:return camera_depth.sum()*0
    delta=(camera_depth[:,:,pairs[:,0]]-camera_depth[:,:,pairs[:,1]])/body_size
    violation=torch.relu(targets['margin']-targets['sign']*delta)
    return (targets['gate']*violation.square()).mean()
