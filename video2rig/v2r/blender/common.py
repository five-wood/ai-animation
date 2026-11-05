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
# Rotation that brings the model's "up" axis (head direction after import) onto Blender +Z.
UP_ROTATIONS = {
    "+Z": None,
    "-Z": ("X", 180.0),
    "+Y": ("X", 90.0),
    "-Y": ("X", -90.0),
    "+X": ("Y", -90.0),
    "-X": ("Y", 90.0),
}


def up_matrix(up="+Z"):
    if up not in UP_ROTATIONS:
        raise SystemExit(f"unknown up axis: {up!r} (use one of {', '.join(UP_ROTATIONS)})")
    r = UP_ROTATIONS[up]
    return Matrix.Identity(4) if r is None else Matrix.Rotation(math.radians(r[1]), 4, r[0])


def _axis_rotations():
    """The 24 axis-aligned proper rotations (3x3 numpy)."""
    out = []
    for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        for sx in (1, -1):
            for sy in (1, -1):
                for sz in (1, -1):
                    R = np.zeros((3, 3))
                    R[0, perm[0]], R[1, perm[1]], R[2, perm[2]] = sx, sy, sz
                    if np.linalg.det(R) > 0:
                        out.append(R)
    return out


def _skin_fit(arm, o, R, origin, max_samples=3000):
    """Mean distance of strongly weighted vertices to their own bone segment (rest pose,
    world space) if the mesh object were rotated by R about `origin`."""
    A = mat_np(arm.matrix_world)
    M = mat_np(o.matrix_world)
    gname = {g.index: g.name for g in o.vertex_groups}
    bones = arm.data.bones
    vs = o.data.vertices
    step = max(1, len(vs) // max_samples)
    d = []
    for i in range(0, len(vs), step):
        v = vs[i]
        if not v.groups:
            continue
        g = max(v.groups, key=lambda g: g.weight)
        n = gname.get(g.group)
        if g.weight < 0.6 or n not in bones:
            continue
        p = M[:3, :3] @ np.array(v.co) + M[:3, 3]
        p = R @ (p - origin) + origin
        b = bones[n]
        h = A[:3, :3] @ np.array(b.head_local) + A[:3, 3]
        t = A[:3, :3] @ np.array(b.tail_local) + A[:3, 3]
        s = t - h
        u = float(np.clip((p - h) @ s / max(float(s @ s), 1e-12), 0, 1))
        d.append(np.linalg.norm(p - (h + u * s)))
    return float(np.mean(d)) if d else None


def rebind_bone_parented_skins(arm, meshes):
    """Some FBX exports (3ds Max / game rigs) come in with the skinned body parented
    rigidly to a bone and WITHOUT an Armature modifier: the vertex weights exist but
    nothing deforms, and the mesh can also land rotated against the skeleton.
    For every such mesh: pick the axis rotation that puts its weighted vertices on
    their own bones, then re-parent it to the armature object with an Armature modifier."""
    bone_names = set(arm.data.bones.keys())
    origin = mat_np(arm.matrix_world)[:3, 3]
    bpy.context.view_layer.update()
    for o in meshes:
        if o.type != "MESH" or any(m.type == "ARMATURE" for m in o.modifiers):
            continue
        if not (o.parent == arm and o.parent_type == "BONE"):
            continue
        if not any(g.name in bone_names for g in o.vertex_groups):
            continue  # rigid prop hung on a bone: keep as is
        cur = _skin_fit(arm, o, np.eye(3), origin)
        if cur is None:
            continue
        best_R, best = np.eye(3), cur
        for R in _axis_rotations():
            f = _skin_fit(arm, o, R, origin)
            if f is not None and f < best - 1e-9:
                best_R, best = R, f
        mw = o.matrix_world.copy()
        if best < 0.5 * cur:
            Rm = Matrix.Identity(4)
            for i in range(3):
                for j in range(3):
                    Rm[i][j] = float(best_R[i, j])
            Tm = Matrix.Translation(Vector(origin.tolist()))
            mw = Tm @ Rm @ Tm.inverted() @ mw
            note = f"rotated to skeleton (vertex-to-bone {cur:.3f} -> {best:.3f})"
        else:
            note = f"orientation kept (vertex-to-bone {cur:.3f})"
        o.parent = arm
        o.parent_type = "OBJECT"
        o.parent_bone = ""
        o.matrix_parent_inverse = Matrix.Identity(4)
        o.matrix_world = mw
        mod = o.modifiers.new("v2r_armature", "ARMATURE")
        mod.object = arm
        mod.use_vertex_groups = True
        bpy.context.view_layer.update()
        print(f"[v2r] rebind {o.name}: was bone-parented without skinning; {note}; armature modifier added")


def prop_meshes(arm, meshes):
    """Meshes of a separate prop rig (m11101's two guns): every bone that carries the mesh - its weighted
    vertex groups, or the bone it is rigidly parented to - hangs under a root bone other than the main
    skeleton's root (the root with the most bones).  A prop skinned to the creature's own hand belongs to the
    body chain and is not detected."""
    bones = arm.data.bones

    def top(b):
        while b.parent is not None:
            b = b.parent
        return b.name

    roots = [b for b in bones if b.parent is None]
    main = max(roots, key=lambda r: len(r.children_recursive)).name
    out = []
    for m in meshes:
        used = set()
        if m.parent_type == "BONE" and m.parent_bone in bones:
            used.add(m.parent_bone)
        gname = {g.index: g.name for g in m.vertex_groups}
        for v in m.data.vertices:
            for g in v.groups:
                n = gname.get(g.group)
                if g.weight > 1e-4 and n in bones:
                    used.add(n)
        if used and all(top(bones[n]) != main for n in used):
            out.append(m)
    return out


def load_model(path, procedural_material=None, up="+Z", drop_props=False):
    """Import FBX/GLB/BLEND, keep only the main armature and its meshes, rest pose.

    up: model axis that points up after import.  Anything other than +Z rotates the
    top-level objects (object transform only; bone data is untouched) so the creature
    stands on the Blender ground plane.  Every script that reloads the model for a
    project must pass the same value (stored in prepare_opts.json).

    drop_props: also delete the prop meshes found by prop_meshes() (a gun with its own root bone): they are
    not rendered, carry no keypoints and are not exported.  Every bone stays (the exported skeleton is
    unchanged).  Like `up`, every script must pass the project's value (prepare_opts.json)."""
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
    rebind_bone_parented_skins(arm, meshes)
    if drop_props:  # after the rebind: a rebound skin is judged by its vertex weights, not its old parent bone
        props = prop_meshes(arm, meshes)
        if props:
            print(f"[v2r] props dropped (own root bone, not part of the creature): "
                  f"{', '.join(sorted(o.name for o in props))}")
        meshes = [o for o in meshes if o not in props]
        if not meshes:
            raise SystemExit("NO_SKINNED_MESH: every mesh bound to the armature is a prop")
        for o in props:
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
    R = up_matrix(up)
    if up != "+Z":
        bpy.context.view_layer.update()
        keep = set([arm] + meshes)
        for o in [arm] + meshes:
            if o.parent is None or o.parent not in keep:
                o.matrix_world = R @ o.matrix_world
        print(f"[v2r] up={up}: model rotated so that {up} points to +Z")
    ensure_materials(meshes, procedural_material)
    bpy.context.view_layer.update()
    return arm, meshes


def check_world_matrix(arm, rig):
    """The reloaded armature must sit exactly where prepare put it (same --up)."""
    W = mat_np(arm.matrix_world)
    ref = np.array(rig["world_matrix"])
    if not np.allclose(W, ref, atol=1e-4 * max(1.0, float(np.abs(ref).max()))):
        raise SystemExit("WORLD_MATRIX_MISMATCH: reloaded model does not match rig.json "
                         "(prepare_opts.json 'up' changed?) - rerun prepare")


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


def rigid_accessories(meshes, brec, min_verts=50, max_blend=0.02, min_share=0.05):
    """Active bones whose skin is (almost) not blended with any other active carrier.

    Every vertex weight is assigned to its carrier: the nearest active bone at or above the
    weighted bone.  A real joint has a band of vertices shared between its carrier and a
    neighbour; a rigid attachment has none.  Returns [(bone index, blend fraction, verts)]."""
    active = {i for i, b in enumerate(brec) if b["active"]}
    by_name = {b["name"]: i for i, b in enumerate(brec)}

    def carrier(i):
        while i >= 0 and i not in active:
            i = brec[i]["parent"]
        return i

    stats = {i: [0, 0] for i in active}
    for m in meshes:
        gname = {g.index: g.name for g in m.vertex_groups}
        for v in m.data.vertices:
            ws = {}
            for g in v.groups:
                bi = by_name.get(gname.get(g.group))
                if bi is None or g.weight <= 1e-3:
                    continue
                c = carrier(bi)
                if c >= 0:
                    ws[c] = ws.get(c, 0.0) + g.weight
            tot = sum(ws.values())
            if tot <= 0:
                continue
            share = {c: w / tot for c, w in ws.items() if w / tot >= min_share}
            for c in share:
                if c in stats:
                    stats[c][0] += 1
                    stats[c][1] += len(share) > 1
    out = []
    for i, (n, nb) in stats.items():
        if brec[i]["parent"] >= 0 and n >= min_verts and nb / n < max_blend:
            out.append((i, nb / n, n))
    return out


def default_limits(side, is_root):
    if is_root:
        return [[-180, 180], [-180, 180], [-180, 180]]
    if side == "M":
        return [[-35, 35], [-20, 20], [-30, 30]]
    return [[-75, 75], [-30, 30], [-50, 50]]


# ----------------------------------------------------------------------------- anatomical limits
# Ranges in degrees, measured in the joint's anatomical frame (not the bone's Euler axes):
#   twist = rotation about the limb axis,
#   flex  = main bending plane, (lo, hi, direction the child moves for positive flex),
#   side  = the remaining bending axis (abduction / lateral bend), same format.
# direction: "fwd" | "back" | "up" | "down" | "out" (away from the body midline) | None (symmetric,
# no known sign).  "hinge" joints use the bend already present in the rest pose when there is one.
# Spine / neck values are TOTALS for the whole section and are shared out over its segments.
# Sources: human ranges of motion (AAOS / standard goniometry tables) for upright bodies and
# veterinary canine/feline goniometry for quadrupeds, rounded and kept on the conservative side.
ANATOMY = {
    "biped": {
        "spine":    dict(twist=(-45, 45), flex=(-30, 80, "fwd"), side=(-35, 35, None), total=True),
        "neck":     dict(twist=(-70, 70), flex=(-50, 50, "fwd"), side=(-40, 40, None), total=True),
        "head":     dict(twist=(-15, 15), flex=(-25, 25, "fwd"), side=(-15, 15, None)),
        "head_only": dict(twist=(-70, 70), flex=(-60, 60, "fwd"), side=(-40, 40, None)),
        "clavicle": dict(twist=(-10, 10), flex=(-10, 35, "up"), side=(-20, 25, "fwd")),
        "shoulder": dict(twist=(-45, 45), flex="shoulder_elevation", side=(-40, 100, "fwd")),
        "elbow":    dict(twist=(-45, 45), flex=(-5, 145, "fwd"), side=(-10, 10, None), hinge=True),
        "wrist":    dict(twist=(-10, 10), flex=(-70, 70, None), side=(-25, 25, None)),
        "finger":   dict(twist=(-5, 5), flex=(-40, 40, None), side=(-15, 15, None)),
        "hip":      dict(twist=(-35, 35), flex=(-25, 110, "fwd"), side=(-20, 45, "out")),
        "knee":     dict(twist=(-10, 10), flex=(-5, 140, "back"), side=(-5, 5, None), hinge=True),
        "ankle":    dict(twist=(-10, 10), flex=(-25, 45, "down"), side=(-20, 20, None)),
        "toe":      dict(twist=(-5, 5), flex=(-40, 40, None), side=(-10, 10, None)),
    },
    "quadruped": {
        "spine":    dict(twist=(-20, 20), flex=(-30, 30, "up"), side=(-40, 40, None), total=True),
        "neck":     dict(twist=(-40, 40), flex=(-45, 45, "up"), side=(-50, 50, None), total=True),
        "head":     dict(twist=(-15, 15), flex=(-30, 30, "up"), side=(-20, 20, None)),
        "head_only": dict(twist=(-40, 40), flex=(-60, 60, "up"), side=(-50, 50, None)),
        "clavicle": dict(twist=(-10, 10), flex=(-15, 15, "fwd"), side=(-10, 10, None)),
        "shoulder": dict(twist=(-20, 20), flex=(-50, 70, "fwd"), side=(-15, 30, "out")),
        "elbow":    dict(twist=(-15, 15), flex=(-5, 130, "fwd"), side=(-5, 5, None), hinge=True),
        "wrist":    dict(twist=(-10, 10), flex=(-10, 120, "back"), side=(-10, 10, None), hinge=True),
        "finger":   dict(twist=(-5, 5), flex=(-40, 40, None), side=(-10, 10, None)),
        "hip":      dict(twist=(-20, 20), flex=(-40, 70, "fwd"), side=(-15, 35, "out")),
        "knee":     dict(twist=(-10, 10), flex=(-5, 120, "back"), side=(-5, 5, None), hinge=True),
        "ankle":    dict(twist=(-10, 10), flex=(-5, 120, "fwd"), side=(-5, 5, None), hinge=True),
        "toe":      dict(twist=(-5, 5), flex=(-40, 40, None), side=(-10, 10, None)),
    },
    "common": {
        "tail":     dict(twist=(-20, 20), flex=(-60, 60, "up"), side=(-90, 90, None), total=True, seg_max=40),
        "ear":      dict(twist=(-15, 15), flex=(-60, 60, "fwd"), side=(-40, 40, None), total=True, seg_max=40),
        "head_part": dict(twist=(-5, 5), flex=(-20, 20, None), side=(-15, 15, None)),
        "helper":   dict(twist=(-10, 10), flex=(-15, 15, None), side=(-15, 15, None)),
        "generic":  dict(twist=(-10, 10), flex=(-30, 30, None), side=(-20, 20, None)),
    },
}

_NAME_HINTS = [
    ("tail", r"tail"),
    ("ear", r"(^|[^a-z])ear|antenna"),
    ("front", r"arm|hand|clav|collar|scapula|elbow|wrist|finger|thumb|shoulder|wing"),
    ("hind", r"leg|thigh|calf|knee|shin|foot|toe|ankle|hip|hock|pelvis_?(l|r)"),
]


def _unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v * 0.0


def anatomical_limits(brec, kps, W, size, center, fwd, lateral, up=(0.0, 0.0, 1.0)):
    """Classify every active bone into a joint type and give it an anatomical range.

    Returns {bone_index: (joint_type, limits[[x0,x1],[y0,y1],[z0,z1]], note)}.
    Classification is geometric (body plan, chains off the midline, position along each
    chain, joints sharing a pivot) with bone names only as hints, so anonymous rigs work."""
    up = np.asarray(up, float)
    fwd = _unit(fwd)
    lateral = _unit(lateral)
    nb = len(brec)
    act = [i for i, b in enumerate(brec) if b["active"]]
    aset = set(act)
    pos = {i: np.array(brec[i]["head_world"]) for i in range(nb)}
    # nearest active ancestor = the joint this bone hangs from
    jpar = {}
    for i in act:
        p = brec[i]["parent"]
        while p >= 0 and p not in aset:
            p = brec[p]["parent"]
        jpar[i] = p if p >= 0 else None
    jkids = {i: [j for j in act if jpar[j] == i] for i in act}
    # world-space rest frames and limb directions
    frames = {}
    for i in act:
        M = W @ np.array(brec[i]["rest"])
        frames[i] = np.stack([_unit(M[:3, k]) for k in range(3)], 1)
    kp_head = {}
    for k in kps:
        if k["type"] == "head":
            kp_head.setdefault(k["bone"], []).append(np.array(k["rest_world"]))
    kp_tail = {k["bone"]: np.array(k["rest_world"]) for k in kps if k["type"] == "tail"}
    children = [[] for _ in range(nb)]
    for i, b in enumerate(brec):
        if b["parent"] >= 0:
            children[b["parent"]].append(i)

    def limb_dir(i):
        level = list(children[i])
        while level:
            hit = [p for j in level for p in kp_head.get(j, [])]
            if hit:
                d = np.mean(hit, 0) - pos[i]
                if np.linalg.norm(d) > 1e-4 * size:
                    return _unit(d)
            level = [c for j in level for c in children[j]]
        if i in kp_tail:
            return _unit(kp_tail[i] - pos[i])
        return _unit(frames[i][:, 1])

    ldir = {i: limb_dir(i) for i in act}
    seg_len = {}
    for i in act:
        ks = jkids[i]
        seg_len[i] = float(np.mean([np.linalg.norm(pos[j] - pos[i]) for j in ks])) if ks else \
            float(np.linalg.norm(np.array(brec[i]["tail_world"]) - pos[i]))
    helper = {i for i in act if jpar[i] is not None and np.linalg.norm(pos[i] - pos[jpar[i]]) < 0.01 * size}
    names = {i: brec[i]["name"].lower() for i in act}

    def hint(i):
        for tag, pat in _NAME_HINTS:
            if re.search(pat, names[i]):
                return tag
        return None

    # ---- midline: main chain from the body root to the head
    mid = [i for i in act if brec[i]["side"] == "M" and i not in helper]
    heads = [i for i in mid if re.search(r"head", names[i]) and not re.search(r"end|top|nub", names[i])]

    def depth(i):
        d = 0
        while jpar.get(i) is not None:
            i, d = jpar[i], d + 1
        return d

    c = np.asarray(center, float)
    head = min(heads, key=depth) if heads else None
    if head is None and mid:
        leaves = [i for i in mid if not [j for j in jkids[i] if j in mid]]
        head = max(leaves, key=lambda i: float(pos[i] @ (up + fwd))) if leaves else None
    main = []
    i = head
    while i is not None and brec[i]["side"] == "M":
        main.append(i)
        i = jpar[i]
    main = main[::-1]
    start = main[0] if main else None
    axis = _unit(pos[head] - pos[start]) if head is not None and start is not None and head != start else up
    plan = "biped" if float(axis @ up) > 0.6 else "quadruped"
    span = max(float(np.linalg.norm(pos[head] - pos[start])) if main else size, 1e-6)

    def along(i):
        return float((pos[i] - (pos[start] if start is not None else c)) @ axis) / span

    out = {}
    head_desc = set()
    if head is not None:
        stack = list(jkids[head])
        while stack:
            j = stack.pop()
            head_desc.add(j)
            stack += jkids[j]
    # side chains: roots are active non-midline bones hanging from the midline (or from nothing)
    roots = [i for i in act if brec[i]["side"] != "M" and i not in helper
             and (jpar[i] is None or brec[jpar[i]]["side"] == "M")]
    front_attach = set()
    for r in roots:
        chain_bones = []
        level = [r]
        while level:
            chain_bones.append([j for j in level if j not in helper])
            nxt = []
            for j in level:
                nxt += [k for k in jkids[j] if brec[k]["side"] == brec[r]["side"]]
            level = nxt
        chain_bones = [lv for lv in chain_bones if lv]
        flat = [j for lv in chain_bones for j in lv]
        tags = [t for t in (hint(j) for j in flat) if t]
        if r in head_desc or (jpar[r] is not None and jpar[r] == head):
            fam = "ear"
        elif tags:
            fam = max(set(tags), key=tags.count)
        else:
            a = along(jpar[r] if jpar[r] is not None else r)
            fam = "front" if a > 0.35 else "hind"
        if fam == "front" and jpar[r] is not None:
            front_attach.add(jpar[r])
        n = len(chain_bones)
        if fam == "front":
            l1 = seg_len[chain_bones[0][0]]
            l2 = seg_len[chain_bones[1][0]] if n > 1 else 0
            has_clav = (re.search(r"clav|collar|scapula", names[chain_bones[0][0]]) is not None
                        or n >= 4 or (n == 3 and l1 < 0.8 * l2))
            seq = (["clavicle"] if has_clav else []) + ["shoulder", "elbow", "wrist"]
        elif fam == "hind":
            seq = ["hip", "knee", "ankle"]
        else:
            seq = [fam]
        for lvl, bones_at in enumerate(chain_bones):
            t = seq[lvl] if lvl < len(seq) else ("finger" if fam == "front" else ("toe" if fam == "hind" else seq[-1]))
            for j in bones_at:
                out[j] = t
    # midline types
    neck_named = [i for i in main if re.search(r"neck", names[i])]
    attach_main = [i for i in main if i in front_attach]
    for k, i in enumerate(main):
        if i == head:
            out[i] = "head"
        elif neck_named:
            out[i] = "neck" if i in neck_named else "spine"
        elif attach_main and main.index(i) > max(main.index(a) for a in attach_main):
            out[i] = "neck"
        else:
            out[i] = "spine"
    for i in mid:
        if i in out:
            continue
        if i in head_desc:
            out[i] = "head_part"
        elif re.search(r"tail", names[i]) or float((pos[i] - c) @ fwd) < -0.05 * size or \
                (plan == "quadruped" and along(i) < 0):
            out[i] = "tail"
        else:
            out[i] = "generic"
    for i in act:
        if i in helper:
            out[i] = "helper"
        out.setdefault(i, "generic")
    # tail segments that hang off a tail stay tail
    for i in act:
        if out[i] == "generic" and jpar[i] is not None and out.get(jpar[i]) == "tail":
            out[i] = "tail"

    n_neck = sum(1 for i in main if out[i] == "neck")
    has_neck = n_neck > 0
    # "total" ranges are shared by the segments of one section: connected bones of the same type
    comp = {i: i for i in act}

    def find(a):
        while comp[a] != a:
            comp[a] = comp[comp[a]]
            a = comp[a]
        return a

    for i in act:
        p = jpar[i]
        if p is not None and out[i] == out[p] and out[i] in ("spine", "neck", "tail", "ear"):
            comp[find(i)] = find(p)
    sec_n = {}
    for i in act:
        sec_n[find(i)] = sec_n.get(find(i), 0) + 1

    def world_dir(key, i):
        if key is None:
            return None
        if key == "fwd":
            return fwd
        if key == "back":
            return -fwd
        if key == "up":
            return up
        if key == "down":
            return -up
        if key == "out":
            s = float((pos[i] - c) @ lateral)
            return lateral * (1.0 if s >= 0 else -1.0)
        raise ValueError(key)

    result = {}
    for i in act:
        t = out[i]
        table = ANATOMY["common"] if t in ANATOMY["common"] else ANATOMY[plan]
        key = "head_only" if (t == "head" and not has_neck) else t
        spec = table[key]
        d = ldir[i]
        note = []
        div = sec_n[find(i)] if spec.get("total") else 1
        if div > 1:
            note.append(f"1/{div} of the {t} total")
        tw = [v / div for v in spec["twist"]]
        if spec["flex"] == "shoulder_elevation":
            # humanlike arm: down to the body, up to overhead, measured from the rest pose angle
            a = float(np.degrees(np.arccos(np.clip(d @ (-up), -1, 1))))  # 0 = hanging straight down
            fl = (-max(a - 10.0, 5.0), max(170.0 - a, 20.0), "up")
            note.append(f"arm {a:.0f}deg from down")
        else:
            fl = spec["flex"]
        cap = spec.get("seg_max", 1e9)
        clip = lambda v: float(np.clip(v, -cap, cap))
        fl = (clip(fl[0] / div), clip(fl[1] / div), fl[2])
        sd = spec["side"]
        sd = (clip(sd[0] / div), clip(sd[1] / div), sd[2])
        # flex axis in world
        p_axis, lo, hi = None, fl[0], fl[1]
        if spec.get("hinge") and jpar[i] is not None and jpar[i] in ldir:
            dp = ldir[jpar[i]]
            bend = float(np.degrees(np.arccos(np.clip(dp @ d, -1, 1))))
            if bend >= 10.0:
                p_axis = _unit(np.cross(dp, d))
                lo, hi = fl[0] - bend, fl[1] - bend
                note.append(f"rest bend {bend:.0f}deg")
        if p_axis is None:
            fd = world_dir(fl[2], i)
            if fd is not None and np.linalg.norm(np.cross(d, fd)) > 0.3:
                p_axis = _unit(np.cross(d, fd))
            else:
                # sign unknown -> symmetric range of the same total size
                h = 0.5 * (fl[1] - fl[0])
                lo, hi = -h, h
                if fl[2] is not None:
                    note.append("flex direction unclear: symmetric")
        # local Euler axes: twist = the one along the limb, flex = the one along p_axis
        F = frames[i]
        k_tw = int(np.argmax(np.abs(F.T @ d)))
        rest = [k for k in range(3) if k != k_tw]
        if p_axis is not None:
            k_fl = max(rest, key=lambda k: abs(F[:, k] @ p_axis))
        else:
            ref = _unit(np.cross(d, up)) if np.linalg.norm(np.cross(d, up)) > 0.3 else _unit(np.cross(d, fwd))
            k_fl = max(rest, key=lambda k: abs(F[:, k] @ ref))
        k_sd = [k for k in rest if k != k_fl][0]
        if abs(F[:, k_tw] @ d) < 0.8:
            note.append(f"limb axis off local axes ({abs(F[:, k_tw] @ d):.2f})")
        lim = [[0.0, 0.0] for _ in range(3)]
        lim[k_tw] = [tw[0], tw[1]]
        if p_axis is not None and F[:, k_fl] @ p_axis < 0:
            lo, hi = -hi, -lo
        lim[k_fl] = [lo, hi]
        slo, shi = sd[0], sd[1]
        sdir = world_dir(sd[2], i)
        if sdir is not None and np.linalg.norm(np.cross(d, sdir)) > 0.3:
            q = _unit(np.cross(d, sdir))
            if F[:, k_sd] @ q < 0:
                slo, shi = -shi, -slo
        elif sdir is not None:
            h = 0.5 * (shi - slo)
            slo, shi = -h, h
        lim[k_sd] = [slo, shi]
        # the rest pose (0) must stay strictly inside every range (solver starts there)
        lim = [[round(float(min(a, -2.0)), 1), round(float(max(b, 2.0)), 1)] for a, b in lim]
        result[i] = (t, lim, f"{plan}; twist={'XYZ'[k_tw]} flex={'XYZ'[k_fl]}" + (("; " + ", ".join(note)) if note else ""))
    return result


def _skin_of_subtree(meshes, bones, root_idx, index, min_w=0.5):
    """World positions of vertices whose strongest weight belongs to bone root_idx or a descendant."""
    names = set()
    stack = [bones[root_idx]]
    while stack:
        b = stack.pop()
        names.add(b.name)
        stack += list(b.children)
    pts = []
    dg = bpy.context.evaluated_depsgraph_get()
    for m in meshes:
        gname = {g.index: g.name for g in m.vertex_groups}
        M = mat_np(m.matrix_world)
        ev = m.evaluated_get(dg)
        me = ev.to_mesh()
        for v_src, v_ev in zip(m.data.vertices, me.vertices):
            if not v_src.groups:
                continue
            g = max(v_src.groups, key=lambda g: g.weight)
            if g.weight >= min_w and gname.get(g.group) in names:
                pts.append(M[:3, :3] @ np.array(v_ev.co) + M[:3, 3])
        ev.to_mesh_clear()
    return np.array(pts) if pts else np.zeros((0, 3))


def _extra_keypoints(kps, bones, index, meshes, W, size, center, lateral, fwd, fist_points=False,
                     offaxis_points=False, spread_min=0.35, max_side_frac=0.25):
    """fist_points: a hand whose finger keypoints all lie close to it (a fist, a paw) keeps only its own
    head keypoint plus one knuckle point: the finger points on a closed fist look alike and are read
    interchangeably.  offaxis_points: on the hand-like ends of the limbs and on the head, add two skin
    points off the bone's line where the skin spreads widest, so the bone's turn about its own axis is
    visible; each is stored as an offset in the bone's local frame (type "offaxis")."""
    Winv = np.linalg.inv(W)
    by_bone = {}
    for n, k in enumerate(kps):
        by_bone.setdefault(k["bone"], []).append(n)
    parent = {i: (index[b.parent.name] if b.parent else -1) for i, b in enumerate(bones)}

    def ancestors(i):
        out = []
        p = parent[i]
        while p >= 0:
            out.append(p)
            p = parent[p]
        return out

    hand_re = re.compile(r"(?i)(hand|wrist|paw|fist)")
    head_re = re.compile(r"(?i)(^|[^a-z])head($|[^a-z])")
    hands = [i for i, b in enumerate(bones) if hand_re.search(b.name) and i in by_bone
             and any(kps[n]["type"] == "head" for n in by_bone[i])]
    # keep only the topmost hand bone of a chain (Bip001 L Hand, not Bip001_L_Hand_Bone below it)
    hands = [i for i in hands if not any(a in hands for a in ancestors(i))]
    drop = set()
    new = []
    if fist_points:
        for h in hands:
            hp = np.array(bones[h].head_local)
            desc = [n for n, k in enumerate(kps) if k["bone"] != h and h in ancestors(k["bone"])]
            if len(desc) < 2:
                continue
            P = np.array([kps[n]["rest_world"] for n in desc])
            hw = W[:3, :3] @ hp + W[:3, 3]
            reach = float(np.max(np.linalg.norm(P - hw, axis=1)))
            if reach > max_side_frac * size:
                continue  # long open fingers: keep them
            # one knuckle point: the descendant keypoint farthest from the wrist
            far = desc[int(np.argmax(np.linalg.norm(P - hw, axis=1)))]
            for n in desc:
                if n != far:
                    drop.add(n)
            print(f"[v2r] fist: {bones[h].name} keeps its head + {kps[far]['name']}, drops {len(desc) - 1} finger points")
    if offaxis_points:
        heads = [i for i, b in enumerate(bones) if head_re.search(b.name) and i in by_bone]
        heads = [i for i in heads if not any(a in heads for a in ancestors(i))]
        for i in hands + heads:
            b = bones[i]
            X = _skin_of_subtree(meshes, bones, i, index)
            if len(X) < 30:
                continue
            Mb = W @ mat_np(b.matrix_local)  # bone frame in world
            R, o = Mb[:3, :3] / np.linalg.norm(Mb[:3, 0]), Mb[:3, 3]
            u = R[:, 1]  # Blender bone +Y; for a 3ds Max rig the limb axis may differ, so measure the spread
            # on the plane across the bone's real limb direction: from its head toward the skin's centroid
            d = X.mean(0) - o
            if np.linalg.norm(d) > 1e-6 * size:
                u = d / np.linalg.norm(d)
            perp = (X - o) - np.outer((X - o) @ u, u)
            w_, E = np.linalg.eigh(np.cov(perp.T))
            e = E[:, -1]
            e = e - (e @ u) * u
            if np.linalg.norm(e) < 1e-9:
                continue
            e /= np.linalg.norm(e)
            s = perp @ e
            span = float(np.percentile(s, 99) - np.percentile(s, 1))
            along = float(np.percentile(np.abs((X - o) @ u), 95))
            if span < spread_min * max(along, 0.02 * size):
                continue
            # a consistent sign: outward / forward / up, whichever the spread direction is closest to
            out_dir = lateral * (1.0 if float((o - np.array(center)) @ lateral) >= 0 else -1.0)
            ref = max((out_dir, fwd, np.array([0.0, 0.0, 1.0])), key=lambda r: abs(float(r @ e)))
            if float(ref @ e) < 0:
                e, s = -e, -s
            for tag, val in (("a", np.percentile(s, 99)), ("b", np.percentile(s, 1))):
                v = X[int(np.argmin(np.abs(s - val)))]
                loc = np.linalg.inv(mat_np(b.matrix_local)) @ np.append(Winv[:3, :3] @ v + Winv[:3, 3], 1.0)
                new.append({"name": f"{b.name}.side_{tag}", "bone": i, "type": "offaxis",
                            "local": [float(x) for x in loc[:3]], "rest_world": [float(x) for x in v],
                            "tail_len": 0.0})
            print(f"[v2r] off-axis points on {b.name}: skin spread {span / size:.3f} of size across its axis")
    return [k for n, k in enumerate(kps) if n not in drop] + new


def extract_rig(arm, meshes, facing="-Y", exclude=DEFAULT_EXCLUDE, merge_frac=0.015,
                limits_override=None, min_seg_frac=0.06, keep_bones=None, limits_mode="anatomical",
                fist_points=False, offaxis_points=False):
    """limits_mode: "anatomical" = per joint type ranges (anatomical_limits), "generic" = the
    old fixed +-75/30/50 (limbs) and +-35/20/30 (midline) Euler boxes.  --limits overrides both.
    fist_points / offaxis_points: see _extra_keypoints (off by default: the rig is unchanged)."""
    if limits_mode not in ("anatomical", "generic"):
        raise ValueError(f"unknown limits_mode {limits_mode!r}")
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
    if fist_points or offaxis_points:
        kps = _extra_keypoints(kps, bones, index, meshes, W, size, center, lateral, fwd,
                               fist_points=fist_points, offaxis_points=offaxis_points)
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
        overridden = False
        if limits_override:
            for pat, v in limits_override.items():
                if re.search(pat, b.name):
                    lim = v
                    overridden = True
        brec.append({"name": b.name, "parent": index[b.parent.name] if b.parent else -1,
                     "rest": mat_np(b.matrix_local).tolist(), "length": b.length,
                     "head_world": to_world(b.head_local).tolist(),
                     "tail_world": to_world(b.tail_local).tolist(),
                     "side": side, "limits": lim, "deform": b.name in deform,
                     "limits_source": "override" if overridden else "generic"})
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
        movers = list(anc[bi]) + ([bi] if k["type"] in ("tail", "offaxis") else [])
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
    # helper bones that share their pivot with the joint they hang from (3ds Max "*_Bone" skin
    # bones, twist helpers): two cameras cannot tell their rotation apart from the parent's,
    # so they ride rigidly with it.  --keep-bones keeps them free.
    for m, b in enumerate(brec):
        if not b["active"] or m in force_active:
            continue
        p = b["parent"]
        while p >= 0 and not brec[p]["active"]:
            p = brec[p]["parent"]
        if p >= 0 and np.linalg.norm(np.array(b["head_world"]) - np.array(brec[p]["head_world"])) < 0.01 * size:
            b["active"] = False
            b["folded"] = True
            b["helper_of"] = brec[p]["name"]
            print(f"[v2r] helper bone {b['name']} shares its pivot with {brec[p]['name']}: rides with it")
    # rigid accessories (goggles, helmets, armour plates): the skin a bone carries is not blended
    # with any other bone, so it is a rigid attachment, not a joint.  Its few keypoints sit close
    # to its pivot, so reader noise turns into large spurious rotations; let it ride with its parent
    # and let its keypoints inform the parent instead.  --keep-bones keeps it free.
    for m, frac, n in rigid_accessories(meshes, brec):
        if m in force_active:
            continue
        brec[m]["active"] = False
        brec[m]["folded"] = True
        brec[m]["rigid_accessory"] = True
        print(f"[v2r] rigid accessory {brec[m]['name']}: {n} skinned verts, {frac:.1%} blended with other bones: rides with its parent")
    if limits_mode == "anatomical":
        for bi, (jt, lim, note) in anatomical_limits(brec, kps, W, size, center, fwd, lateral).items():
            brec[bi]["joint"] = jt
            brec[bi]["limits_note"] = note
            if brec[bi]["limits_source"] != "override":
                brec[bi]["limits"] = lim
                brec[bi]["limits_source"] = "anatomical"
    # skeleton edges between keypoints (for drawing): keypoint -> nearest keypoint ancestor
    kp_of_bone_head = {k["bone"]: i for i, k in enumerate(kps) if k["type"] == "head"}
    edges = []
    for i, k in enumerate(kps):
        cand = ([k["bone"]] if k["type"] in ("tail", "offaxis") else []) + anc[k["bone"]]
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
        "n_active": int(sum(b["active"] for b in brec)), "limits_mode": limits_mode,
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
        elif k["type"] == "offaxis":
            p = pb.matrix @ Vector(k["local"])  # bone-local offset riding rigidly on the posed bone
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
    sc.eevee.gtao_distance = (0.015 if studio.get("visual_profile") == "soft-clean" else 0.1) * studio["distance"]
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
    if studio.get("visual_profile", "legacy") == "soft-clean":
        # Multiple broad studio sources retain form shading but avoid competing hard
        # ground shadows. Rendered model has no floor/shadow-catcher in this profile.
        bg.inputs["Color"].default_value = (1, 1, 1, 1)
        bg.inputs["Strength"].default_value = 0.55 if rng is None else rng.uniform(.45, .65)
        center = np.array(studio["target"], float)
        distance = float(studio["distance"])
        directions = [((1., -1., 1.6), 1.0), ((-1., -.4, 1.0), .75), ((0., 1., 1.5), .65)]
        overall = 1.0 if rng is None else rng.uniform(.85, 1.15)
        for i, (offset, ratio) in enumerate(directions):
            ld = bpy.data.lights.new("v2r_soft_" + str(i), "AREA")
            ld.shape = "DISK"
            ld.size = distance * 1.2
            ld.energy = distance ** 2 * 15 * ratio * overall
            ld.use_shadow = True  # self-shadowing remains, broad source makes it soft
            lo = bpy.data.objects.new(ld.name, ld)
            sc.collection.objects.link(lo)
            loc = center + np.array(offset) * distance * .65
            if rng is not None:
                loc += np.array([rng.uniform(-.08,.08) for _ in range(3)]) * distance
            lo.location = loc.tolist()
            lo.rotation_mode = "QUATERNION"
            lo.rotation_quaternion = look_at_quat(loc, center)
        return
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
