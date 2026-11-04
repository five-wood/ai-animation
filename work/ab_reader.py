"""A/B of reader-side options (e.g. TTA) on one project: re-read the exam / strike-exam videos (2D ground
truth) and the real clip, then grade the reading itself and the legacy+temporal solves.  Nothing in the
project is written: readings go to --out, grading goes to a scratch dir.

    /data/video2rig/.venv/bin/python ab_reader.py --code /tmp/v2r_tta --project ../projects/m11101 \
        --real Seedance25_Split_screen_two_loc_1793704727806 --tta 1,4,8 --out /tmp/ab_tta.json
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import torch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--real", required=True)
    ap.add_argument("--tta", default="1,8")
    ap.add_argument("--solve", default="legacy,temporal")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, a.code)
    from v2r.read import load_reader, read_video_frames, split_views, find_split, run_reader
    from v2r.geom import Cams
    from v2r.solve import fix_swaps, triangulate_all, clean_tracks, fit, consistency_report
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ab_solver

    P = a.project
    rig = json.load(open(os.path.join(P, "rig.json")))
    studio = json.load(open(os.path.join(P, "studio.json")))
    cams = Cams(studio)
    model, st = load_reader(P, "cuda")
    mods = ab_solver.load_code(a.code)
    vids = {"exam": (os.path.join(P, "exam", "exam_video.mp4"), "half", "exam"),
            "strike": (os.path.join(P, "exam_strike", "exam_video.mp4"), "half", "exam_strike")}
    summ = json.load(open(os.path.join(P, "out", a.real, "summary.json")))
    vids["real"] = (summ["source_video"], summ.get("split_x"), None)
    results = {}
    for tta in [int(x) for x in a.tta.split(",")]:
        rec = {}
        for tag, (path, split, exam_dir) in vids.items():
            frames, fps = read_video_frames(path)
            sp = frames[0].shape[1] // 2 if split == "half" else (int(split) if split else find_split(frames))
            panels = [split_views(f, sp, studio)[0] for f in frames]
            t0 = time.time()
            uv, conf, dep = run_reader(model, st, panels, "cuda", tta=tta)
            t_read = time.time() - t0
            r = {"read_s": round(t_read, 1)}
            T = len(uv)
            size_px = float(np.median(np.ptp(uv.reshape(-1, uv.shape[2], 2), axis=1).max(-1)))
            # 2D reading quality
            pad = np.pad(uv, ((2, 2), (0, 0), (0, 0), (0, 0)), mode="edge")
            med = np.median(np.stack([pad[i:i + T] for i in range(5)]), 0)
            r["jump_px_mean"] = float(np.linalg.norm(uv - med, axis=-1).mean())
            if exam_dir:
                gt = json.load(open(os.path.join(P, exam_dir, "frames", "motion_gt.json")))
                names = [c["name"] for c in studio["cams"]]
                uvg = np.array([[f[n]["uvz"] for n in names] for f in gt["frames"]])[..., :2]
                vis = np.array([[f[n]["vis"] for n in names] for f in gt["frames"]]) > 0
                e = np.linalg.norm(uv - uvg, axis=-1)
                r["gt_err_px_mean"] = float(e.mean())
                r["gt_err_px_visible"] = float(e[vis].mean())
                r["gt_err_px_hidden"] = float(e[~vis].mean()) if (~vis).any() else None
                r["gt_err_px_p90"] = float(np.percentile(e, 90))
                kn = [k["name"] for k in rig["keypoints"]]
                hands = [i for i, n in enumerate(kn) if "Hand" in n or "Finger" in n]
                r["gt_err_px_hands"] = float(e[:, :, hands].mean())
                # frame-to-frame error change (jitter of the error itself)
                r["gt_err_jitter_px"] = float(np.linalg.norm(np.diff(uv - uvg, axis=0), axis=-1).mean())
            # build a reading dict like pipeline.animate (legacy path) for the solvers
            uv_s, conf_s, n_sw = fix_swaps(cams, rig, uv, conf)
            X, res = triangulate_all(cams, uv_s, conf_s)
            rep = consistency_report(cams, rig, X, res, conf_s, uv_s)
            cf = conf_s * np.where(np.array(rep["bad_mask"])[:, None, None], 0.3, 1.0)
            cf, _, _ = clean_tracks(uv_s, cf, res)
            X_fit, _ = triangulate_all(cams, uv_s, cf)
            rd = {"uv": uv_s, "conf": conf_s, "depth": dep, "res": res, "conf_fit": cf, "X_fit": X_fit, "X": X}
            r["check_bad_frames"] = int(rep["bad_frames"])
            for solver in a.solve.split(","):
                variant = solver if solver != "temporal" else 'temporal:{"robust_pos":0.003,"robust_rot":0.02}'
                torch.manual_seed(0)
                sol = ab_solver.solve_variant(mods, rig, studio, rd, fps, variant)
                if exam_dir:
                    g = ab_solver.exam_metrics(a.code, P, rig, sol, exam_dir=exam_dir)
                    r[solver] = {k: g[k] for k in ("joint_err_mean_pct_of_size", "bone_angle_mean_deg", "twist_mean_deg")}
                    if "impact" in g:
                        r[solver]["impact"] = g["impact"]
                else:
                    m = ab_solver.real_metrics(mods, sol["rig"], studio, rd, sol, fps)
                    r[solver] = {k: m[k] for k in ("local_jerk_rms_deg", "res_px_median_hands", "res_px_median_body",
                                                   "impact_peak_mean_pct", "impact_stop_frames_mean")}
                    r[solver]["Rhand_step_jerk"] = (m["step_deg[Bip001 R Hand]"], m["jerk_deg[Bip001 R Hand]"])
            rec[tag] = r
            line = f"[tta] tta={tta} {tag:6s} read {r['read_s']:5.1f}s jump {r['jump_px_mean']:.2f}px"
            if exam_dir:
                line += (f" gt {r['gt_err_px_mean']:.2f}px (vis {r['gt_err_px_visible']:.2f}, hid {r['gt_err_px_hidden'] or float('nan'):.2f},"
                         f" hands {r['gt_err_px_hands']:.2f}, jitter {r['gt_err_jitter_px']:.2f})")
            for solver in a.solve.split(","):
                s = r[solver]
                if exam_dir:
                    line += f" | {solver} {s['joint_err_mean_pct_of_size']:.2f}%/{s['bone_angle_mean_deg']:.1f}"
                    if "impact" in s:
                        line += f"/peak {s['impact']['peak_speed_ratio']:.0%}"
                else:
                    line += (f" | {solver} jerk {s['local_jerk_rms_deg']:.2f} Rhand {s['Rhand_step_jerk'][0]:.2f}/{s['Rhand_step_jerk'][1]:.2f}"
                             f" res-hands {s['res_px_median_hands']:.2f} peak {s['impact_peak_mean_pct']:.1f}% stop {s['impact_stop_frames_mean']:.1f}")
            print(line, flush=True)
        results[tta] = rec
    json.dump(results, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
