"""Blender-side helpers shared by every v2r Blender script.

Runs inside Blender's bundled Python (numpy available).  All data that crosses
into the training / solving code is written as JSON or .npz.

Conventions
-----------
* "armature space"  : Blender bone matrix_local / pose_bone.matrix space.
* "world space"     : world = W @ armature, W = armature object matrix_world.
* Global motion G   : 4x4 in world space, applied as world = G @ W @ M_b.
* Local rotation    : pose_bone.matrix_basis rotation of every non-root bone.
* Cameras           : OpenCV convention (x right, y down, z forward), K/R/t.
"""
import bpy
import bmesh  # noqa: F401  (kept for scripts that import *)
import sys
import os
import re
import json
import math
import random
import numpy as np
from mathutils import Matrix, Vector, Quaternion, Euler
from mathutils.bvhtree import BVHTree

F_CV = np.diag([1.0, -1.0, -1.0])

DEFAULT_EXCLUDE = r"(?i)(face|eye|lid|brow|lip|tongue|teeth|tooth|nub|twist|_end$|\.end$|ik_|_ik$|pole|target|helper|ctrl)"


# ----------------------------------------------------------------------------- utils
def script_args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def mat_np(m):
    return np.array([list(r) for r in m], dtype=np.float64)


def np_mat(a):
    return Matrix([[float(v) for v in r] for r in a])


def save_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------- model
def load_model(path, procedural_material=None):
    """Import FBX/GLB/BLEND, keep only the main armature and its meshes, rest pose."""
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=path, use_anim=False)
        elif ext in (".glb", ".gltf"):
            bpy.ops.import_scene.gltf(filepath=path)
        else:
            raise SystemExit(f"unsupported model format: {ext}")
    scene = bpy.context.scene
    arms = [o for o in scene.objects if o.type == "ARMATURE"]
    if not arms:
        raise SystemExit("NO_ARMATURE: the model has no skeleton - run `v2r autorig` first")
    arm = max(arms, key=lambda a: len(a.data.bones))

    def bound(o):
        if o.type != "MESH":
            return False
        if any(m.type == "ARMATURE" and m.object == arm for m in o.modifiers):
            return True
        p = o.parent
        while p is not None:
            if p == arm:
                return True
            p = p.parent
        return False

    meshes = [o for o in scene.objects if bound(o)]
    if not meshes:
        raise SystemExit("NO_SKINNED_MESH: no mesh is bound to the armature")
    for o in list(scene.objects):
        if o is not arm and o not in meshes:
            bpy.data.objects.remove(o, do_unlink=True)
    for o in [arm] + meshes:
        o.hide_render = False
        o.hide_viewport = False
        o.animation_data_clear()
        if o.type == "MESH" and o.data.shape_keys is not None:
            o.data.shape_keys.animation_data_clear()
    for pb in arm.pose.bones:
        for c in list(pb.constraints):
            pb.constraints.remove(c)
        pb.rotation_mode = "QUATERNION"
        pb.matrix_basis = Matrix.Identity(4)
    ensure_materials(meshes, procedural_material)
    bpy.context.view_layer.update()
    return arm, meshes


def _material_ok(mat):
    if mat is None:
        return False
    if not mat.use_nodes or mat.node_tree is None:
        return True
    for n in mat.node_tree.nodes:
        if n.type == "TEX_IMAGE":
            img = n.image
            if img is None or img.size[0] == 0:
                return False
    return True


def procedural_material(name="v2r_skin", seed=0):
    rng = random.Random(seed)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    coord = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 6.0
    noise.inputs["Detail"].default_value = 8.0
    vor = nt.nodes.new("ShaderNodeTexVoronoi")
    vor.inputs["Scale"].default_value = 25.0
    mix = nt.nodes.new("ShaderNodeMath")
    mix.operation = "MULTIPLY_ADD"
    mix.inputs[1].default_value = 0.6
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    base = (0.25 + 0.1 * rng.random(), 0.18 + 0.08 * rng.random(), 0.12 + 0.05 * rng.random(), 1)
    ramp.color_ramp.elements[0].color = (base[0] * 0.35, base[1] * 0.35, base[2] * 0.35, 1)
    ramp.color_ramp.elements[1].color = (base[0] * 1.6, base[1] * 1.5, base[2] * 1.4, 1)
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.35
    nt.links.new(coord.outputs["Object"], noise.inputs["Vector"])
    nt.links.new(coord.outputs["Object"], vor.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], mix.inputs[0])
    nt.links.new(vor.outputs["Distance"], mix.inputs[2])
    nt.links.new(mix.outputs[0], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(mix.outputs[0], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = 0.75
    return mat


def ensure_materials(meshes, force=None):
    """Replace missing/broken materials (missing textures render pink) with a procedural skin."""
    proc = None
    for m in meshes:
        mats = list(m.data.materials)
        bad = force is True or not mats or any(not _material_ok(x) for x in mats)
        if bad:
            if proc is None:
                proc = procedural_material()
            m.data.materials.clear()
            m.data.materials.append(proc)
            print(f"[v2r] mesh {m.name}: using procedural material (textures missing)")


def mesh_world_arrays(meshes):
    """Evaluated (posed) mesh vertices in world space + triangle indices."""
    dg = bpy.context.evaluated_depsgraph_get()
    vs, tris, off = [], [], 0
    for m in meshes:
        ev = m.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        n = len(me.vertices)
        co = np.empty(n * 3)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        M = mat_np(ev.matrix_world)
        co = co @ M[:3, :3].T + M[:3, 3]
        t = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", t)
        vs.append(co)
        tris.append(t.reshape(-1, 3) + off)
        off += n
        ev.to_mesh_clear()
    return np.concatenate(vs), np.concatenate(tris)


def bvh_from_meshes(meshes):
    v, t = mesh_world_arrays(meshes)
    return BVHTree.FromPolygons(v.astype(float).tolist(), t.astype(int).tolist(), all_triangles=True), v


# ----------------------------------------------------------------------------- rig
def topo_bones(arm):
    order = []

    def rec(b):
        order.append(b)
        for c in b.children:
            rec(c)

    for b in arm.data.bones:
        if b.parent is None:
            rec(b)
    return order


def weighted_groups(meshes, thr=0.02):
    names = set()
    for m in meshes:
        if m.parent_type == "BONE" and m.parent_bone:
            names.add(m.parent_bone)
        idx = {g.index: g.name for g in m.vertex_groups}
        mx = {}
        for v in m.data.vertices:
            for g in v.groups:
                if g.weight > mx.get(g.group, 0.0):
                    mx[g.group] = g.weight
        names |= {idx[i] for i, w in mx.items() if w > thr and i in idx}
    return names


def default_limits(side, is_root):
    if is_root:
        return [[-180, 180], [-180, 180], [-180, 180]]
    if side == "M":
        return [[-35, 35], [-20, 20], [-30, 30]]
    return [[-75, 75], [-30, 30], [-50, 50]]


def extract_rig(arm, meshes, facing="-Y", exclude=DEFAULT_EXCLUDE, merge_frac=0.015,
                limits_override=None, min_seg_frac=0.06, keep_bones=None):
    W = mat_np(arm.matrix_world)
    bones = topo_bones(arm)
    index = {b.name: i for i, b in enumerate(bones)}
    verts, _ = mesh_world_arrays(meshes)
    lo, hi = verts.min(0), verts.max(0)
    size = float(np.max(hi - lo))
    height = float(hi[2] - lo[2])
    center = ((lo + hi) / 2).tolist()
    fwd = {"-Y": (0, -1, 0), "+Y": (0, 1, 0), "-X": (-1, 0, 0), "+X": (1, 0, 0)}[facing]
    fwd = np.array(fwd, float)
    lateral = np.cross(fwd, [0, 0, 1.0])  # creature's right-hand side direction
    deform = weighted_groups(meshes)
    excl = re.compile(exclude) if exclude else None

    def to_world(p):
        p = np.asarray(p, float)
        return (W[:3, :3] @ p + W[:3, 3])

    # keypoint candidates
    kps = []
    has_deform_desc = {}
    for b in reversed(bones):
        has_deform_desc[b.name] = any((c.name in deform) or has_deform_desc[c.name] for c in b.children)
    for b in bones:
        if b.name not in deform and not has_deform_desc[b.name]:
            continue
        if excl and excl.search(b.name):
            continue
        if b.name in deform or has_deform_desc[b.name]:
            kps.append({"name": b.name, "bone": index[b.name], "type": "head",
                        "rest_world": to_world(b.head_local).tolist()})
        if b.name in deform and not has_deform_desc[b.name]:
            kps.append({"name": b.name + ".tip", "bone": index[b.name], "type": "tail",
                        "rest_world": to_world(b.tail_local).tolist()})
    # merge coincident keypoints and "fold the fingers into the hand": a keypoint closer than
    # min_seg_frac * size to an already kept keypoint is dropped (keep first in topological order)
    kept = []
    for k in kps:
        p = np.array(k["rest_world"])
        if any(np.linalg.norm(p - np.array(q["rest_world"])) < max(merge_frac, min_seg_frac) * size for q in kept):
            continue
        kept.append(k)
    kps = kept
    # "cut off whatever sticks out of the body": a tip keypoint is placed at the bone tail, or
    # just inside the surface if the (often guessed) tail of an imported leaf bone pokes out.
    # Tips of bones that already have a child keypoint are dropped (FBX Biped bones often have
    # meaningless tails pointing sideways).
    bvh, _ = bvh_from_meshes(meshes)
    head_kp_bones = {k["bone"] for k in kps if k["type"] == "head"}
    child_has_kp = {}
    for b in bones:
        child_has_kp[index[b.name]] = any(index[c.name] in head_kp_bones for c in b.children_recursive)
    kps = [k for k in kps if not (k["type"] == "tail" and child_has_kp[k["bone"]])]
    # flat exported hierarchies (3ds Max Biped -> FBX: hand, forearm, upper arm all children of
    # the neck): a tail keypoint is redundant when another keypoint sits near the tail
    kps = [k for k in kps if not (k["type"] == "tail" and any(
        q is not k and np.linalg.norm(np.array(q["rest_world"]) - to_world(bones[k["bone"]].tail_local))
        < 0.05 * size for q in kps))]
    for k in kps:
        b = bones[k["bone"]]
        k["tail_len"] = 0.0
        if k["type"] != "tail":
            continue
        h, t = to_world(b.head_local), to_world(b.tail_local)
        L = float(np.linalg.norm(t - h))
        frac = 1.0
        if L > 1e-9:
            d = (t - h) / L
            hit = bvh.ray_cast(Vector(h.tolist()), Vector(d.tolist()), L)
            if hit[0] is not None:
                frac = max(0.25, 0.85 * hit[3] / L)
        k["tail_len"] = float(b.length * frac)
        k["rest_world"] = (h + (t - h) * frac).tolist()
        if frac < 1.0:
            print(f"[v2r] tip of {b.name} clipped to {frac:.2f} of the bone (it stuck out of the mesh)")
    # side / mirror
    c = np.array(center)
    for k in kps:
        lat = float(np.dot(np.array(k["rest_world"]) - c, lateral))
        k["lat"] = lat
        k["side"] = "M" if abs(lat) < 0.02 * size else ("R" if lat > 0 else "L")
    for i, k in enumerate(kps):
        k["mirror"] = -1
        if k["side"] == "M":
            continue
        p = np.array(k["rest_world"])
        pm = p - 2 * np.dot(p - c, lateral) * lateral
        best, bd = -1, 0.06 * size
        for j, q in enumerate(kps):
            if j != i and q["side"] not in ("M", k["side"]):
                d = np.linalg.norm(np.array(q["rest_world"]) - pm)
                if d < bd:
                    best, bd = j, d
        k["mirror"] = best
    # bone records
    brec = []
    kp_bones = {}
    for ki, k in enumerate(kps):
        kp_bones.setdefault(k["bone"], []).append(ki)
    for i, b in enumerate(bones):
        side_lat = float(np.dot(to_world(b.head_local) - c, lateral))
        side = "M" if abs(side_lat) < 0.02 * size else ("R" if side_lat > 0 else "L")
        lim = default_limits(side, b.parent is None)
        if limits_override:
            for pat, v in limits_override.items():
                if re.search(pat, b.name):
                    lim = v
        brec.append({"name": b.name, "parent": index[b.parent.name] if b.parent else -1,
                     "rest": mat_np(b.matrix_local).tolist(), "length": b.length,
                     "head_world": to_world(b.head_local).tolist(),
                     "tail_world": to_world(b.tail_local).tolist(),
                     "side": side, "limits": lim, "deform": b.name in deform})
    # which bone rotations are observable through some keypoint
    anc = []
    for i, b in enumerate(brec):
        a, p = [], b["parent"]
        while p >= 0:
            a.append(p)
            p = brec[p]["parent"]
        anc.append(a)
    force_active = set()
    if keep_bones:
        kb = re.compile(keep_bones)
        force_active = {i for i, b in enumerate(brec) if kb.search(b["name"])}
    moved = [set() for _ in brec]
    for ki, k in enumerate(kps):
        bi = k["bone"]
        movers = list(anc[bi]) + ([bi] if k["type"] == "tail" else [])
        for m in movers:
            if np.linalg.norm(np.array(k["rest_world"]) - np.array(brec[m]["head_world"])) > 1e-3 * size:
                moved[m].add(ki)
    for m, b in enumerate(brec):
        others = {ki for ki, k in enumerate(kps)
                  if np.linalg.norm(np.array(k["rest_world"]) - np.array(b["head_world"])) > 1e-3 * size}
        # a bone that moves every keypoint is redundant with the global transform
        b["active"] = bool(b["parent"] >= 0 and moved[m] and moved[m] != others)
        b["n_moved"] = len(moved[m])
        # "fold the fingers into the hand": a bone whose whole subtree (all keypoints it moves)
        # stays within a small radius rides rigidly with its parent (fingers, ears, toes)
        hw = np.array(b["head_world"])
        reach = max([np.linalg.norm(np.array(kps[ki]["rest_world"]) - hw) for ki in moved[m]] or [0.0])
        b["reach"] = float(reach)
        # fold only the small end-pieces (fingers, ears, toes): everything the bone moves stays
        # close to its head AND the bone has no deforming child chain of real length
        if b["active"] and reach < min_seg_frac * size and m not in force_active:
            b["active"] = False
            b["folded"] = True
    # skeleton edges between keypoints (for drawing): keypoint -> nearest keypoint ancestor
    kp_of_bone_head = {k["bone"]: i for i, k in enumerate(kps) if k["type"] == "head"}
    edges = []
    for i, k in enumerate(kps):
        cand = ([k["bone"]] if k["type"] == "tail" else []) + anc[k["bone"]]
        for a in cand:
            if a in kp_of_bone_head and kp_of_bone_head[a] != i:
                edges.append([kp_of_bone_head[a], i])
                break
    warn = []
    flat = [b["name"] for b in brec if b["parent"] >= 0 and not b["active"] and b["deform"]
            and len(bones[[x["name"] for x in brec].index(b["name"])].children) == 0]
    if len(kps) < 4:
        warn.append("very few keypoints")
    return {
        "armature": arm.name, "world_matrix": W.tolist(), "bones": brec, "keypoints": kps,
        "edges": edges, "size": size, "height": height, "center": center,
        "ground_z": float(lo[2]), "bbox": [lo.tolist(), hi.tolist()], "facing": facing,
        "forward": fwd.tolist(), "lateral": lateral.tolist(), "warnings": warn,
        "n_active": int(sum(b["active"] for b in brec)),
    }


# ----------------------------------------------------------------------------- pose
def root_indices(rig):
    return [i for i, b in enumerate(rig["bones"]) if b["parent"] < 0]


def apply_pose(arm, rig, G, local_quats, frame=None, key=False):
    """G: 4x4 world-space global transform; local_quats: (B,4) wxyz for every bone."""
    W = np.array(rig["world_matrix"])
    Winv = np.linalg.inv(W)
    pbs = arm.pose.bones
    for i, b in enumerate(rig["bones"]):
        pb = pbs[b["name"]]
        q = Quaternion([float(x) for x in local_quats[i]])
        if b["parent"] < 0:
            rest = np.array(b["rest"])
            basis = np.linalg.inv(rest) @ Winv @ np.asarray(G) @ W @ rest @ np.array(q.to_matrix().to_4x4())
            mb = np_mat(basis)
            loc, rq, _ = mb.decompose()
            pb.location = loc
            prev = pb.rotation_quaternion.copy()
            if prev.dot(rq) < 0:
                rq.negate()
            pb.rotation_quaternion = rq
            if key:
                pb.keyframe_insert("location", frame=frame)
        else:
            prev = pb.rotation_quaternion.copy()
            if prev.dot(q) < 0:
                q.negate()
            pb.location = (0, 0, 0)
            pb.rotation_quaternion = q
        pb.scale = (1, 1, 1)
        if key:
            pb.keyframe_insert("rotation_quaternion", frame=frame)


def keypoints_world(arm, rig):
    """Current (evaluated) keypoint positions in world space."""
    Wm = arm.matrix_world
    out = []
    for k in rig["keypoints"]:
        b = rig["bones"][k["bone"]]
        pb = arm.pose.bones[b["name"]]
        if k["type"] == "head":
            p = pb.head
        else:
            frac = k.get("tail_len", b["length"]) / max(b["length"], 1e-12)
            p = pb.head + (pb.tail - pb.head) * frac
        out.append(list(Wm @ p))
    return np.array(out)


def set_linear_interp(arm):
    ad = arm.animation_data
    if ad and ad.action:
        for fc in ad.action.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"


# ----------------------------------------------------------------------------- studio
def look_at_quat(pos, target):
    d = Vector(target) - Vector(pos)
    return d.to_track_quat("-Z", "Y")


def make_studio(rig, panel_w=640, panel_h=720, lens=50.0, sensor_h=24.0, elev_deg=8.0, fill=0.5,
                views=("front", "side")):
    lo, hi = np.array(rig["bbox"][0]), np.array(rig["bbox"][1])
    ext = hi - lo
    fwd = np.array(rig["forward"])
    lat = np.array(rig["lateral"])
    up = np.array([0, 0, 1.0])
    tan_v = (sensor_h / 2) / lens
    tan_h = tan_v * panel_w / panel_h
    ext_f = abs(np.dot(ext, np.abs(fwd)))
    ext_l = abs(np.dot(ext, np.abs(lat)))
    ext_z = ext[2]
    target = np.array(rig["center"], float)
    target[2] = rig["ground_z"] + 0.5 * max(ext_z, 0.35 * rig["size"])
    # common distance so both panels share the same scale
    # frame on the typical extent (rest-pose T/A arms are wider than most poses)
    need_h = max(0.8 * max(ext_f, ext_l), 0.6 * ext_z)
    need_v = max(ext_z, 0.45 * rig["size"])
    d = max(need_h / (2 * tan_h * fill), need_v / (2 * tan_v * fill)) + 0.5 * min(ext_f, ext_l)
    cams = []
    dirs = {"front": fwd, "side": lat, "back": -fwd, "side2": -lat}
    for vi, v in enumerate(views):
        dvec = dirs[v]
        pos = target + dvec * d * math.cos(math.radians(elev_deg)) + up * d * math.sin(math.radians(elev_deg))
        q = look_at_quat(pos, target)
        Rbl = np.array(q.to_matrix())  # camera->world
        R_w2c_bl = Rbl.T
        t_bl = -R_w2c_bl @ pos
        R = F_CV @ R_w2c_bl
        t = F_CV @ t_bl
        f = lens / sensor_h * panel_h
        K = [[f, 0, panel_w / 2.0], [0, f, panel_h / 2.0], [0, 0, 1]]
        cams.append({"name": v, "K": K, "R": R.tolist(), "t": t.tolist(), "w": panel_w, "h": panel_h,
                     "x0": vi * panel_w, "y0": 0,
                     "blender": {"loc": pos.tolist(), "quat": list(q), "lens": lens, "sensor_h": sensor_h}})
    return {"panel_w": panel_w, "panel_h": panel_h, "width": panel_w * len(views), "height": panel_h,
            "cams": cams, "target": target.tolist(), "distance": float(d), "ground_z": rig["ground_z"]}


def build_cameras(studio):
    objs = []
    for c in studio["cams"]:
        cd = bpy.data.cameras.new("cam_" + c["name"])
        cd.lens = c["blender"]["lens"]
        cd.sensor_fit = "VERTICAL"
        cd.sensor_height = c["blender"]["sensor_h"]
        cd.clip_start = 0.01 * studio["distance"]
        cd.clip_end = 10 * studio["distance"]
        co = bpy.data.objects.new("cam_" + c["name"], cd)
        bpy.context.scene.collection.objects.link(co)
        co.location = c["blender"]["loc"]
        co.rotation_mode = "QUATERNION"
        co.rotation_quaternion = c["blender"]["quat"]
        objs.append(co)
    return objs


def project(cam, P):
    """World points (N,3) -> (N,3) [u, v, z] in panel pixels."""
    K, R, t = np.array(cam["K"]), np.array(cam["R"]), np.array(cam["t"])
    X = P @ R.T + t
    uv = X @ K.T
    return np.concatenate([uv[:, :2] / uv[:, 2:3], X[:, 2:3]], 1)


def setup_render(studio, samples=16, blur=False, shutter=1.0):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.eevee.taa_render_samples = samples
    sc.eevee.use_gtao = True
    sc.eevee.gtao_distance = 0.1 * studio["distance"]
    sc.eevee.use_soft_shadows = True
    sc.eevee.shadow_cube_size = "1024"
    sc.eevee.shadow_cascade_size = "2048"
    sc.eevee.use_motion_blur = bool(blur)
    sc.eevee.motion_blur_shutter = shutter
    sc.eevee.motion_blur_position = "CENTER"
    sc.eevee.motion_blur_steps = 2
    sc.render.film_transparent = True
    sc.render.resolution_x = studio["panel_w"]
    sc.render.resolution_y = studio["panel_h"]
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    if sc.world is None:
        sc.world = bpy.data.worlds.new("v2r_world")
    sc.world.use_nodes = True
    return sc


def setup_lights(studio, rng=None, strength=None):
    """Studio lighting.  rng=None -> fixed nice lighting (reference / exam)."""
    for o in list(bpy.data.objects):
        if o.type == "LIGHT":
            bpy.data.objects.remove(o, do_unlink=True)
    sc = bpy.context.scene
    bg = sc.world.node_tree.nodes["Background"]
    if rng is None:
        az, el, s, amb, tint = 35.0, 50.0, 3.2, 0.9, (1.0, 0.98, 0.95)
    else:
        az, el = rng.uniform(-180, 180), rng.uniform(20, 80)
        s, amb = rng.uniform(1.5, 5.0), rng.uniform(0.4, 1.3)
        tint = (rng.uniform(0.85, 1.0), rng.uniform(0.85, 1.0), rng.uniform(0.85, 1.0))
    bg.inputs["Color"].default_value = (*tint, 1)
    bg.inputs["Strength"].default_value = amb
    ld = bpy.data.lights.new("v2r_sun", "SUN")
    ld.energy = s
    ld.angle = math.radians(8)
    lo = bpy.data.objects.new("v2r_sun", ld)
    sc.collection.objects.link(lo)
    lo.rotation_euler = Euler((math.radians(90 - el), 0, math.radians(az)), "XYZ")
    ld2 = bpy.data.lights.new("v2r_fill", "SUN")
    ld2.energy = 0.35 * s
    lo2 = bpy.data.objects.new("v2r_fill", ld2)
    sc.collection.objects.link(lo2)
    lo2.rotation_euler = Euler((math.radians(60), 0, math.radians(az + 160)), "XYZ")


def render_to(path, cam_obj):
    sc = bpy.context.scene
    sc.camera = cam_obj
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
