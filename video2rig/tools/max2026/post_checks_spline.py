"""Post checks for spline_edit.max (run with 3dsmaxbatch -sceneFile spline_edit.max).
1. Protected curves: keys of every non-Biped node we did not write are identical to base_scene.max.
2. Editability on the real clip: change ONE COM key (lower 1.5cm) and confirm feet stay planted;
   saved as a separate demo file com_edit_test.max. spline_edit.max itself is never re-saved.
"""
import json, os, math, traceback
import pymxs
rt = pymxs.runtime
RUN = os.environ.get('V2R_RUN') or r'D:\Project\ai-animation\work\max_editable_20261111_spline'
BASE_SCENE = r'D:\Project\ai-animation\work\max_editable_20261109\base_scene.max'
EDIT = RUN + '\\' + (os.environ.get('V2R_OUT_NAME') or 'spline_edit.max')
DEMO = RUN + r'\com_edit_test.max'
WRITTEN = {'Bone001', 'Bone002', 'Bone142', 'Bone0013', 'Bone0021', 'Bone0022', 'Bone004'}


def snapshot():
    out, seen = {}, set()

    def scan(c, path):
        try:
            h = int(rt.getHandleByAnim(c))
        except Exception:
            return
        if h in seen:
            return
        seen.add(h)
        try:
            n = int(rt.numKeys(c))
        except Exception:
            n = -1
        children = []
        for i in range(1, int(c.numSubs) + 1):
            try:
                ch = rt.getSubAnim(c, i).controller
            except Exception:
                continue
            if ch is not None:
                children.append((ch, path + '/' + str(rt.getSubAnimName(c, i))))
        if n > 0 and not children:
            keys = []
            for i in range(1, n + 1):
                t = rt.getKeyTime(c, i)
                with pymxs.attime(float(t.frame)):
                    try:
                        v = str(c.value)
                    except Exception:
                        v = '?'
                keys.append([int(t.ticks), v])
            out[path] = keys
        for ch, p in children:
            scan(ch, p)
    for n in list(rt.objects):
        if str(rt.classOf(n)) == 'Biped_Object' or str(n.name) in WRITTEN:
            continue
        scan(n.controller, str(n.name))
    return out


def xyz(p):
    return [float(p.x), float(p.y), float(p.z)]


def dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def flex(h, k, f):
    u = [k[i] - h[i] for i in range(3)]
    v = [f[i] - k[i] for i in range(3)]
    c = sum(u[i] * v[i] for i in range(3)) / math.sqrt(sum(x * x for x in u) * sum(x * x for x in v))
    return math.degrees(math.acos(max(-1, min(1, c))))


def plants_of(pk):
    """[(side, a, b, anchor_xyz)] for old (single anchor) and new (keys) packets."""
    out = []
    for j, s in enumerate('LR'):
        ft = pk['feet'][s]
        if 'keys' in ft:
            st = [k for k in ft['keys'] if k['type'] == 'plant_start']
            en = [k for k in ft['keys'] if k['type'] == 'plant_end']
            out += [(j, k0['frame'], k1['frame'], k0['rows'][3]) for k0, k1 in zip(st, en)]
        else:
            out.append((j, ft['frames'][0], ft['frames'][1], ft['anchor_rows'][3]))
    return out


def feet_check(plants):
    drift, knees = [0.0, 0.0], [[999, 0], [999, 0]]
    for j, a, b, anc in plants:
        s = 'LR'[j]
        for i in range(int(2 * (b - a)) + 1):
            with pymxs.attime(a + 0.5 * i):
                f = xyz(rt.getNodeByName('Bip001 %s Foot' % s).transform.row4)
                drift[j] = max(drift[j], dist(f, anc))
                fl = flex(xyz(rt.getNodeByName('Bip001 %s Thigh' % s).transform.row4),
                          xyz(rt.getNodeByName('Bip001 %s Calf' % s).transform.row4), f)
                knees[j] = [min(knees[j][0], fl), max(knees[j][1], fl)]
    return drift, knees


def main():
    cur = os.path.abspath(str(rt.maxFilePath) + str(rt.maxFileName))
    if os.path.normcase(cur) != os.path.normcase(EDIT):
        raise RuntimeError('open spline_edit.max first: ' + cur)
    if os.path.exists(DEMO):
        raise RuntimeError('demo exists; refusing overwrite')
    edit_snap = snapshot()
    pk = json.load(open(RUN + r'\edit_packet.json', encoding='utf8'))
    plants = plants_of(pk)
    drift0, knees0 = feet_check(plants)
    # one COM key edit: a COM pose key inside the longest double-support stretch if there is one
    com = rt.getNodeByName('Bip001')
    poses = pk['parts']['COM']['poses']
    both = [f for f in poses if sum(1 for j, a, b, _ in plants if a < f < b) == 2]
    f = both[len(both) // 2] if both else poses[len(poses) // 2]
    with pymxs.attime(float(f)):
        p = com.transform.row4
        before = xyz(p)
        rt.biped.setTransform(com, rt.Name('pos'), rt.point3(p.x, p.y, p.z - 1.5), True)
    with pymxs.attime(float(f)):
        after = xyz(com.transform.row4)
    drift1, knees1 = feet_check(plants)
    rt.sliderTime = f
    if not rt.saveMaxFile(DEMO, useNewFile=True, clearNeedSaveFlag=True, quiet=True):
        raise RuntimeError('save demo failed')
    rt.loadMaxFile(BASE_SCENE, quiet=True)
    base_snap = snapshot()
    changed = [k for k in sorted(set(base_snap) | set(edit_snap)) if base_snap.get(k) != edit_snap.get(k)]
    detail = {k: {'base': base_snap.get(k), 'edit': edit_snap.get(k)} for k in changed}
    rep = {'protected_tracks_checked': len(base_snap), 'protected_tracks_changed': changed, 'changed_detail': detail,
           'feet_before_edit': {'anchor_drift_cm': drift0, 'knee_flex_deg': knees0},
           'com_edit': {'frame': f, 'com_before': before, 'com_after': after, 'drop_cm': before[2] - after[2],
                        'keys_touched': 1, 'output': DEMO},
           'feet_after_edit': {'anchor_drift_cm': drift1, 'knee_flex_deg': knees1}}
    with open(RUN + r'\post_checks.json', 'w', encoding='utf8') as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    print('V2R post checks', rep)


try:
    main()
except Exception:
    with open(RUN + r'\post_checks.error.json', 'w', encoding='utf8') as fh:
        json.dump({'error': traceback.format_exc()}, fh)
    print(traceback.format_exc())
