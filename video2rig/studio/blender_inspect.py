"""Inspect an uploaded character model and export the web preview exactly as training will see it.

blender -b --factory-startup --python studio/blender_inspect.py -- <model> <out.glb> <report.json>

Loads the model with v2r's own load_model(up="+Z") (same import, skeleton/mesh selection, prop removal
and rebinding as `prepare`), so the orientation the user fixes on the web is the orientation prepare
will start from. Writes a GLB (Blender +Z up -> glTF +Y up) and a JSON report:
  {ok, error, bones, meshes, bbox:[[x,y,z],[x,y,z]], flat_in_z, suggest:{up, reason}, joints:[[x,y,z]...]}
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'v2r', 'blender'))
from common import *  # noqa: E402,F401,F403

args = script_args()
model, out_glb, out_json = args[0], args[1], args[2]
rep = {'ok': False}
try:
    arm, meshes = load_model(model, procedural_material=None, up='+Z', drop_props=True)
    bpy.context.view_layer.update()
    pts = []
    for m in meshes:
        M = m.matrix_world
        for c in m.bound_box:
            pts.append(list(M @ Vector(c)))
    P = np.array(pts)
    lo, hi = P.min(0), P.max(0)
    ext = hi - lo
    rep.update(ok=True, bones=len(arm.data.bones), meshes=len(meshes),
               bbox=[lo.tolist(), hi.tolist()], extent=ext.tolist())
    # same warning as prepare.py: much lower than it is wide => probably lying down
    rep['flat_in_z'] = bool(ext[2] < 0.75 * max(ext[0], ext[1]))
    # Skeleton direction: root bone -> deepest descendants' mean tail = rough "body axis" hint.
    Wm = arm.matrix_world
    heads = {b.name: Wm @ b.head_local for b in arm.data.bones}
    tails = {b.name: Wm @ b.tail_local for b in arm.data.bones}
    rep['joints'] = [[round(v, 5) for v in heads[b.name]] + [round(v, 5) for v in tails[b.name]] for b in arm.data.bones][:400]
    # Suggest an up axis: the bbox axis of largest extent among those whose skeleton spread is largest,
    # signed so that the head end (where most bone tails are) is "up". Only a hint for the UI.
    centre = (lo + hi) / 2
    T = np.array([list(t) for t in tails.values()])
    axis = int(np.argmax(ext))
    sign = 1 if (T[:, axis] - centre[axis]).mean() >= 0 else -1
    rep['suggest'] = {'axis': '+-'[sign < 0] + 'XYZ'[axis], 'reason': 'longest side of the model'}
    bpy.ops.object.select_all(action='DESELECT')
    for o in [arm] + meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm
    os.makedirs(os.path.dirname(os.path.abspath(out_glb)), exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=out_glb, use_selection=True, export_animations=False, export_yup=True)
    rep['glb'] = os.path.basename(out_glb)
except SystemExit as e:
    msg = str(e)
    rep['error'] = 'NO_ARMATURE' if 'NO_ARMATURE' in msg else 'NO_SKINNED_MESH' if 'NO_SKINNED_MESH' in msg else 'IMPORT_FAILED'
    rep['detail'] = msg[-500:]
except Exception as e:  # noqa: BLE001
    rep['error'] = 'IMPORT_FAILED'
    rep['detail'] = (traceback.format_exc() or str(e))[-1500:]
with open(out_json, 'w', encoding='utf-8') as f:
    json.dump(rep, f, ensure_ascii=False)
print('[v2r] inspect', 'ok' if rep['ok'] else rep.get('error'))
