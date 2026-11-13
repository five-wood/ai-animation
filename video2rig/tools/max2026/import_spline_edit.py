"""Write the per-part spline edit packet into a NEW copy of the working base scene.
Run in 3ds Max 2026 (3dsmaxbatch with -sceneFile base_scene.max, or an interactive session that has
base_scene.max open and unsaved-clean). Never saves over the base, the original or older outputs.

Order per frame: COM -> Pelvis -> Spine... -> arms -> bones (parent before child). Feet: first key
sliding (new independent world reference), later keys planted (join). Legs are left to Biped IK.
After writing, samples Max at every integer frame + half frames and stores evaluated transforms.
"""
import json, os, time, traceback
import pymxs
rt = pymxs.runtime

RUN = os.environ.get('V2R_RUN') or r'D:\Project\ai-animation\work\max_editable_20261111_spline'
BASE_SCENE = r'D:\Project\ai-animation\work\max_editable_20261109\base_scene.max'
PACKET = RUN + r'\edit_packet.json'
OUTPUT = RUN + '\\' + (os.environ.get('V2R_OUT_NAME') or 'spline_edit.max')  # keyframe-first runs use keyframe_edit.max
REPORT = RUN + r'\import_report.json'
SAMPLES = RUN + r'\max_samples.json'
ORDER = ['COM', 'Torso', 'L Arm', 'R Arm', 'L Ear', 'R Ear', 'Tail']
SAMPLE_NODES = ['Bip001', 'Bip001 Pelvis', 'Bip001 Spine', 'Bip001 Spine1', 'Bip001 Neck', 'Bip001 Head',
                'Bip001 L Clavicle', 'Bip001 L UpperArm', 'Bip001 L Forearm', 'Bip001 L Hand',
                'Bip001 R Clavicle', 'Bip001 R UpperArm', 'Bip001 R Forearm', 'Bip001 R Hand',
                'Bip001 L Thigh', 'Bip001 L Calf', 'Bip001 L Foot', 'Bip001 L Toe0',
                'Bip001 R Thigh', 'Bip001 R Calf', 'Bip001 R Foot', 'Bip001 R Toe0',
                'Bone001', 'Bone002', 'Bone142', 'Bone0013', 'Bone0021', 'Bone0022', 'Bone004']


def write_json(path, data):
    with open(path + '.tmp', 'w', encoding='utf8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(path + '.tmp', path)


def m3(rows):
    return rt.matrix3(*[rt.point3(*v) for v in rows])


def rows(m):
    return [[float(m[i][j]) for j in range(3)] for i in range(4)]


def node(name):
    n = rt.getNodeByName(name)
    if n is None:
        raise RuntimeError('missing node ' + name)
    return n


def key_times(ctrl):
    try:
        n = int(rt.numKeys(ctrl))
    except Exception:
        return None
    if n < 0:
        return None
    return [float(rt.getKeyTime(ctrl, i).frame) for i in range(1, n + 1)]


def euler_key_times(r):
    out = set()
    for i in range(1, int(r.numSubs) + 1):
        try:
            kt = key_times(rt.getSubAnim(r, i).controller)
        except Exception:
            kt = None
        out.update(kt or [])
    return sorted(out)


def bone_rot_ctrl(n):
    c = n.controller
    if str(rt.classOf(c)).lower() != 'prs':
        raise RuntimeError('unsupported controller on ' + str(n.name))
    r = rt.getSubAnim(c, 2).controller
    if str(rt.classOf(r)).lower() != 'euler_xyz':
        raise RuntimeError('unsupported rotation controller on ' + str(n.name))
    return r


def main():
    t0 = time.time()
    current = os.path.abspath(str(rt.maxFilePath) + str(rt.maxFileName))
    if os.path.normcase(current) != os.path.normcase(BASE_SCENE):
        raise RuntimeError('Open scene must be the working base: ' + current)
    if bool(rt.getSaveRequired()):
        raise RuntimeError('Base scene has unsaved changes; refusing')
    if os.path.exists(OUTPUT):
        raise RuntimeError('Refusing to overwrite ' + OUTPUT)
    pk = json.load(open(PACKET, encoding='utf8'))
    fps = int(pk['fps'])
    if int(rt.frameRate) != fps:
        raise RuntimeError('frame rate mismatch')
    com = node('Bip001')
    bc = com.controller
    if bool(bc.figureMode) or bool(bc.footstepMode) or bool(bc.mixerMode) or int(rt.biped.numLayers(bc)) != 0:
        raise RuntimeError('unsupported Biped mode/layers')
    rt.execute('fn v2rSetWorldRotation node q = (in coordsys (transMatrix node.transform.pos) (node.rotation = q))')

    # ---- pre-existing keys on nodes we write: only frame-0 Biped pose keys are allowed
    written = [n for p in ORDER for n in pk['parts'][p]['nodes'] if not pk['parts'][p]['constant_local'][n]]
    pre = {}
    for name in written + ['Bip001 L Foot', 'Bip001 R Foot']:
        n = node(name)
        if str(rt.classOf(n)) == 'Biped_Object':
            kt = key_times(n.controller) if name != 'Bip001' else None
        else:
            kt = euler_key_times(bone_rot_ctrl(n))
        pre[name] = kt
        if kt and any(abs(t) > 1e-6 for t in kt):
            raise RuntimeError('pre-existing animation on ' + name + ': ' + str(kt))

    # ---- body / arms / bones: part by part (parent parts first); inside a part frame by frame,
    # parent node before child. Child world = legal LOCAL * actual Max parent at that frame, so the
    # stored local key equals the legal local value and parent interpolation never leaks into it.
    frames = set()
    with pymxs.animate(True):
        for p in ORDER:
            part = pk['parts'][p]
            names = [n for n in part['nodes'] if not part['constant_local'][n]]
            K = sorted({int(k) for n in names for k in part['keys'][n]})
            frames.update(K)
            for f in K:
                with pymxs.attime(float(f)):
                    for name in names:
                        n = node(name)
                        v = part['keys'][name][str(f)]
                        par = pk['parent'][name]
                        tm = m3(v['world']) if par is None else m3(v['local']) * node(par).transform
                        if name == 'Bip001':
                            rt.biped.setTransform(n, rt.Name('pos'), tm.row4, True)
                            rt.biped.setTransform(n, rt.Name('rotation'), tm.rotationpart, True)
                        elif str(rt.classOf(n)) == 'Biped_Object':
                            rt.biped.setTransform(n, rt.Name('rotation'), tm.rotationpart, True)
                        else:
                            bone_rot_ctrl(n)
                            rt.v2rSetWorldRotation(n, rt.inverse(tm.rotationpart))
    frames = sorted(frames)
    # ---- feet: per contact segment a world anchor (sliding key = new reference, planted = join),
    # free keys in between. Old single-plant packets are converted on the fly.
    foot_log = {}
    for s in ('L', 'R'):
        ft = pk['feet'][s]
        n = node(ft['node'])
        keys = ft.get('keys')
        if keys is None:
            f0, f1 = ft['frames']
            keys = [{'frame': f0, 'type': 'plant_start', 'rows': ft['anchor_rows']},
                    {'frame': f1, 'type': 'plant_end', 'rows': ft['anchor_rows']}]
        done = []
        for k in keys:
            A = m3(k['rows'])
            with pymxs.attime(float(k['frame'])):
                rt.biped.setTransform(n, rt.Name('pos'), A.row4, True)
                rt.biped.setTransform(n, rt.Name('rotation'), A.rotationpart, True)
                if k['type'] == 'plant_start':
                    rt.biped.setSlidingKey(n)
                elif k['type'] in ('plant_end', 'plant_hold'):
                    rt.biped.setPlantedKey(n)
                else:
                    rt.biped.setFreeKey(n)
            done.append([k['frame'], k['type']])
        foot_log[s] = done
    f_end = float(pk['frames'][1])
    if float(rt.animationRange.end.frame) != f_end:
        rt.animationRange = rt.interval(0, f_end)  # playback range only; keys outside are kept

    # ---- inventory of keys actually stored
    inventory = {}
    for name in SAMPLE_NODES:
        n = node(name)
        if name == 'Bip001':
            subs = {}
            for i in range(1, int(n.controller.numSubs) + 1):
                sub = rt.getSubAnim(n.controller, i)
                try:
                    subs[str(rt.getSubAnimName(n.controller, i))] = key_times(sub.controller)
                except Exception:
                    pass
            inventory[name] = subs
        elif str(rt.classOf(n)) == 'Biped_Object':
            inventory[name] = key_times(n.controller)
        else:
            inventory[name] = euler_key_times(bone_rot_ctrl(n))
    # ---- evaluation samples (integer + half frames)
    f0, f1 = pk['frames']
    times = [f0 + 0.5 * i for i in range(int(2 * (f1 - f0)) + 1)]
    samples = []
    for t in times:
        with pymxs.attime(t):
            samples.append({name: rows(node(name).transform) for name in SAMPLE_NODES})
    rt.sliderTime = 0
    if not rt.saveMaxFile(OUTPUT, useNewFile=True, clearNeedSaveFlag=True, quiet=True):
        raise RuntimeError('save failed')
    write_json(SAMPLES, {'times': times, 'samples': samples})
    foot_keys = {}
    for s in ('L', 'R'):
        c = node('Bip001 %s Foot' % s).controller
        foot_keys[s] = []
        for i in range(1, int(rt.numKeys(c)) + 1):
            k = rt.biped.getKey(c, i)
            foot_keys[s].append({p: str(rt.getProperty(k, p)) for p in ('time', 'ikBlend', 'ikSpace', 'ikJoinedPivot')})
    write_json(REPORT, {'status': 'written', 'output': OUTPUT, 'base_scene': BASE_SCENE, 'frames_written': frames,
                        'pre_existing_key_times': pre, 'inventory': inventory, 'feet': foot_log, 'foot_key_props': foot_keys,
                        'animation_range': [float(rt.animationRange.start.frame), float(rt.animationRange.end.frame)],
                        'elapsed_seconds': time.time() - t0})
    print('V2R edit written', OUTPUT)


try:
    main()
except Exception:
    write_json(RUN + r'\import.error.json', {'error': traceback.format_exc(), 'time': time.strftime('%Y-%m-%d %H:%M:%S')})
    print(traceback.format_exc())
