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
                         up=popt.get("up", "+Z"), drop_props=bool(popt.get("drop_props")))
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


# ------------------------------------------------------------------ contact poses (reach)
# Joint angles drawn independently almost never put two hands together or a hand on the body, yet those
# are exactly the poses in which a hand is hard to see (a fist on the chest, both hands on one weapon).
# reach_pose() bends a free limb chain with its own joints, inside their limits, so its end lands on a
# chosen target.  Pure numerical IK on the rig's own FK (no Blender evaluation), so it is cheap.
def _limb_chains():
    """Free limb chains (shoulder side first) whose end carries a keypoint: only L/R chains."""
    kids = {i: [j for j, b in enumerate(rig["bones"]) if b["parent"] == i] for i in range(B)}
    act = set(active)
    chains = []
    for i in active:
        b = rig["bones"][i]
        if b.get("side") not in ("L", "R") or b.get("joint") not in ("clavicle", "shoulder"):
            continue
        p = b["parent"]
        while p >= 0 and p not in act:
            p = rig["bones"][p]["parent"]
        if p >= 0 and rig["bones"][p].get("side") in ("L", "R"):
            continue  # not the root of its chain
        chain, cur = [i], i
        while True:
            nxt = [j for j in kids[cur] if j in act] or \
                  [k for j in kids[cur] for k in kids[j] if k in act]
            if not nxt:
                break
            cur = nxt[0]
            chain.append(cur)
        if len(chain) >= 2:
            chains.append((b["side"], chain))
    return chains


_REST_W = None


def _fk_end(eul, g, chain):
    """World position of the end of `chain` (tail of its last bone) for the pose (eul, g)."""
    global _REST_W
    if _REST_W is None:
        _REST_W = np.array(rig["world_matrix"])
    W = _REST_W
    G = global_matrix(*g)
    rest = [np.array(b["rest"]) for b in rig["bones"]]
    # forward kinematics along the full ancestry of the end bone
    end = chain[-1]
    path = []
    j = end
    while j >= 0:
        path.append(j)
        j = rig["bones"][j]["parent"]
    path = path[::-1]
    A = np.linalg.inv(W) @ G @ W
    prev = None
    for j in path:
        off = rest[j] if prev is None else np.linalg.inv(rest[prev]) @ rest[j]
        R = np.eye(4)
        R[:3, :3] = np.array(Euler([math.radians(a) for a in eul[j]], "XYZ").to_matrix())
        A = A @ off @ R
        prev = j
    tip = A @ np.array([0.0, rig["bones"][end]["length"], 0.0, 1.0])
    return (W @ tip)[:3]


def reach_pose(eul, g, chain, target, iters=40):
    """Bend `chain` (inside its limits) so its end moves toward `target` (world).  Coordinate descent on
    the chain's Euler channels with a step that shrinks; stays close to the drawn pose otherwise."""
    eul = eul.copy()
    step = 12.0
    best = np.linalg.norm(_fk_end(eul, g, chain) - target)
    for _ in range(iters):
        improved = False
        for j in chain:
            lim = rig["bones"][j]["limits"]
            for a in range(3):
                for sgn in (1, -1):
                    old = eul[j, a]
                    eul[j, a] = float(np.clip(old + sgn * step, lim[a][0], lim[a][1]))
                    d = np.linalg.norm(_fk_end(eul, g, chain) - target)
                    if d < best - 1e-6:
                        best, improved = d, True
                    else:
                        eul[j, a] = old
        if not improved:
            step *= 0.5
            if step < 0.5:
                break
    return eul, best


def contact_variant(eul, g, rng, together=0.18, onto_body=0.17):
    """With probability `together` bring the ends of the two free arms together in front of the body,
    with `onto_body` lay one arm's end on the body's own surface (a random skin point not on that arm).
    Returns (eul, kind)."""
    chains = _limb_chains()
    if not chains:
        return eul, None
    u = rng.random()
    fwd = np.array(rig["forward"]) if "forward" in rig else np.array([0.0, -1.0, 0.0])
    up = np.array([0.0, 0.0, 1.0])
    if u < together:
        left = [c for s, c in chains if s == "L"]
        right = [c for s, c in chains if s == "R"]
        if not left or not right:
            return eul, None
        a, b = left[0], right[0]
        ea, eb = _fk_end(eul, g, a), _fk_end(eul, g, b)
        reach = 0.5 * np.linalg.norm(ea - eb)
        mid = 0.5 * (ea + eb)
        d = fwd + 0.3 * up * rng.uniform(-1, 1)  # mostly in front of the body, a little up or down
        target = mid + d / max(np.linalg.norm(d), 1e-9) * reach * rng.uniform(0.2, 0.6)
        off = np.array([rng.gauss(0, 1) for _ in range(3)])
        off *= 0.04 * size / max(np.linalg.norm(off), 1e-9)
        eul, _ = reach_pose(eul, g, a, target + off)
        eul, _ = reach_pose(eul, g, b, target - off)
        return eul, "together"
    if u < together + onto_body:
        side, chain = chains[int(rng.random() * len(chains))]
        q, G = pose_to_arrays(eul, g)
        apply_pose(arm, rig, G, q)  # the skin in the drawn pose, to pick a target on its surface
        bpy.context.view_layer.update()
        bvh_, verts = bvh_from_meshes(meshes)
        tips = np.array([_fk_end(eul, g, c) for _, c in chains])
        far = np.linalg.norm(verts[:, None, :] - tips[None], axis=-1).min(1) > 0.12 * size
        cand = np.nonzero(far)[0]
        if not len(cand):
            return eul, None
        target = verts[int(cand[int(rng.random() * len(cand))])]
        eul, _ = reach_pose(eul, g, chain, target)
        return eul, "onto_body"
    return eul, None


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


def make_strike_motion(rng, T, fps=30.0):
    """Exam motion with impacts: hold -> fast accelerating move -> abrupt stop -> hold ...
    Every move ends at full speed and stops within one frame (an ease-in curve without
    ease-out), like a punch or a strike.  All active joints share the key times; each joint
    gets a new random target inside its limits at every key.  Returns eul, g, impacts
    (list of {"start", "end"} frame indices of each move; the impact is at "end")."""
    keys, t = [0], int(0.4 * fps)          # Seedance-like still start
    keys.append(t)
    while True:
        move = rng.choice([2, 3, 4])
        hold = rng.randint(int(0.25 * fps), int(0.55 * fps))
        if t + move + hold >= T - 1:
            break
        keys += [t + move, t + move + hold]
        t += move + hold
    targets = {}
    for i in active:
        lim = rig["bones"][i]["limits"]
        pose = np.zeros(3)
        seq = [pose]
        for _ in range(len(keys) // 2):
            nxt = np.array([0.5 * (lo + hi) + 0.75 * 0.5 * (hi - lo) * rng.uniform(-1, 1) for lo, hi in lim])
            seq.append(nxt)
        targets[i] = seq
    eul = np.zeros((T, B, 3))
    impacts = []
    for n in range(1, len(keys) - 1, 2):
        s, e = keys[n], keys[n + 1]
        impacts.append({"start": int(s), "end": int(e)})
    seg = 0
    for f in range(T):
        # which move are we in / after
        while seg < len(impacts) and f >= impacts[seg]["end"]:
            seg += 1
        for i in active:
            seq = targets[i]
            if seg < len(impacts) and f >= impacts[seg]["start"]:
                s, e = impacts[seg]["start"], impacts[seg]["end"]
                u = (f - s) / (e - s)
                w = u * u  # ease-in only: full speed at the end, then stop
                eul[f, i] = seq[seg] + (seq[seg + 1] - seq[seg]) * w
            else:
                eul[f, i] = seq[seg]
    g = np.zeros((T, 6))
    g[:, 0] = 20 * smooth_noise(rng, T, fmax=0.3)
    g[:, 1] = 5 * smooth_noise(rng, T, fmax=0.4)
    g[:, 2] = 4 * smooth_noise(rng, T, fmax=0.4)
    g[:, 3] = 0.06 * size * smooth_noise(rng, T, fmax=0.3)
    g[:, 4] = 0.06 * size * smooth_noise(rng, T, fmax=0.3)
    return eul, g, impacts


# ------------------------------------------------------------------ labels
# Visibility ("is this keypoint seen in this view?") is decided from what the picture shows at the keypoint's
# pixel: the keypoint counts as seen when the first surface its camera ray (or a ray a few pixels around it) hits
# belongs to the keypoint's own body segment - the bone it sits on, the bone above it or a bone below it.  A joint
# inside the body (the pelvis root, a shoulder) is therefore seen whenever its own segment faces the camera; it is
# hidden only when ANOTHER part (an arm in front of the chest, the body in front of the far hand) covers it.
# The older test ("does the ray from the camera to the joint cross more than one surface") marked every joint
# that lies inside thick skin as hidden, even when nothing else covered it.
VIS_MODE = opt.get("visibility", "segment")
VIS_WINDOW_PX = 3.0
_ACT = set(active)


def _carrier(i):
    """The bone whose motion carries bone i: nearest active bone at or above it, else its chain's top bone."""
    j = i
    while j >= 0 and j not in _ACT:
        j = rig["bones"][j]["parent"]
    if j >= 0:
        return j
    j = i
    while rig["bones"][j]["parent"] >= 0:
        j = rig["bones"][j]["parent"]
    return j


def _triangle_carriers():
    """Carrier bone of every triangle in the same order as bvh_from_meshes (meshes in order, evaluated loop
    triangles): the carrier with the largest summed skin weight over the triangle's three vertices."""
    by_name = {b["name"]: i for i, b in enumerate(rig["bones"])}
    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for m in meshes:
        gname = {g.index: g.name for g in m.vertex_groups}
        vw = []
        for v in m.data.vertices:
            d = {}
            for g in v.groups:
                bi = by_name.get(gname.get(g.group))
                if bi is not None and g.weight > 1e-3:
                    c = _carrier(bi)
                    d[c] = d.get(c, 0.0) + g.weight
            vw.append(d)
        ev = m.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        for tri in me.loop_triangles:
            s = {}
            for vi in tri.vertices:
                for c, w in vw[vi].items():
                    s[c] = s.get(c, 0.0) + w
            out.append(max(s, key=s.get) if s else -1)
        ev.to_mesh_clear()
    return np.array(out, int)


def _keypoint_segments():
    """Allowed carriers per keypoint: its own bone's, its parent's and its children's."""
    kids = {i: [j for j, b in enumerate(rig["bones"]) if b["parent"] == i] for i in range(B)}
    out = []
    for k in rig["keypoints"]:
        b = k["bone"]
        s = {_carrier(b)}
        if rig["bones"][b]["parent"] >= 0:
            s.add(_carrier(rig["bones"][b]["parent"]))
        s |= {_carrier(j) for j in kids[b]}
        out.append(s)
    return out


TRI_CARRIER = _triangle_carriers() if VIS_MODE == "segment" else None
KP_SEGMENTS = _keypoint_segments() if VIS_MODE == "segment" else None
_WIN = [(0.0, 0.0)] + [(VIS_WINDOW_PX * math.cos(a), VIS_WINDOW_PX * math.sin(a))
                       for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)]


def _vis_segment(c, p, bvh):
    K_, R_, t_ = np.array(c["K"]), np.array(c["R"]), np.array(c["t"])
    Kinv = np.linalg.inv(K_)
    C = -R_.T @ t_
    o = Vector(C.tolist())
    far = 20.0 * float(studio.get("distance", 10.0 * size))
    vis = np.zeros(K)
    for k in range(K):
        for du, dv in _WIN:
            d = R_.T @ (Kinv @ np.array([p[k, 0] + du, p[k, 1] + dv, 1.0]))
            d = d / np.linalg.norm(d)
            hit = bvh.ray_cast(o, Vector(d.tolist()), far)
            if hit[0] is not None and hit[2] is not None and TRI_CARRIER[hit[2]] in KP_SEGMENTS[k]:
                vis[k] = 1.0
                break
    return vis


def _vis_ray(c, kp, bvh):
    cam_pos = np.array(c["blender"]["loc"])
    vis = np.zeros(K)
    for k in range(K):
        d = kp[k] - cam_pos
        dist = np.linalg.norm(d)
        dn = Vector((d / dist).tolist())
        # count surface crossings between camera and joint: 0/1 = visible, >=2 = something in front
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
    return vis


def labels_for_current_pose():
    bpy.context.view_layer.update()
    kp = keypoints_world(arm, rig)
    bvh, _ = bvh_from_meshes(meshes)
    out = {}
    for c in studio["cams"]:
        p = project(c, kp)
        inside = (p[:, 0] >= 0) & (p[:, 0] < c["w"]) & (p[:, 1] >= 0) & (p[:, 1] < c["h"])
        vis = _vis_segment(c, p, bvh) if VIS_MODE == "segment" else _vis_ray(c, kp, bvh)
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
        contact = None
        if opt.get("contact_poses"):
            eul, contact = contact_variant(eul, g, rng)
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
        if contact:
            rec["contact"] = contact
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
    impacts = []
    if opt.get("style", "smooth") == "strike":
        eul, g, impacts = make_strike_motion(rng, T)
    else:
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
        json.dump({"fps": 30, "frames": frames, "euler": eul.tolist(), "global": g.tolist(),
                   "style": opt.get("style", "smooth"), "impacts": impacts}, f)
print(f"[v2r] gen done in {time.time() - t0:.1f}s")
