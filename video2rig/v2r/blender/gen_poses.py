"""Step 2: render random poses of the monster from the two studio cameras.

Every sample: bend every active joint at random *inside its limits*, turn and move
the body, give about half the samples a velocity so EEVEE motion blur smears the
fast limbs (like Seedance does), render front + side as RGBA, and save exact
2D joint positions, camera depth and visibility.

Also used for the exam: mode "motion" renders a smooth made-up motion as a clip.

blender -b --factory-startup --python gen_poses.py -- <workdir> <outdir> <json-options>
  options: mode=random|motion, n, start, seed, fast_frac, samples, motion_file
"""
import os
import sys
import json
import time
import math
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *  # noqa: E402,F401,F403

args = script_args()
workdir, outdir = args[0], args[1]
opt = json.loads(args[2]) if len(args) > 2 else {}
mode = opt.get("mode", "random")
os.makedirs(outdir, exist_ok=True)

rig = load_json(os.path.join(workdir, "rig.json"))
studio = load_json(os.path.join(workdir, "studio.json"))
popt = load_json(os.path.join(workdir, "prepare_opts.json")) if os.path.exists(
    os.path.join(workdir, "prepare_opts.json")) else {}
arm, meshes = load_model(rig["model"], procedural_material=True if popt.get("procedural_material") else None,
                         up=popt.get("up", "+Z"))
assert len(arm.data.bones) == len(rig["bones"]), "rig.json does not match the model"
check_world_matrix(arm, rig)

cams = build_cameras(studio)
sc = setup_render(studio, samples=opt.get("samples", 16), blur=True, shutter=opt.get("shutter", 1.0))
sc.render.image_settings.compression = 15
B = len(rig["bones"])
K = len(rig["keypoints"])
roots = root_indices(rig)
active = [i for i, b in enumerate(rig["bones"]) if b["active"]]
center = np.array(rig["center"])
size = rig["size"]


def euler_quat(e_deg):
    return np.array(list(Euler([math.radians(a) for a in e_deg], "XYZ").to_quaternion()))


def global_matrix(yaw, pitch, roll, tx, ty, tz):
    """Rotate about the creature's own centre, then translate (world space)."""
    R = (Matrix.Rotation(math.radians(yaw), 4, "Z") @ Matrix.Rotation(math.radians(pitch), 4, "X")
         @ Matrix.Rotation(math.radians(roll), 4, "Y"))
    c = Vector(center.tolist())
    G = Matrix.Translation(c + Vector((tx, ty, tz))) @ R @ Matrix.Translation(-c)
    return mat_np(G)


# ------------------------------------------------------------------ random pose sampler
def sample_pose(rng):
    amp = rng.uniform(0.25, 1.0)
    eul = np.zeros((B, 3))
    for i in active:
        lim = rig["bones"][i]["limits"]
        if rng.random() < 0.15:
            continue  # leave some joints at rest
        for a in range(3):
            lo, hi = lim[a]
            eul[i, a] = amp * rng.uniform(lo, hi) if rng.random() < 0.85 else rng.uniform(lo, hi)
    r = rng.random()
    if r < 0.55:
        yaw = rng.gauss(0, 20)
    elif r < 0.7:
        yaw = rng.choice([90, -90, 180]) + rng.gauss(0, 20)
    else:
        yaw = rng.uniform(-180, 180)
    pitch = rng.gauss(0, 10) if rng.random() < 0.85 else rng.uniform(-70, 70)
    roll = rng.gauss(0, 8) if rng.random() < 0.85 else rng.uniform(-60, 60)
    tx, ty = rng.uniform(-0.15, 0.15) * size, rng.uniform(-0.15, 0.15) * size
    tz = rng.uniform(-0.08, 0.08) * size if rng.random() < 0.7 else rng.uniform(0, 0.3) * size
    return eul, np.array([yaw, pitch, roll, tx, ty, tz])


def pose_to_arrays(eul, g):
    q = np.tile([1.0, 0, 0, 0], (B, 1))
    for i in active:
        q[i] = euler_quat(eul[i])
    return q, global_matrix(*g)


# ------------------------------------------------------------------ smooth made-up motion (the exam)
def smooth_noise(rng, T, n_comp=3, fmin=0.2, fmax=1.6, fps=30.0):
    t = np.arange(T) / fps
    out = np.zeros(T)
    for _ in range(n_comp):
        f = rng.uniform(fmin, fmax)
        out += rng.uniform(0.3, 1.0) * np.sin(2 * math.pi * f * t + rng.uniform(0, 2 * math.pi))
    return out / max(1e-6, np.abs(out).max())


def make_motion(rng, T):
    eul = np.zeros((T, B, 3))
    for i in active:
        lim = rig["bones"][i]["limits"]
        for a in range(3):
            lo, hi = lim[a]
            mid, half = 0.5 * (lo + hi), 0.5 * (hi - lo)
            eul[:, i, a] = mid + 0.8 * half * smooth_noise(rng, T)
    g = np.zeros((T, 6))
    g[:, 0] = 35 * smooth_noise(rng, T, fmax=0.4)
    g[:, 1] = 8 * smooth_noise(rng, T, fmax=0.6)
    g[:, 2] = 6 * smooth_noise(rng, T, fmax=0.6)
    g[:, 3] = 0.1 * size * smooth_noise(rng, T, fmax=0.4)
    g[:, 4] = 0.1 * size * smooth_noise(rng, T, fmax=0.4)
    g[:, 5] = 0.06 * size * np.abs(smooth_noise(rng, T, fmax=1.2))
    return eul, g


# ------------------------------------------------------------------ labels
def labels_for_current_pose():
    bpy.context.view_layer.update()
    kp = keypoints_world(arm, rig)
    bvh, _ = bvh_from_meshes(meshes)
    out = {}
    for c in studio["cams"]:
        p = project(c, kp)
        cam_pos = np.array(c["blender"]["loc"])
        vis = np.zeros(K)
        for k in range(K):
            d = kp[k] - cam_pos
            dist = np.linalg.norm(d)
            dn = Vector((d / dist).tolist())
            # count surface crossings between camera and joint: 0/1 = visible (the joint
            # sits inside its own body part), >=2 = something else is in front of it
            o, travelled, n_hit = Vector(cam_pos.tolist()), 0.0, 0
            while n_hit < 4:
                hit = bvh.ray_cast(o, dn, dist - travelled)
                if hit[0] is None:
                    break
                n_hit += 1
                step = hit[3] + 1e-4 * size
                travelled += step
                o = o + dn * step
            vis[k] = 1.0 if n_hit <= 1 else 0.0
        inside = (p[:, 0] >= 0) & (p[:, 0] < c["w"]) & (p[:, 1] >= 0) & (p[:, 1] < c["h"])
        out[c["name"]] = {"uvz": p, "vis": vis * inside}
    return kp, out


def key_pose(frame, q, G):
    apply_pose(arm, rig, G, q, frame=frame, key=True)


# ------------------------------------------------------------------ main loops
t0 = time.time()
if mode == "random":
    n, start, seed = opt.get("n", 100), opt.get("start", 0), opt.get("seed", 0)
    fast_frac = opt.get("fast_frac", 0.5)
    rng = random.Random(seed * 100003 + start)
    meta = []
    for j in range(n):
        idx = start + j
        if j % 4 == 0:
            setup_lights(studio, rng)
        eul, g = sample_pose(rng)
        fast = rng.random() < fast_frac
        if fast:
            s = rng.uniform(0.3, 1.0)
            deul = np.zeros_like(eul)
            for i in active:
                deul[i] = [s * rng.gauss(0, 12) for _ in range(3)]
            dg = np.array([rng.gauss(0, 6), rng.gauss(0, 3), rng.gauss(0, 3),
                           rng.gauss(0, 0.04) * size, rng.gauss(0, 0.04) * size, rng.gauss(0, 0.03) * size]) * s
        else:
            deul, dg = np.zeros_like(eul), np.zeros(6)
        base = 3 * j + 1
        for off, sgn in ((0, -1), (1, 0), (2, 1)):
            q, G = pose_to_arrays(eul + sgn * deul, g + sgn * dg)
            key_pose(base + off, q, G)
        set_linear_interp(arm)
        sc.frame_set(base + 1)
        kp, lab = labels_for_current_pose()
        q, G = pose_to_arrays(eul, g)
        rec = {"id": idx, "fast": fast, "kp3d": kp.tolist(), "G": G.tolist(), "quat": q.tolist()}
        for c, co in zip(studio["cams"], cams):
            fn = f"{idx:06d}_{c['name']}.png"
            render_to(os.path.join(outdir, fn), co)
            rec[c["name"]] = {"img": fn, "uvz": lab[c["name"]]["uvz"].tolist(),
                              "vis": lab[c["name"]]["vis"].tolist()}
        meta.append(rec)
        # drop keys so the next sample starts clean
        arm.animation_data_clear()
        if j % 20 == 0:
            el = time.time() - t0
            print(f"[v2r] {j + 1}/{n} samples  {el / (j + 1):.2f}s/sample", flush=True)
    with open(os.path.join(outdir, f"meta_{start:06d}.json"), "w") as f:
        json.dump(meta, f)

elif mode == "motion":
    T, seed = opt.get("frames", 120), opt.get("seed", 1)
    rng = random.Random(seed)
    eul, g = make_motion(rng, T)
    setup_lights(studio)
    frames = []
    for t in range(T):
        q, G = pose_to_arrays(eul[t], g[t])
        key_pose(t + 1, q, G)
    set_linear_interp(arm)
    sc.frame_start, sc.frame_end = 1, T
    for t in range(T):
        sc.frame_set(t + 1)
        kp, lab = labels_for_current_pose()
        q, G = pose_to_arrays(eul[t], g[t])
        rec = {"t": t, "kp3d": kp.tolist(), "G": G.tolist(), "quat": q.tolist()}
        for c, co in zip(studio["cams"], cams):
            fn = f"{t:05d}_{c['name']}.png"
            render_to(os.path.join(outdir, fn), co)
            rec[c["name"]] = {"img": fn, "uvz": lab[c["name"]]["uvz"].tolist(),
                              "vis": lab[c["name"]]["vis"].tolist()}
        frames.append(rec)
        if t % 20 == 0:
            print(f"[v2r] motion frame {t + 1}/{T}", flush=True)
    with open(os.path.join(outdir, "motion_gt.json"), "w") as f:
        json.dump({"fps": 30, "frames": frames, "euler": eul.tolist(), "global": g.tolist()}, f)
print(f"[v2r] gen done in {time.time() - t0:.1f}s")
