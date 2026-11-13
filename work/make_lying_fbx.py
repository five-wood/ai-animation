"""Make a deliberately mis-oriented copy of a rigged FBX (test data for the web orientation tool).
blender -b --factory-startup --python make_lying_fbx.py -- <in.fbx> <out.fbx>
Rotates the whole model by Rx(-90) then Rz(+90) and applies the rotation to the armature and meshes
(bake into the data), so the exported file lies on its back with its head toward Blender -Y.
"""
import math
import sys

import bpy
from mathutils import Matrix

src, dst = sys.argv[sys.argv.index('--') + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src, use_anim=False)
R = Matrix.Rotation(math.radians(90), 4, 'Z') @ Matrix.Rotation(math.radians(-90), 4, 'X')
tops = [o for o in bpy.context.scene.objects if o.parent is None]
for o in tops:
    o.matrix_world = R @ o.matrix_world
bpy.context.view_layer.update()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
bpy.ops.export_scene.fbx(filepath=dst, use_selection=False, add_leaf_bones=False, bake_anim=False,
                         path_mode='COPY', embed_textures=True, axis_forward='-Z', axis_up='Y')
pts = []
for o in bpy.context.scene.objects:
    if o.type == 'MESH':
        for c in o.bound_box:
            pts.append(o.matrix_world @ __import__('mathutils').Vector(c))
lo = [min(p[i] for p in pts) for i in range(3)]
hi = [max(p[i] for p in pts) for i in range(3)]
print('[v2r] lying copy extent', [round(hi[i] - lo[i], 3) for i in range(3)], '->', dst)
