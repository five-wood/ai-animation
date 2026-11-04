"""Experimental residual-aware recovery of a temporal solve, without contact rules.

Uses model reprojection only as *additional* weak evidence, never ground truth.
Persistent multi-view disagreement reduces recycled triangulation/depth guidance;
weakly supported local rotations receive a small rotational-speed penalty to reduce
null-space drift (acceleration alone permits smooth wrong motion). This trades motion
amplitude for stability on ambiguous joints; must be audited on known-motion data.
No fixed bone names, frame ranges, ground plane or planted-foot constraint.
"""
import numpy as np


def recovery_evidence(uv, weights, proj, size_px, heat_quality=None):
    uv, weights, proj = [np.asarray(x, dtype=float) for x in (uv,weights,proj)]
    if uv.shape != proj.shape or weights.shape != uv.shape[:-1]:
        raise ValueError("Incompatible observation/projection shapes")
    scale = max(float(size_px) * .025, 3.)
    residual = np.linalg.norm(uv-proj,axis=-1)
    # Never discard an observation solely because the warm solve does not explain it.
    # Range [0.2,1], retain all raw arrays and original weights for audit.
    residual_factor = .2 + .8 / (1 + (residual/scale)**2)
    w = weights * residual_factor
    # High residual in either view means triangulation is not independent evidence:
    # that 3D point was made from those same disagreeing 2D detections.
    pair = np.max(residual,axis=1)
    tri_factor = 1/(1+(pair/scale)**2)
    # Weak evidence for local orientation: propagated via all moved keypoints later.
    normalized = np.clip(weights,0,1)
    support = np.mean(normalized * residual_factor,axis=1)
    return w, {"residual_px":residual,"residual_factor":residual_factor,
               "triangulation_factor":tri_factor,"point_support":support,
               "weights_original":weights,"weights_recovered":w,
               "residual_scale_px":np.asarray(scale)}


def bone_support(sk, point_support):
    """Estimate evidence for a rotation from the descendants it actually moves.
    Side-neutral and name-neutral; not a visibility probability.
    """
    p=np.asarray(point_support)
    out=np.ones((len(p),sk.A),dtype=float)
    for ai,bi in enumerate(sk.active):
        moved=[]
        for ki,k in enumerate(sk.rig['keypoints']):
            b=k['bone']
            if b==bi and k['type']=='tail':moved.append(ki);continue
            parent=sk.parent[b]
            while parent>=0:
                if parent==bi:
                    moved.append(ki);break
                parent=sk.parent[parent]
        if moved: out[:,ai]=np.median(p[:,moved],axis=1)
    return np.clip(out,.01,1)
