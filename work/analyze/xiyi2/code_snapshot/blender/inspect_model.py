"""Quick report on a model before running the pipeline: armature, skinned meshes, textures, size, facing.
blender -b --factory-startup --python inspect_model.py -- <model>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *  # noqa

f = script_args()[0]
ext = os.path.splitext(f)[1].lower()
bpy.ops.wm.read_factory_settings(use_empty=True)
if ext == ".fbx":
    bpy.ops.import_scene.fbx(filepath=f, use_anim=True)
elif ext in (".glb", ".gltf"):
    bpy.ops.import_scene.gltf(filepath=f)
elif ext == ".blend":
    bpy.ops.wm.open_mainfile(filepath=f)
arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
print(f"[v2r] file {f}")
print(f"[v2r] armatures: {[(a.name, len(a.data.bones)) for a in arms]}")
for m in meshes:
    mods = [md.type for md in m.modifiers]
    print(f"[v2r] mesh {m.name}: {len(m.data.vertices)} verts, {len(m.vertex_groups)} vertex groups, modifiers {mods}, "
          f"parent {m.parent.name if m.parent else None}, dims {tuple(round(d, 3) for d in m.dimensions)}")
for i in bpy.data.images:
    print(f"[v2r] image {i.name}: {tuple(i.size)} {'MISSING' if i.size[0] == 0 else 'ok'} ({i.filepath})")
for a in arms:
    act = a.animation_data.action if a.animation_data else None
    print(f"[v2r] {a.name} action: {act.name if act else None} {tuple(act.frame_range) if act else ''}")
    roots = [b.name for b in a.data.bones if b.parent is None]
    print(f"[v2r] roots: {roots}")
if not arms:
    print("[v2r] NO ARMATURE -> rig it first (UniRig, see README)")
elif any(i.size[0] == 0 for i in bpy.data.images):
    print("[v2r] textures missing -> copy them next to the model or use --procedural-material")
