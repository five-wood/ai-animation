import os, sys
sys.path.insert(0, "/data/video2rig/code/v2r/blender")
from common import *
rig = load_json("/data/video2rig/projects/dog/rig.json")
studio = load_json("/data/video2rig/projects/dog/studio.json")
arm, meshes = load_model(rig["model"], procedural_material=True)
cams = build_cameras(studio)
sc = setup_render(studio, samples=16)
setup_lights(studio)
for m in meshes:
    print("mesh", m.name, "smooth", m.data.polygons[0].use_smooth, "normals_split", m.data.has_custom_normals)
for variant in ["base", "noshadow", "flipcheck"]:
    if variant == "noshadow":
        for o in bpy.data.objects:
            if o.type == "LIGHT": o.data.use_shadow = False
    if variant == "flipcheck":
        for m in meshes:
            for mat in m.data.materials:
                mat.use_backface_culling = True
    render_to(f"/tmp/lt_{variant}.png", cams[0])
