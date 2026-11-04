"""Build a rigged test monster (four-legged lizard with tail) in Blender, skinned with automatic weights.
blender -b --factory-startup --python make_test_creature.py -- <out.blend> [out.fbx]
Facing -Y (Blender front), Z up, ground at z=0.
"""
import bpy
import sys
import math
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
out_blend = argv[0]
out_fbx = argv[1] if len(argv) > 1 else None
bpy.ops.wm.read_factory_settings(use_empty=True)

# joints: name -> (position, parent, skin radius at the joint)
J = {}


def j(name, pos, parent, r):
    J[name] = (Vector(pos), parent, r)


j("hips", (0, 0.30, 0.50), None, 0.16)
j("spine1", (0, 0.05, 0.53), "hips", 0.17)
j("spine2", (0, -0.18, 0.56), "spine1", 0.17)
j("chest", (0, -0.36, 0.58), "spine2", 0.15)
j("neck", (0, -0.50, 0.70), "chest", 0.09)
j("head", (0, -0.64, 0.80), "neck", 0.11)
j("snout", (0, -0.92, 0.74), "head", 0.05)
j("tail1", (0, 0.55, 0.47), "hips", 0.11)
j("tail2", (0, 0.85, 0.40), "tail1", 0.08)
j("tail3", (0, 1.15, 0.33), "tail2", 0.055)
j("tail4", (0, 1.45, 0.27), "tail3", 0.035)
j("tail_end", (0, 1.70, 0.22), "tail4", 0.015)
for s, x in (("L", 1), ("R", -1)):
    j(f"shoulder.{s}", (0.17 * x, -0.33, 0.52), "chest", 0.08)
    j(f"elbow.{s}", (0.27 * x, -0.27, 0.30), f"shoulder.{s}", 0.06)
    j(f"wrist.{s}", (0.27 * x, -0.33, 0.07), f"elbow.{s}", 0.045)
    j(f"hand_end.{s}", (0.29 * x, -0.50, 0.03), f"wrist.{s}", 0.04)
    j(f"hip.{s}", (0.17 * x, 0.32, 0.46), "hips", 0.09)
    j(f"knee.{s}", (0.27 * x, 0.18, 0.28), f"hip.{s}", 0.065)
    j(f"ankle.{s}", (0.27 * x, 0.34, 0.08), f"knee.{s}", 0.045)
    j(f"foot_end.{s}", (0.29 * x, 0.17, 0.03), f"ankle.{s}", 0.04)

# bones: from joint to its first child (names of bones = joint names; leaf joints are ends)
children = {n: [c for c, (_, p, _) in J.items() if p == n] for n in J}
main_child = {"hips": "spine1", "chest": "neck"}
bones = {}
for n, (pos, par, r) in J.items():
    ch = children[n]
    if not ch:
        continue
    c = main_child.get(n, ch[0])
    bones[n] = (pos, J[c][0], par)

# ---------------------------------------------------------------- armature
arm_data = bpy.data.armatures.new("monster_rig")
arm = bpy.data.objects.new("monster_rig", arm_data)
bpy.context.scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
arm.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
eb = {}
for n, (h, t, par) in bones.items():
    b = arm_data.edit_bones.new(n)
    b.head, b.tail = h, t
    b.roll = 0.0
    eb[n] = b
for n, (h, t, par) in bones.items():
    if par is not None:
        eb[n].parent = eb[par]
        eb[n].use_connect = (eb[par].tail - eb[n].head).length < 1e-4
bpy.ops.object.mode_set(mode="OBJECT")

# ---------------------------------------------------------------- body mesh from a skin modifier
names = list(J.keys())
idx = {n: i for i, n in enumerate(names)}
verts = [J[n][0][:] for n in names]
edges = [(idx[n], idx[p]) for n, (_, p, _) in J.items() if p is not None]
me = bpy.data.meshes.new("monster_body")
me.from_pydata(verts, edges, [])
body = bpy.data.objects.new("monster_body", me)
bpy.context.scene.collection.objects.link(body)
sk = body.modifiers.new("skin", "SKIN")
sk.use_smooth_shade = True
for n in names:
    v = me.skin_vertices[0].data[idx[n]]
    r = J[n][2]
    v.radius = (r, r * (0.85 if n in ("hips", "spine1", "spine2", "chest") else 1.0))
    v.use_root = n == "hips"
sub = body.modifiers.new("subd", "SUBSURF")
sub.levels = 2
sub.render_levels = 2
dg = bpy.context.evaluated_depsgraph_get()
ev = body.evaluated_get(dg)
baked = bpy.data.meshes.new_from_object(ev)
body.modifiers.clear()
body.data = baked
for p in body.data.polygons:
    p.use_smooth = True

# ---------------------------------------------------------------- material (scaly, darker back, pale belly)
mat = bpy.data.materials.new("monster_skin")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
tc = nt.nodes.new("ShaderNodeTexCoord")
sep = nt.nodes.new("ShaderNodeSeparateXYZ")
vor = nt.nodes.new("ShaderNodeTexVoronoi")
vor.inputs["Scale"].default_value = 38.0
noi = nt.nodes.new("ShaderNodeTexNoise")
noi.inputs["Scale"].default_value = 4.0
noi.inputs["Detail"].default_value = 6.0
belly = nt.nodes.new("ShaderNodeMapRange")
belly.inputs["From Min"].default_value = 0.25
belly.inputs["From Max"].default_value = 0.6
ramp = nt.nodes.new("ShaderNodeValToRGB")
ramp.color_ramp.elements[0].color = (0.62, 0.52, 0.38, 1)   # pale belly
ramp.color_ramp.elements[1].color = (0.16, 0.20, 0.12, 1)   # dark olive back
mix = nt.nodes.new("ShaderNodeMix")
mix.data_type = "RGBA"
mix.blend_type = "MULTIPLY"
mix.inputs["Factor"].default_value = 0.55
scal = nt.nodes.new("ShaderNodeValToRGB")
scal.color_ramp.elements[0].color = (0.45, 0.45, 0.45, 1)
scal.color_ramp.elements[1].color = (1, 1, 1, 1)
bump = nt.nodes.new("ShaderNodeBump")
bump.inputs["Strength"].default_value = 0.4
nt.links.new(tc.outputs["Object"], sep.inputs["Vector"])
nt.links.new(sep.outputs["Z"], belly.inputs["Value"])
nt.links.new(belly.outputs["Result"], ramp.inputs["Fac"])
nt.links.new(tc.outputs["Object"], vor.inputs["Vector"])
nt.links.new(tc.outputs["Object"], noi.inputs["Vector"])
nt.links.new(vor.outputs["Distance"], scal.inputs["Fac"])
nt.links.new(ramp.outputs["Color"], mix.inputs[6])
nt.links.new(scal.outputs["Color"], mix.inputs[7])
nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
nt.links.new(vor.outputs["Distance"], bump.inputs["Height"])
nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
bsdf.inputs["Roughness"].default_value = 0.6
body.data.materials.append(mat)
# eyes (dark spheres parented to the head bone through weights below)
for x in (0.07, -0.07):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.028, location=(x, -0.74, 0.85), segments=16, ring_count=8)
    e = bpy.context.active_object
    em = bpy.data.materials.new("eye")
    em.diffuse_color = (0.02, 0.02, 0.02, 1)
    em.use_nodes = True
    em.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.9, 0.55, 0.05, 1)
    e.data.materials.append(em)
    e.select_set(True)
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()

# ---------------------------------------------------------------- skin to the armature (automatic weights)
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.parent_set(type="ARMATURE_AUTO")
n_groups = len(body.vertex_groups)
empty = [g.name for g in body.vertex_groups
         if not any(any(gg.group == g.index and gg.weight > 0.01 for gg in v.groups) for v in body.data.vertices)]
print(f"[v2r] creature: {len(arm_data.bones)} bones, {len(body.data.vertices)} verts, {n_groups} groups, "
      f"empty groups: {empty}")
bpy.ops.wm.save_as_mainfile(filepath=out_blend)
if out_fbx:
    bpy.ops.export_scene.fbx(filepath=out_fbx, add_leaf_bones=False, bake_anim=False)
print("[v2r] saved", out_blend, out_fbx)
