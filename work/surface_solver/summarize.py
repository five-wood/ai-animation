"""Summarise ab_*.json files: per variant, mean over exam clips of each creature/style, the per-clip spread,
paired differences against a reference variant, and the real-clip metrics.

    python summarize.py <ref variant> <file.json> [<file.json> ...]
"""
import json
import sys

import numpy as np

KEYS = [("joint_err_mean_pct_of_size", "joint%", 2), ("bone_angle_mean_deg", "limb", 2), ("twist_mean_deg", "twist", 2),
        ("body_deg", "body", 2)]


def group(clip):
    proj, tag = clip.split(":")
    creature = proj.split("_s")[0]
    return creature, tag


def main():
    ref = sys.argv[1]
    res = {}
    for f in sys.argv[2:]:
        for v, clips in json.load(open(f)).items():
            res.setdefault(v, {}).update(clips)
    if ref not in res:
        print("reference variant missing; have:", list(res))
        return
    groups = sorted({group(c) for c in res[ref] if not c.endswith(":real")})
    for v in res:
        print(f"\n=== {v}")
        for g in groups:
            clips = sorted(c for c in res[v] if not c.endswith(":real") and group(c) == g and c in res[ref])
            if not clips:
                continue
            line = f"  {g[0]:7s} {g[1]:12s} n={len(clips)}"
            for k, name, nd in KEYS:
                x = np.array([res[v][c][k] for c in clips])
                d = x - np.array([res[ref][c][k] for c in clips])
                line += f" | {name} {x.mean():.{nd}f}"
                if v != ref:
                    line += f" (d {d.mean():+.{nd}f}, {int((d < 0).sum())}/{len(d)} better, worst {d.max():+.{nd}f})"
                else:
                    line += f" (sd {x.std():.{nd}f}, range {x.min():.{nd}f}..{x.max():.{nd}f})"
            if g[1] == "exam_strike":
                pk = np.array([res[v][c]["impact"]["peak_speed_ratio"] for c in clips])
                lg = np.array([res[v][c]["impact"]["stop_lag_frames"] for c in clips])
                line += f" | peak {pk.mean():.0%} lag {lg.mean():.2f}"
            print(line)
        for c in sorted(x for x in res[v] if x.endswith(":real")):
            r = res[v][c]
            print(f"  REAL {c}: Rhand step/jerk {r['step_deg[Bip001 R Hand]']:.2f}/{r['jerk_deg[Bip001 R Hand]']:.2f} "
                  f"Lhand {r['step_deg[Bip001 L Hand]']:.2f}/{r['jerk_deg[Bip001 L Hand]']:.2f} local jerk "
                  f"{r['local_jerk_rms_deg']:.2f} | impact peak {r['impact_peak_mean_pct']:.2f}% stop "
                  f"{r['impact_stop_frames_mean']:.1f}f | res hands {r['res_px_median_hands']:.2f} body "
                  f"{r['res_px_median_body']:.2f}")


if __name__ == "__main__":
    main()
