"""Build a local fake V2R_ROOT for the studio (no GPU): one trained-looking project from the fixture."""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixture')
ROOT = os.path.join(HERE, 'root')
REPO = os.path.dirname(os.path.dirname(HERE))

shutil.rmtree(ROOT, ignore_errors=True)
for name, model in (('m11101_tex', os.path.join(REPO, '11101_nogun_exam_textured.fbx')), ('lizard_demo', None)):
    p = os.path.join(ROOT, 'projects', name)
    os.makedirs(os.path.join(p, 'model'))
    for fn in ('studio.json', 'prepare_opts.json', 'reference_two_views.png'):
        shutil.copy2(os.path.join(FIX, fn), os.path.join(p, fn))
    rig = json.load(open(os.path.join(FIX, 'rig.json')))
    rig['model'] = model or '/nonexistent/model.fbx'
    json.dump(rig, open(os.path.join(p, 'rig.json'), 'w'))
    open(os.path.join(p, 'model', 'best.pt'), 'wb').write(b'fake')
    if not model:
        os.makedirs(os.path.join(p, 'out', 'exam'))
        shutil.copy2(os.path.join(FIX, 'out', 'ladingwu_temporal', 'ladingwu_temporal.glb'), os.path.join(p, 'out', 'exam', 'exam.glb'))
os.makedirs(os.path.join(ROOT, 'projects', 'half_done'))   # not trained -> not listed
os.makedirs(os.path.join(ROOT, 'jobs'))
print('ok', ROOT)
