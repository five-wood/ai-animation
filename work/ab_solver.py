"""Offline A/B of solver variants on saved readings (no reader, no render, no export).

Run on the server from the code directory::

    /data/video2rig/.venv/bin/python /path/ab_solver.py --project ../projects/m11101 \
        --real Seedance25_Split_screen_two_loc_1793704727806 --variants legacy,temporal --out /tmp/ab.json

Inputs are reconstructed exactly as v2r.pipeline.animate does after reading:
legacy uses reading.npz conf_fit/X_fit, temporal recomputes observation weights from
uv/conf/res (heatmap quality is not saved, so those two factors are neutral for both
A and B of the same solver) and re-triangulates.  Every variant of one solver family
therefore receives identical observations; only the solver changes.

Metrics
-------
real clip (no ground truth): rotation jitter of selected bones relative to their parent,
hand/finger reading-vs-solution residual, impact of the three swings (peak hand speed,
frames to stop, peak deceleration, all in the front view), overall reprojection.
exam (ground truth): grade() of v2r.exam without writing exam_report.json.
"""
import argparse
import importlib
import json
import os
import sys
import time

import numpy as np
import torch


def load_code(code):
    sys.path.insert(0, code)
    import v2r.solve as solve
    import v2r.solve_temporal as solve_temporal
    importlib.reload(solve)
    importlib.reload(solve_temporal)
    from v2r.geom import Cams, Skeleton, unit_rot_np
    from v2r.solve import triangulate_all
    from v2r.observations import observation_weights
    return solve, solve_temporal, Cams, Skeleton, unit_rot_np, triangulate_all, observation_weights


def freeze_rig(rig, names):
    """Same as prepare marking these bones inactive (they ride with their parent)."""
    if not names:
        return rig
    rig = json.loads(json.dumps(rig))
    for b in rig["bones"]:
        if b["name"] in names:
            b["active"] = False
            b["folded"] = True
    rig["n_active"] = int(sum(b["active"] for b in rig["bones"]))
    return rig


def solve_variant(mods, rig, studio, rd, fps, variant):
    solve, solve_temporal, Cams, Skeleton, unit_rot_np, triangulate_all, observation_weights = mods
    family, _, opts = variant.partition(":")
    kw = json.loads(opts) if opts else {}
    freeze = kw.pop("freeze", [])
    corroborate = kw.pop("corroborate", False)
    rig = freeze_rig(rig, freeze)
    if family == "temporal":
        kw.setdefault("robust_pos", 0.003)  # the temporal default since 2026-11-04 (pipeline.py)
        kw.setdefault("robust_rot", 0.02)
    uv, dep = rd["uv"], rd["depth"]
    cams = Cams(studio)
    if family == "legacy":
        if "conf_fit" in rd:
            conf_s, X_fit = rd["conf_fit"], rd["X_fit"]
        else:  # older readings (e.g. dog_v2) did not save the solver inputs
            conf_s, X_fit = rd["conf"], rd["X"]
        sol = solve.fit(rig, studio, uv, conf_s, X_fit, depth=dep, verbose=False, **kw)
    elif family == "temporal":
        conf_s, _ = observation_weights(rig, studio, uv, rd["conf"], rd["res"], fps, None, corroborate=corroborate)
        X_fit, _ = triangulate_all(cams, uv, conf_s)
        sol = solve_temporal.fit_temporal(rig, studio, uv, conf_s, X_fit, fps, depth=dep, verbose=False, **kw)
    else:
        raise ValueError(family)
    sol["rig"] = rig
    return sol


def real_metrics(mods, rig, studio, rd, sol, fps):
    _, _, Cams, Skeleton, unit_rot_np, _, _ = mods
    names = [b["name"] for b in rig["bones"]]
    kn = [k["name"] for k in rig["keypoints"]]
    sk = Skeleton(rig)
    w = sol["world"]
    out = {}

    def rel_jitter(child, parent):
        Rp = unit_rot_np(w[:, names.index(parent), :3, :3])
        Rc = unit_rot_np(w[:, names.index(child), :3, :3])
        rel = np.einsum("tji,tjk->tik", Rp, Rc)
        step = np.einsum("tji,tjk->tik", rel[:-1], rel[1:])
        ang = np.degrees(np.arccos(np.clip((np.trace(step, axis1=1, axis2=2) - 1) / 2, -1, 1)))
        acc = np.abs(np.diff(ang))
        return float(ang.mean()), float(acc.mean())

    pairs = [("Bone146", "Bip001 Head"), ("Bip001 R Hand", "Bip001 R Forearm"), ("Bip001 L Hand", "Bip001 L Forearm"),
             ("Bip001 R Forearm", "Bip001 R UpperArm"), ("Bip001 L Forearm", "Bip001 L UpperArm"),
             ("Bone142", "Bone002"), ("Bone0022", "Bone0021")]
    for c, p in pairs:
        if c in names and p in names:
            out[f"step_deg[{c}]"], out[f"jerk_deg[{c}]"] = rel_jitter(c, p)
    # whole-body rotational jerk of active bones (local Euler 2nd diff)
    e = sol["eul"]
    out["local_jerk_rms_deg"] = float(np.sqrt(np.mean(np.degrees(np.linalg.norm(np.diff(e, n=2, axis=0), axis=-1)) ** 2)))
    # residual of hand / finger readings vs solution
    uv = rd["uv"]
    res = np.linalg.norm(sol["proj"] - uv, axis=-1)  # T,V,K
    hand = [i for i, n in enumerate(kn) if "Hand" in n or "Finger" in n]
    body = [i for i, n in enumerate(kn) if any(s in n for s in ("Spine", "Neck", "Head", "Pelvis"))]
    out["res_px_median_all"] = float(np.median(res))
    out["res_px_median_hands"] = float(np.median(res[:, :, hand]))
    out["res_px_median_body"] = float(np.median(res[:, :, body]))
    # impact of the hand swings, front view, faster hand per frame
    size_px = float(np.median(np.ptp(uv.reshape(-1, uv.shape[2], 2), axis=1).max(-1)))
    hk = [kn.index("Bip001 R Hand"), kn.index("Bip001 L Hand")]
    track = sol["proj"][:, 0, hk]
    sp = (np.linalg.norm(np.diff(track, axis=0), axis=-1) / size_px * 100).max(1)
    sp_read = (np.linalg.norm(np.diff(uv[:, 0, hk], axis=0), axis=-1) / size_px * 100).max(1)
    imp = []
    for a, b in ((0.4, 1.2), (1.8, 2.7), (3.8, 4.7)):
        i, j = int(a * fps), int(b * fps)
        s = sp[i:j]
        p = int(np.argmax(s))
        after = s[p:]
        f90 = max([n for n in range(len(after)) if after[n] >= 0.9 * s[p]] or [0])
        f20 = next((n for n in range(f90, len(after)) if after[n] <= 0.2 * s[p]), len(after))
        imp.append({"peak_pct": float(s[p]), "stop_frames": int(f20 - f90), "peak_decel_pct": float(np.max(-np.diff(s))),
                    "read_peak_pct": float(sp_read[i:j].max())})
    out["impact"] = imp
    out["impact_peak_mean_pct"] = float(np.mean([x["peak_pct"] for x in imp]))
    out["impact_stop_frames_mean"] = float(np.mean([x["stop_frames"] for x in imp]))
    out["impact_decel_mean_pct"] = float(np.mean([x["peak_decel_pct"] for x in imp]))
    return out


SCRATCH = "/tmp/v2r_ab_scratch"


def exam_metrics(code, project, rig, sol, exam_dir="exam"):
    """grade() always writes <project>/<exam_dir>/exam_report.json, so it is pointed at a
    scratch project directory: the real project is only ever READ (an earlier version of this
    harness truncated the live report by opening it for writing)."""
    from v2r.exam import grade
    gt = json.load(open(os.path.join(project, exam_dir, "frames", "motion_gt.json")))
    os.makedirs(os.path.join(SCRATCH, exam_dir), exist_ok=True)
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        r = grade(SCRATCH, rig, gt, sol["G"], sol["world"], sol["kp"], float(np.median(sol["rep_px"])),
                  exam_dir=exam_dir)
    keys = ("joint_err_mean_pct_of_size", "bone_angle_mean_deg", "bone_angle_p90_deg", "twist_mean_deg", "passed")
    out = {k: r[k] for k in keys}
    if "impact" in r:
        out["impact"] = r["impact"]
    out["per_bone"] = {n: (round(v["mean_deg"], 2), round(v["twist_mean_deg"], 2)) for n, v in r["per_bone"].items()}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="/data/video2rig/code")
    ap.add_argument("--project", required=True)
    ap.add_argument("--real", required=True, help="output name of the real clip under out/")
    ap.add_argument("--variants", required=True, help="comma separated family[:json-kwargs]; use ; between variants if kwargs contain commas")
    ap.add_argument("--skip-exam", action="store_true")
    ap.add_argument("--exam-only", action="store_true", help="no real clip (other creatures)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    mods = load_code(a.code)
    rig = json.load(open(os.path.join(a.project, "rig.json")))
    studio = json.load(open(os.path.join(a.project, "studio.json")))
    if not a.exam_only:
        real_dir = os.path.join(a.project, "out", a.real)
        rd_real = {k: v for k, v in np.load(os.path.join(real_dir, "reading.npz")).items()}
        fps_real = float(np.load(os.path.join(real_dir, "motion.npz"))["fps"])
    rd_exam = {k: v for k, v in np.load(os.path.join(a.project, "out", "exam", "reading.npz")).items()}
    fps_exam = float(np.load(os.path.join(a.project, "out", "exam", "motion.npz"))["fps"])
    strike = os.path.join(a.project, "out", "exam_strike")
    rd_strike = None
    if os.path.isfile(os.path.join(strike, "reading.npz")) and not a.skip_exam:
        rd_strike = {k: v for k, v in np.load(os.path.join(strike, "reading.npz")).items()}
        fps_strike = float(np.load(os.path.join(strike, "motion.npz"))["fps"])
    variants = a.variants.split(";") if ";" in a.variants else a.variants.split(",")
    results = {}
    for v in variants:
        torch.manual_seed(0)
        np.random.seed(0)
        t0 = time.time()
        rec = {}
        if not a.exam_only:
            sol = solve_variant(mods, rig, studio, rd_real, fps_real, v)
            rec["real"] = real_metrics(mods, sol["rig"], studio, rd_real, sol, fps_real)
        if not a.skip_exam:
            sol_e = solve_variant(mods, rig, studio, rd_exam, fps_exam, v)
            # grade against the ORIGINAL rig: frozen bones count with their true GT motion
            rec["exam"] = exam_metrics(a.code, a.project, rig, sol_e)
            if rd_strike is not None:
                sol_s = solve_variant(mods, rig, studio, rd_strike, fps_strike, v)
                rec["strike"] = exam_metrics(a.code, a.project, rig, sol_s, exam_dir="exam_strike")
        rec["seconds"] = time.time() - t0
        results[v] = rec
        line = f"[ab] {v:40s}"
        if "real" in rec:
            r = rec["real"]
            line += (f" jitter: goggles {r.get('step_deg[Bone146]', float('nan')):5.2f}/{r.get('jerk_deg[Bone146]', float('nan')):5.2f}  "
                     f"Rhand {r['step_deg[Bip001 R Hand]']:5.2f}/{r['jerk_deg[Bip001 R Hand]']:5.2f}  local {r['local_jerk_rms_deg']:5.2f}  "
                     f"res hands {r['res_px_median_hands']:5.2f} body {r['res_px_median_body']:5.2f}  "
                     f"impact peak {r['impact_peak_mean_pct']:5.2f}% stop {r['impact_stop_frames_mean']:.1f}f decel {r['impact_decel_mean_pct']:5.2f}")
        if "exam" in rec:
            e = rec["exam"]
            line += (f"  | exam joint {e['joint_err_mean_pct_of_size']:.2f}% limb {e['bone_angle_mean_deg']:.1f} "
                     f"(p90 {e['bone_angle_p90_deg']:.1f}) twist {e['twist_mean_deg']:.1f} pass={e['passed']}")
        if "strike" in rec:
            s = rec["strike"]
            im = s.get("impact") or {}
            line += (f"  | strike joint {s['joint_err_mean_pct_of_size']:.2f}% limb {s['bone_angle_mean_deg']:.1f} "
                     f"peak-kept {im.get('peak_speed_ratio', float('nan')):.0%} lag {im.get('stop_lag_frames', float('nan')):.2f}f "
                     f"overshoot {im.get('overshoot_pct_of_size', float('nan')):.2f}%")
        print(line + f"  {rec['seconds']:.0f}s", flush=True)
    json.dump(results, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
