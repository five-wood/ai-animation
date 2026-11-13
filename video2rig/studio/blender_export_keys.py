"""Export an animation that only keeps the user's keyframes (Blender interpolates between them).

blender -b --factory-startup --python studio/blender_export_keys.py -- <project> <motion.npz> <out_file> <json-options>
  options: {"frames": [0, 11, ...] (video frames, 0-based), "fps": 24, "format": "fbx"|"glb",
  (BVH stores a full pose for every frame by definition, so it has no keys-only variant.)
            "interp": "BEZIER"|"LINEAR", "name": "clip"}
Video frame f becomes scene frame f (time f / fps): the first key is at time 0.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'v2r', 'blender'))
from common import *  # noqa: E402,F401,F403

args = script_args()
project, motion_path, out_file = args[0], args[1], args[2]
opt = json.loads(args[3]) if len(args) > 3 else {}
fmt = opt.get('format', 'fbx')
fps = float(opt.get('fps', 30))
interp = opt.get('interp', 'BEZIER')
name = opt.get('name', 'clip')

rig = load_json(os.path.join(project, 'rig.json'))
popt = load_json(os.path.join(project, 'prepare_opts.json')) if os.path.exists(
    os.path.join(project, 'prepare_opts.json')) else {}
arm, meshes = load_model(rig['model'], procedural_material=True if popt.get('procedural_material') else None,
                         up=popt.get('up', '+Z'), drop_props=bool(popt.get('drop_props')))
check_world_matrix(arm, rig)
mot = np.load(motion_path)
G, Q = mot['G'], mot['quat']
T = G.shape[0]
frames = sorted({int(f) for f in opt.get('frames', []) if 0 <= int(f) < T} | {0, T - 1})
sc = bpy.context.scene
sc.render.fps = int(round(fps))
sc.render.fps_base = sc.render.fps / fps
# Video frame f -> scene frame f (time f/fps), so keys land on whole frames in Max/Maya at the video frame rate.
sc.frame_start, sc.frame_end = 0, T - 1
arm.animation_data_create()
act = bpy.data.actions.new(name)
arm.animation_data.action = act
for t in frames:
    apply_pose(arm, rig, G[t], Q[t], frame=t, key=True)
for fc in act.fcurves:
    for kp in fc.keyframe_points:
        kp.interpolation = interp
        if interp == 'BEZIER':
            kp.handle_left_type = kp.handle_right_type = 'AUTO_CLAMPED'
    fc.update()
print(f'[v2r] keyed {len(frames)} of {T} frames, {interp}')

bpy.ops.object.select_all(action='DESELECT')
for o in [arm] + meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = arm
os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)
def sparse_fbx_patch(key_frames, interp):
    """Blender's FBX exporter always samples every frame (bake) and its 'simplify' only drops samples whose value
    barely changes, so a plain export still has one key per frame per bone (verified in 3ds Max 2026: 193 keys).
    Patch it so that ONLY our keyframe frames are written, and so the FBX key interpolation flag says cubic
    (auto tangents, smooth in Max/Maya/Unity) or linear instead of Blender's fixed linear flag."""
    import io_scene_fbx.fbx_utils as fu
    import io_scene_fbx.export_fbx_bin as fb
    keep = sorted(key_frames)

    def simplify(self, fac, step, force_keep=False):
        if self._frame_times_array is None:
            return
        # Sample i is scene frame frame_start + i (bake step 1) = video frame i.
        mask = self._frame_write_mask_array
        mask[:] = False
        idx = [i for i in keep if i < mask.shape[1]]
        mask[:, idx] = True

    fu.AnimationCurveNodeWrapper.simplify = simplify
    # FbxAnimCurveDef: interpolation constant 0x2 / linear 0x4 / cubic 0x8; tangent auto 0x100,
    # generic time-independent 0x2000 + clamp progressive 0x4000.
    flags = (0x8 if interp == 'BEZIER' else 0x4) | 0x100 | 0x2000 | 0x4000
    orig = fb.elem_data_single_int32_array

    def int32_array(elem, name, value):
        if name == b'KeyAttrFlags':
            value = (flags,)
        return orig(elem, name, value)

    fb.elem_data_single_int32_array = int32_array


if fmt == 'fbx':
    sparse_fbx_patch(frames, interp)
    # bake keeps scene frames 1..T; the patch above keeps only the keyframe samples.
    bpy.ops.export_scene.fbx(filepath=out_file, use_selection=True, bake_anim=True, bake_anim_use_all_actions=False,
                             bake_anim_use_nla_strips=False, bake_anim_force_startend_keying=True,
                             bake_anim_step=1.0, bake_anim_simplify_factor=1.0, add_leaf_bones=False,
                             path_mode='COPY', embed_textures=True)
elif fmt == 'glb':
    bpy.ops.export_scene.gltf(filepath=out_file, use_selection=True, export_animations=True,
                              export_force_sampling=False, export_frame_range=True)
else:
    raise SystemExit(f'unknown format {fmt}')
print('[v2r] exported', out_file)
