"""video2rig command line.

  python -m v2r.cli prepare  <model.fbx|glb> <project> [--facing=-Y] [--up=+Z] [--procedural-material] [--no-drop-props]
  python -m v2r.cli gen      <project> [--n 3000] [--workers 8]
  python -m v2r.cli train    <project> [--epochs 40]
  python -m v2r.cli exam     <project> [--frames 150] [--seed 7]
  python -m v2r.cli animate  <project> <video.mp4> [--name clip] [--split auto|half] [--swap]
"""
import os
import sys
import json
import glob
import time
import shutil
import argparse
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BL_DIR = os.path.join(ROOT, "v2r", "blender")
BLENDER = os.environ.get("V2R_BLENDER", "/opt/unimate/blender-4.0.2-linux-x64/blender")


def run_blender(script, *args, log=None, quiet=True):
    cmd = [BLENDER, "-b", "--factory-startup", "--python", os.path.join(BL_DIR, script), "--", *args]
    if log:
        with open(log, "w") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    else:
        r = subprocess.run(cmd, capture_output=quiet, text=True)
        if quiet:
            for line in (r.stdout or "").splitlines():
                if line.startswith("[v2r]") or "Error" in line or "Traceback" in line:
                    print(line)
    if r.returncode != 0:
        raise SystemExit(f"blender {script} failed (rc={r.returncode}) log={log}")
    return r


def resolve_drop_props(flag, geometry_from):
    """--drop-props is on by default for a new project.  With --geometry-from the rig comes from the template
    project, so unless the flag is given explicitly the template's own setting is used (a template without
    the key was prepared with its props)."""
    if flag is not None:
        return bool(flag)
    if geometry_from:
        p = os.path.join(geometry_from, "prepare_opts.json")
        return bool(json.load(open(p)).get("drop_props")) if os.path.exists(p) else False
    return True


def cmd_prepare(a):
    a.drop_props = resolve_drop_props(a.drop_props, a.geometry_from)
    opt = {"facing": a.facing, "up": a.up, "procedural_material": a.procedural_material, "fill": a.fill,
           "panel_w": a.panel_w, "panel_h": a.panel_h, "min_seg_frac": a.min_seg, "keep_bones": a.keep_bones,
           "visual_profile": a.visual_profile, "geometry_from": a.geometry_from,
           "limits_mode": "generic" if a.generic_limits else "anatomical",
           "fist_points": a.fist_points, "offaxis_points": a.offaxis_points, "drop_props": a.drop_props}
    if a.limits:
        opt["limits"] = json.load(open(a.limits))
    os.makedirs(a.project, exist_ok=True)
    run_blender("prepare.py", os.path.abspath(a.model), os.path.abspath(a.project), json.dumps(opt), quiet=False)
    from PIL import Image
    ims = [Image.open(os.path.join(a.project, f"ref_{v}.png")).convert("RGBA") for v in ("front", "side")]
    W = sum(i.width for i in ims)
    out = Image.new("RGBA", (W, ims[0].height), (200, 200, 200, 255))
    x = 0
    for i in ims:
        out.alpha_composite(i, (x, 0))
        x += i.width
    out = out.convert("RGB")
    # thin divider like the split-screen in the video
    from PIL import ImageDraw
    ImageDraw.Draw(out).line([(ims[0].width, 0), (ims[0].width, out.height)], fill=(120, 120, 120), width=2)
    out.save(os.path.join(a.project, "reference_two_views.png"))
    print("[v2r] reference picture for Seedance:", os.path.join(a.project, "reference_two_views.png"))


def cmd_inspect(a):
    run_blender("inspect_model.py", os.path.abspath(a.model), quiet=True)


def cmd_gen(a):
    out = os.path.join(a.project, "train_data")
    if a.fresh and os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)
    logs = os.path.join(a.project, "logs")
    os.makedirs(logs, exist_ok=True)
    per = (a.n + a.workers - 1) // a.workers
    procs = []
    t0 = time.time()
    for w in range(a.workers):
        start = a.offset + w * per
        n = min(per, a.offset + a.n - start)
        if n <= 0:
            continue
        if os.path.exists(os.path.join(out, f"meta_{start:06d}.json")):
            continue
        opt = json.dumps({"mode": "random", "n": n, "start": start, "seed": a.seed,
                          "fast_frac": a.fast_frac, "samples": a.samples, "contact_poses": a.contact_poses,
                          "visibility": a.visibility})
        cmd = [BLENDER, "-b", "--factory-startup", "--python", os.path.join(BL_DIR, "gen_poses.py"), "--",
               os.path.abspath(a.project), os.path.abspath(out), opt]
        procs.append(subprocess.Popen(cmd, stdout=open(os.path.join(logs, f"gen_{start:06d}.log"), "w"),
                                      stderr=subprocess.STDOUT))
    rcs = [p.wait() for p in procs]
    print(f"[v2r] gen: {len(procs)} workers rc={rcs} {time.time() - t0:.0f}s")
    n_meta = sum(len(json.load(open(f))) for f in glob.glob(os.path.join(out, "meta_*.json")))
    print(f"[v2r] {n_meta} labelled samples in {out}")
    if any(rcs):
        raise SystemExit(1)


def cmd_train(a):
    from v2r.train import train
    train(a.project, epochs=a.epochs, batch=a.batch, lr=a.lr, backbone=a.backbone, img=a.img,
          workers=a.workers, steps=a.steps, hidden_empty=a.hidden_empty, smear_frac=a.smear)


def cmd_exam(a):
    if a.regrade:
        from v2r.exam import regrade
        regrade(a.project, style=a.style)
        return
    from v2r.exam import exam
    exam(a.project, frames=a.frames, seed=a.seed, render=not a.reuse, compress=not a.clean, style=a.style)


def cmd_animate(a):
    from v2r.pipeline import animate
    animate(a.project, a.video, name=a.name, split=a.split, swap=a.swap, fps=a.fps,
            max_frames=a.max_frames, force=a.force, calib_mode=a.calib, solver=a.solver, tta=a.tta)


def cmd_stabilize(a):
    from v2r.stabilize import stabilize
    stabilize(a.project, a.source, name=a.name, cutoff_hz=a.cutoff)


def cmd_contact(a):
    from v2r.contact import contact_export
    contact_export(a.project, a.source, a.name, foot=a.foot, start_seconds=a.start,
                   fade_seconds=a.fade, reference_seconds=a.reference)


def main(argv=None):
    p = argparse.ArgumentParser("v2r")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("inspect")
    s.add_argument("model")
    s.set_defaults(fn=cmd_inspect)
    s = sub.add_parser("prepare")
    s.add_argument("model")
    s.add_argument("project")
    s.add_argument("--facing", default="-Y", choices=["-Y", "+Y", "-X", "+X"],
                   help="look direction AFTER --up is applied; write as --facing=-Y")
    s.add_argument("--up", default="+Z", choices=["+Z", "-Z", "+Y", "-Y", "+X", "-X"],
                   help="model axis the head points to after import (model lying down -> e.g. --up=-Y); "
                        "the model is rotated so this axis becomes +Z. Write as --up=-Y")
    s.add_argument("--procedural-material", action="store_true")
    s.add_argument("--visual-profile", choices=["legacy", "soft-clean"], default="legacy",
                   help="soft-clean: broad studio light, no cast floor shadow or added shadow/dust")
    s.add_argument("--geometry-from", default=None, help="reuse exact rig/cameras from an existing project for A/B tests")
    s.add_argument("--limits", help="json {bone-regex: [[x0,x1],[y0,y1],[z0,z1]]} in degrees (overrides the rest)")
    s.add_argument("--generic-limits", action="store_true",
                   help="old fixed Euler boxes instead of per-joint anatomical ranges")
    s.add_argument("--fist-points", action="store_true",
                   help="a closed hand keeps its wrist point and one knuckle point instead of every finger point")
    s.add_argument("--offaxis-points", action="store_true",
                   help="two skin points off the bone's line on each hand and on the head (shows their twist)")
    s.add_argument("--drop-props", action=argparse.BooleanOptionalAction, default=None,
                   help="remove meshes that ride on their own root bone (a gun that is not skinned to the hand): "
                        "not rendered, no keypoints, not exported; the skeleton is unchanged.  On by default; "
                        "--no-drop-props keeps them (with --geometry-from the template's setting is the default)")
    s.add_argument("--fill", type=float, default=0.68)
    s.add_argument("--min-seg", type=float, default=0.06,
                   help="bones shorter than this fraction of the body size are folded into their parent")
    s.add_argument("--keep-bones", default=None, help="regex of bones never folded")
    s.add_argument("--panel-w", type=int, default=640)
    s.add_argument("--panel-h", type=int, default=720)
    s.set_defaults(fn=cmd_prepare)
    s = sub.add_parser("gen")
    s.add_argument("project")
    s.add_argument("--n", type=int, default=3000)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--fast-frac", type=float, default=0.5)
    s.add_argument("--samples", type=int, default=16)
    s.add_argument("--fresh", action="store_true")
    s.add_argument("--contact-poses", action="store_true",
                   help="about a third of the poses bring both hands together or lay one hand on the body")
    s.add_argument("--visibility", default="segment", choices=["segment", "ray"],
                   help="segment: a keypoint is seen when the surface at its pixel is its own body segment; "
                        "ray: the older 'camera-to-joint ray crosses at most one surface' test")
    s.set_defaults(fn=cmd_gen)
    s = sub.add_parser("train")
    s.add_argument("project")
    s.add_argument("--epochs", type=int, default=40)
    s.add_argument("--batch", type=int, default=32)
    s.add_argument("--lr", type=float, default=3e-4)
    s.add_argument("--img", type=int, default=448)
    s.add_argument("--workers", type=int, default=12)
    s.add_argument("--backbone", default="vit_base_patch14_dinov2.lvd142m")
    s.add_argument("--steps", type=int, default=None,
                   help="train exactly this many optimiser steps (overrides --epochs)")
    s.add_argument("--hidden-empty", action="store_true",
                   help="keypoints hidden by the body get an empty target: the reader learns a low peak there")
    s.add_argument("--smear", type=float, default=0.0,
                   help="share of training pictures with a limb smeared into a streak, e.g. 0.3")
    s.set_defaults(fn=cmd_train)
    s = sub.add_parser("exam")
    s.add_argument("project")
    s.add_argument("--frames", type=int, default=150)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--reuse", action="store_true", help="reuse the rendered exam clip")
    s.add_argument("--clean", action="store_true", help="no Seedance-like compression on the exam video")
    s.add_argument("--regrade", action="store_true",
                   help="only grade the existing out/exam/motion.npz again (no render, no solve)")
    s.add_argument("--style", default="smooth", choices=["smooth", "strike"],
                   help="strike: abrupt-stop moves graded for impact fidelity, kept in exam_strike/ "
                        "(the standard exam/ is untouched)")
    s.set_defaults(fn=cmd_exam)
    s = sub.add_parser("contact")
    s.add_argument("project")
    s.add_argument("source")
    s.add_argument("--name", required=True)
    s.add_argument("--foot", default="ankle.L")
    s.add_argument("--start", type=float, default=3.0)
    s.add_argument("--fade", type=float, default=.5)
    s.add_argument("--reference", type=float, default=4.0)
    s.set_defaults(fn=cmd_contact)
    s = sub.add_parser("stabilize")
    s.add_argument("project")
    s.add_argument("source", help="existing animation result name")
    s.add_argument("--name", default=None, help="new output name; source is never overwritten")
    s.add_argument("--cutoff", type=float, default=1.5, help="half-amplitude cutoff in Hz; lower = smoother")
    s.set_defaults(fn=cmd_stabilize)
    s = sub.add_parser("animate")
    s.add_argument("project")
    s.add_argument("video")
    s.add_argument("--name", default=None)
    s.add_argument("--split", default="auto")
    s.add_argument("--swap", action="store_true", help="video has side view on the left")
    s.add_argument("--fps", type=float, default=None)
    s.add_argument("--max-frames", type=int, default=None)
    s.add_argument("--force", action="store_true", help="continue even if the two views disagree")
    s.add_argument("--solver", default="legacy", choices=["legacy", "temporal"],
                   help="temporal: experimental reliability fusion + fps-normalized joint solve")
    s.add_argument("--calib", default="auto", choices=["auto", "off"],
                   help="frame-0 re-framing correction (video starts from the reference picture)")
    s.add_argument("--tta", type=int, default=1,
                   help="read every panel N times (shifted / zoomed a little) and average the heatmaps; "
                        "about N times slower to read, small gain with the current reader (see work/analyze/research)")
    s.set_defaults(fn=cmd_animate)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
