"""Read-only editability probe (3dsmaxbatch -sceneFile <run>\\spline_edit.max; nothing saved):
move a planted foot at ONE key of a plant and check whether the whole plant follows (Join to Prev).
Env: V2R_RUN, V2R_SIDE, V2R_A, V2R_B (plant frames), V2R_KEY (frame of the key to edit)."""
import json, os, math, traceback
import pymxs
rt = pymxs.runtime
RUN = os.environ['V2R_RUN']
SIDE, A, B, KEY = os.environ['V2R_SIDE'], int(os.environ['V2R_A']), int(os.environ['V2R_B']), int(os.environ['V2R_KEY'])
DX = 3.0
out = {'side': SIDE, 'plant': [A, B], 'edited_key': KEY, 'move_cm': [DX, 0, 0]}


def pos(n, t):
    with pymxs.attime(float(t)):
        p = n.transform.row4
    return [float(p.x), float(p.y), float(p.z)]


try:
    foot = rt.getNodeByName('Bip001 %s Foot' % SIDE)
    probe = sorted(set([A, (A + B) // 2, B, KEY, max(0, A - 3), B + 3]))
    before = {t: pos(foot, t) for t in probe}
    with pymxs.attime(float(KEY)):
        tm = foot.transform
        p = tm.row4
        rt.biped.setTransform(foot, rt.Name('pos'), rt.point3(p.x + DX, p.y, p.z), True)
    after = {t: pos(foot, t) for t in probe}
    out['shift_x_by_frame'] = {str(t): round(after[t][0] - before[t][0], 3) for t in probe}
    c = foot.controller
    out['foot_keys_after'] = []
    for i in range(1, int(rt.numKeys(c)) + 1):
        f = float(rt.getKeyTime(c, i).frame)
        if A - 6 <= f <= B + 6:
            k = rt.biped.getKey(c, i)
            out['foot_keys_after'].append({'frame': f, 'ikBlend': str(k.ikBlend), 'join': str(k.ikJoinedPivot)})
except Exception:
    out['error'] = traceback.format_exc()
with open(os.path.join(RUN, 'move_plant_probe.json'), 'w', encoding='utf8') as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
