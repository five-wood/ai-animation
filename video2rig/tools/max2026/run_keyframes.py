"""Generate keyframes_auto.json for ONE animation result (the web "生成候选关键帧" job).

python run_keyframes.py <project_dir> <result_name> [--profile <bind_inspection.json>]

Steps (CPU only, ~10 s): contacts (detect_contacts.py) -> candidates (extract_keyframes.py) ->
breakdown ladder (breakdown_ladder.py, only when the Biped bind profile matches the rig).
Inputs are copied into a private temp dir; the result dir only receives
  keyframes_auto.json            (atomic replace)
  keyframes_auto_history/<time>_<sha12>.json   (the previous candidate file, never overwritten)
  contacts_auto.json / contacts_plan.json      (evidence, overwritten; marked reviewed=false)
keyframes_artist.json is never touched: artist annotations reference candidate ids and the
page converts them when the candidate file changes (base_auto_sha256 differs).
Prints "[v2r] keyframes stage <n>/3 <name>" markers for the task monitor.
"""
from pathlib import Path
import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REQUIRED_KPS = ['Bip001', 'Bip001 Spine1', 'Bip001 Neck', 'Bip001 L Hand', 'Bip001 R Hand',
                'Bip001 L Forearm', 'Bip001 R Forearm', 'Bip001 L Foot', 'Bip001 R Foot']


def log(msg):
    print(f'[v2r] {msg}', flush=True)


def fail(msg, code=2):
    log(f'KEYFRAMES_FAILED: {msg}')
    sys.exit(code)


def check_inputs(project, out):
    """(rig, motion, reading, summary) paths or a readable reason why this result cannot be processed."""
    rig = project / 'rig.json'
    motion = next((p for p in (out / 'motion_temporal.npz', out / 'motion.npz') if p.is_file()), None)
    reading = out / 'reading.npz'
    summary = out / 'summary.json'
    if not rig.is_file():
        return None, '项目缺少 rig.json'
    if motion is None:
        return None, '结果缺少 motion.npz（求解动作）'
    if not reading.is_file():
        return None, '结果缺少 reading.npz（识别读数），无法判断着地和读取置信度'
    names = [k['name'] for k in json.loads(rig.read_text(encoding='utf8')).get('keypoints', [])]
    missing = [n for n in REQUIRED_KPS if n not in names]
    if missing:
        return None, f'骨架没有 Biped 关节 {", ".join(missing[:3])}{" 等" if len(missing) > 3 else ""}，目前只支持 Biped 角色'
    return (rig, motion, reading, summary if summary.is_file() else None), None


def profile_matches(profile, rig_path):
    """The Max bind profile belongs to one Biped; use it only if every needed node is present."""
    if not profile or not Path(profile).is_file():
        return False, '没有找到 Max 骨架配置（bind profile）'
    try:
        import build_spline_edit as bse
        nodes = {n['name'] for n in json.loads(Path(profile).read_text(encoding='utf8'))['nodes']}
        bones = {b['name'] for b in json.loads(Path(rig_path).read_text(encoding='utf8'))['bones']}
        need = set(bse.NODES)
        if not need <= nodes:
            return False, 'Max 骨架配置与这个角色不符'
        if not need <= bones:
            return False, '角色骨骼与 Max 骨架配置不符'
    except Exception as e:  # noqa: BLE001
        return False, f'骨架配置无法读取：{e}'
    return True, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('result')
    ap.add_argument('--profile', default=str(HERE / 'profiles' / 'm11101_bind_inspection.json'))
    a = ap.parse_args()
    project = Path(a.project).resolve(strict=True)
    out = (project / 'out' / a.result).resolve(strict=True)
    if out.parent != project / 'out':
        fail('结果目录不在项目 out/ 下')
    t0 = time.time()
    log(f'keyframes start {project.name}/{out.name}')
    paths, reason = check_inputs(project, out)
    if paths is None:
        fail(reason)
    rig, motion, reading, summary = paths
    video = 'unknown.mp4'
    if summary:
        sv = json.loads(summary.read_text(encoding='utf8')).get('source_video') or ''
        video = sv.replace('\\', '/').rstrip('/').split('/')[-1] or video
    with tempfile.TemporaryDirectory(prefix='v2r-keyframes-') as tmp:
        work = Path(tmp)
        shutil.copy2(rig, work / 'rig.json')
        shutil.copy2(motion, work / 'motion_temporal.npz')
        shutil.copy2(reading, work / 'reading.npz')
        if summary:
            shutil.copy2(summary, work / 'summary.json')

        log('keyframes stage 1/3 contacts')
        import detect_contacts as dc
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            dc.figure = lambda *args, **kw: None  # evidence plots are optional on the server
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            dc.main(str(work))
        for line in buf.getvalue().splitlines()[:6]:
            print('   ', line)

        log('keyframes stage 2/3 candidates')
        ok, why = profile_matches(a.profile, rig)
        import extract_keyframes as ek
        argv = ['extract_keyframes.py', str(work), '--video', video, '--out', str(work / 'keyframes_auto.json'),
                '--contacts', str(work / 'contacts_auto.json')]
        if ok:
            import build_spline_edit as bse
            bse.PROFILE = Path(a.profile).resolve()   # module already imported by profile_matches()
            os.environ['V2R_BIND_PROFILE'] = str(bse.PROFILE)
            argv += ['--ladder-contacts', str(work / 'contacts_plan.json')]
        else:
            argv += ['--no-ladder']
            log(f'keyframes ladder skipped: {why}')
        sys.argv = argv
        ek.main()
        doc = json.loads((work / 'keyframes_auto.json').read_text(encoding='utf8'))
        if ok:
            log('keyframes stage 3/3 ladder (computed with the candidates)')
            lad = doc.get('breakdown_ladder')
            if isinstance(lad, dict) and lad.get('error'):
                log(f'keyframes ladder failed: {lad["error"]}')
        else:
            doc['breakdown_ladder'] = {'error': why}
        doc['generated_from'] = {'project': project.name, 'result': out.name, 'motion_file': motion.name,
                                 'motion_sha256': hashlib.sha256(motion.read_bytes()).hexdigest(),
                                 'reading_sha256': hashlib.sha256(reading.read_bytes()).hexdigest()}
        data = (json.dumps(doc, ensure_ascii=False, indent=1) + '\n').encode('utf8')

        # install: keep the previous candidate file, then replace atomically
        target = out / 'keyframes_auto.json'
        if target.exists():
            old = target.read_bytes()
            if hashlib.sha256(old).hexdigest() == hashlib.sha256(data).hexdigest():
                log('keyframes unchanged (identical candidate file)')
            hist = out / 'keyframes_auto_history'
            hist.mkdir(exist_ok=True)
            name = time.strftime('%Y%m%d-%H%M%S') + '_' + hashlib.sha256(old).hexdigest()[:12] + '.json'
            dest = hist / name
            if not dest.exists():
                with open(dest, 'xb') as f:
                    f.write(old)
            log(f'keyframes previous candidates kept: {dest.name}')
        for name in ('contacts_auto.json', 'contacts_plan.json'):
            shutil.copy2(work / name, out / f'.{name}.tmp')
            os.replace(out / f'.{name}.tmp', out / name)
        tmp_target = out / '.keyframes_auto.json.tmp'
        tmp_target.write_bytes(data)
        os.replace(tmp_target, target)
    n = len(doc['keyframes'])
    lad = doc.get('breakdown_ladder') or {}
    extra = f", ladder {len(lad.get('breakdowns', []))} ranked (suggested {lad.get('suggested_count')})" if isinstance(lad, dict) and 'breakdowns' in lad else ', no ladder'
    log(f'keyframes done in {time.time() - t0:.1f}s: {n} candidates{extra} -> {target}')


if __name__ == '__main__':
    main()
