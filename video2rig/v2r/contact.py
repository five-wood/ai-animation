"""Explicit, interval-limited foot contact refinement of an existing solved motion.

Not automatic contact detection. User/operator must confirm the selected foot is planted.
Only the two nearest active ancestors of the selected foot are optimised; root and all
other local channels are unchanged. A smoothstep fade introduces the late-hold anchor,
chosen from a quiet reference interval. Two points (ankle head and clipped toe marker)
constrain position and one foot direction. This is not a physics simulation, full sole
orientation constraint or a guarantee that an unreachable anchor can be reached.
"""
import json
import os
import re
import shutil
import time
import numpy as np
import torch

from v2r.geom import Skeleton, Cams
from v2r.stabilize import continuous_quats, temporal_metrics


def contact_refine(rig, motion, foot="ankle.L", start_seconds=3.0, fade_seconds=.5,
                   reference_seconds=4.0, iters=1600, device="cpu"):
    fps = float(motion["fps"])
    E0 = np.asarray(motion["eul"], dtype=float)
    G = np.asarray(motion["G"], dtype=float)
    T = len(G)
    times = np.arange(T) / fps
    if not 0 <= start_seconds < times[-1] or fade_seconds <= 0:
        raise ValueError("Contact start must be within the clip and fade must be positive")
    if not start_seconds <= reference_seconds <= times[-1]:
        raise ValueError("Choose a reference hold interval within the contact interval")
    sk = Skeleton(rig, device=device)
    kp_indices = [i for i, k in enumerate(rig["keypoints"])
                  if rig["bones"][k["bone"]]["name"] == foot]
    if len(kp_indices) < 2:
        raise ValueError("Contact requires head and tip keypoints of the foot bone")
    bi = rig["keypoints"][kp_indices[0]]["bone"]
    # Do not alter global or axial bones; keep refinement limited to the limb.
    chain = []
    parent = rig["bones"][bi]["parent"]
    while parent >= 0 and len(chain) < 2:
        if parent in sk.active:
            chain.append(sk.active.index(parent))
        parent = rig["bones"][parent]["parent"]
    if len(chain) != 2:
        raise ValueError("Need two active parent joints to refine this contact")
    if E0.shape != (T, sk.A, 3):
        raise ValueError("Motion does not match the prepared rig")
    _, kp0 = sk.fk_np(G, E0)
    # Pick a real quiet-hold pose, instead of independently medianing two points (which could
    # change the fixed ankle-to-tip distance and make the anchor impossible by construction).
    ref_ids = np.flatnonzero(times >= reference_seconds)
    med = np.median(kp0[ref_ids][:, kp_indices], axis=0)
    ref = int(ref_ids[np.argmin(np.sum((kp0[ref_ids][:, kp_indices] - med) ** 2, axis=(1, 2)))])
    anchor = kp0[ref, kp_indices].copy()
    phase = np.clip((times - start_seconds) / fade_seconds, 0, 1)
    alpha = phase ** 2 * (3 - 2 * phase)
    # Blended target prevents an abrupt snap on entry to contact.
    target = kp0[:, kp_indices] * (1 - alpha[:, None, None]) + anchor[None] * alpha[:, None, None]
    dtype = torch.float32
    td = lambda x: torch.tensor(x, dtype=dtype, device=device)
    base = td(E0)
    lim = sk.t_lim[chain]
    mid, half = lim.mean(-1), (lim[..., 1] - lim[..., 0]) / 2
    safe = half.clamp(min=1e-6)
    init = torch.atanh(((base[:, chain] - mid) / safe).clamp(-.99999, .99999))
    params = torch.nn.Parameter(init.clone())
    al = td(alpha)[:, None, None]
    tG, tgt = td(G), td(target)
    opt = torch.optim.Adam([params], lr=.025)
    def objective():
        angles = mid + half * torch.tanh(params)
        E = base.clone()
        # alpha zero -> bit-identical unchanged original angles before contact.
        E[:, chain] = base[:, chain] * (1 - al) + angles * al
        world, kp = sk.fk(tG, E)
        err = (kp[:, kp_indices] - tgt) / sk.size
        dE = E[:, chain] - base[:, chain]
        accel = E[2:, chain] - 2 * E[1:-1, chain] + E[:-2, chain]
        # Full contact dominates only the selected foot; all-frame regularisation prevents
        # chasing the remaining noisy frame-wise root movement with discontinuous leg angles.
        loss = 10000 * err.square().mean() + .002 * dE.square().mean() + .1 * accel.square().mean()
        return loss
    for i in range(iters):
        opt.zero_grad()
        loss = objective()
        loss.backward()
        opt.step()
    # Adam's final updates can oscillate slightly around a tight anchor. Finish with a
    # line-searched full-clip solve rather than exporting its last stochastic-size step.
    polish = torch.optim.LBFGS([params], lr=.5, max_iter=150, tolerance_grad=1e-9,
                               tolerance_change=1e-11, line_search_fn="strong_wolfe")
    def closure():
        polish.zero_grad()
        loss = objective()
        loss.backward()
        return loss
    polish.step(closure)
    with torch.no_grad():
        angles = mid + half * torch.tanh(params)
        E = base.clone()
        E[:, chain] = base[:, chain] * (1 - al) + angles * al
        _, kp = sk.fk(tG, E)
    E = E.cpu().numpy()
    # Preserve all unconstrained channels exactly (no float32 recast drift).
    Eout = E0.copy()
    Eout[:, chain] = E[:, chain]
    Eout[times <= start_seconds] = E0[times <= start_seconds]
    _, kp = sk.fk_np(G, Eout)
    result = dict(G=G.copy(), eul=Eout, quat=continuous_quats(sk.local_quats(Eout)), kp=kp, fps=fps)
    full = times >= start_seconds + fade_seconds
    def metrics(P):
        pos = P[full][:, kp_indices]
        delta = np.linalg.norm(np.diff(pos, axis=0), axis=-1)
        acc = np.linalg.norm(np.diff(pos, n=2, axis=0), axis=-1)
        return {"point_range_xyz_pct_size": (np.ptp(pos, axis=0) / sk.size * 100).tolist(),
                "step_p90_pct_size": float(np.percentile(delta, 90) / sk.size * 100),
                "accel_rms_pct_size_per_frame2": float(np.sqrt(np.mean(acc ** 2)) / sk.size * 100),
                "anchor_error_p90_pct_size": float(np.percentile(np.linalg.norm(pos - anchor, axis=-1), 90) / sk.size * 100)}
    report = {"foot": foot, "start_seconds": start_seconds, "fade_seconds": fade_seconds,
              "reference_start_seconds": reference_seconds, "anchor_frame": ref,
              "anchor_points": anchor.tolist(),
              "modified_bones": [rig["bones"][sk.active[c]]["name"] for c in chain],
              "before_contact": metrics(kp0), "after_contact": metrics(kp),
              "max_local_angle_change_deg": float(np.max(np.abs(Eout-E0)) * 180 / np.pi),
              "note": "Operator-selected planted interval and quiet-hold anchor. Not automatic contact detection, not verified physical sole placement."}
    return result, report


def contact_export(project, source, name, foot="ankle.L", start_seconds=3.0,
                   fade_seconds=.5, reference_seconds=4.0):
    project = os.path.abspath(project)
    for n in (source, name):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", n):
            raise ValueError("Use safe source and output names")
    src, dst = [os.path.join(project, "out", n) for n in (source, name)]
    if os.path.exists(dst):
        raise ValueError("Preserving existing output; choose a new name")
    with open(os.path.join(project, "rig.json")) as f: rig=json.load(f)
    with open(os.path.join(project, "studio.json")) as f: studio=json.load(f)
    with np.load(os.path.join(src, "motion.npz")) as z: motion={k:z[k].copy() for k in z.files}
    with np.load(os.path.join(src, "reading.npz")) as z: reading={k:z[k].copy() for k in z.files}
    with open(os.path.join(src, "summary.json")) as f: summary=json.load(f)
    t0=time.time()
    result, report=contact_refine(rig,motion,foot,start_seconds,fade_seconds,reference_seconds)
    report["source"]=source
    report["before"]=temporal_metrics(rig,motion,studio,reading)
    report["after"]=temporal_metrics(rig,result,studio,reading)
    os.makedirs(dst)
    np.savez(os.path.join(dst,"motion.npz"),**result)
    for fn in ("check.json","reading.npz","diagnostics.json","diagnostic.mp4"):
        f=os.path.join(src,fn)
        if os.path.isfile(f): shutil.copy2(f,os.path.join(dst,fn))
    with open(os.path.join(dst,"contact.json"),"w") as f:json.dump(report,f,indent=1)
    from v2r.cli import run_blender
    run_blender("export_anim.py",project,os.path.join(dst,"motion.npz"),dst,
                json.dumps({"name":name,"fps":result["fps"],"render":True}),
                log=os.path.join(dst,"export.log"))
    # Walk provenance for videos produced before source_video metadata was introduced.
    original=summary
    visited=set()
    clipname=source
    while not original.get("source_video") and original.get("stabilized_from") not in visited:
        par=original.get("stabilized_from")
        if not par:break
        visited.add(par)
        clipname=par
        path=os.path.join(project,"out",par,"summary.json")
        if not os.path.isfile(path):break
        with open(path) as f: original=json.load(f)
    video=original.get("source_video") or os.path.join(os.path.dirname(os.path.dirname(project)),"videos",clipname+".mp4")
    if os.path.isfile(video):
        from v2r.read import read_video_frames,split_views,find_split
        from v2r.pipeline import make_lab_video
        frames,_=read_video_frames(video,max_frames=len(result["G"]))
        split=original.get("split_x") or find_split(frames)
        panels=[split_views(f,split,studio,original.get("swap",False))[0] for f in frames]
        proj=np.moveaxis(Cams(studio).project_np(result["kp"]),0,1)[...,:2]
        make_lab_video(project,dst,panels,reading["uv"],reading["conf"],proj,result["fps"],rig,studio)
    summary.update({"status":"forced_preview" if summary.get("forced") else "contact_refined",
                    "contact_from":source,"contact_foot":foot,"seconds":time.time()-t0,
                    "reproj_px_median":report["after"]["reprojection_median_px"],
                    "reproj_px_p90":report["after"]["reprojection_p90_px"]})
    with open(os.path.join(dst,"summary.json"),"w") as f:json.dump(summary,f,indent=1)
    print(json.dumps(report,indent=1))
    return report
