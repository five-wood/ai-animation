"""Bridge for the local preview server: runs the real web/keyframes_store.py.
python kf_bridge.py view <result_dir>          -> {"status": 200, "body": {...}}
python kf_bridge.py save <result_dir> < body    -> {"status": 200|4xx|5xx, "body": {...}}
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'video2rig'))
from web import keyframes_store as ks  # noqa: E402


def summary(out_dir):
    try:
        return json.loads((Path(out_dir) / 'summary.json').read_text(encoding='utf-8'))
    except Exception:
        return None


KEYFRAME_REQUIRED = ('Bip001', 'Bip001 Spine1', 'Bip001 Neck', 'Bip001 L Hand', 'Bip001 R Hand',
                     'Bip001 L Forearm', 'Bip001 R Forearm', 'Bip001 L Foot', 'Bip001 R Foot')


def generation(project_dir, out_dir):
    """Same rules as server.keyframe_generation (kept in sync by hand for this preview only)."""
    try:
        rig = json.loads((Path(project_dir) / 'rig.json').read_text(encoding='utf-8'))
    except Exception:
        rig = {}
    names = {k.get('name') for k in rig.get('keypoints', [])}
    missing = [n for n in KEYFRAME_REQUIRED if n not in names]
    if missing:
        return {'available': False, 'reason': '这个角色没有 Biped 关节（如 ' + '、'.join(missing[:2]) + '），目前只支持 Biped 角色'}
    o = Path(out_dir)
    if not any((o / f).is_file() for f in ('motion.npz', 'motion_temporal.npz')):
        return {'available': False, 'reason': '这个结果没有 motion.npz（求解动作），可能未完成或检查未通过'}
    if not (o / 'reading.npz').is_file():
        return {'available': False, 'reason': '这个结果没有 reading.npz（识别读数），无法判断着地，不能生成候选'}
    return {'available': True, 'reason': ''}


def main():
    cmd, out_dir = sys.argv[1], sys.argv[2]
    if cmd == 'view':
        body = ks.view(out_dir, summary(out_dir), None)
        body['generation'] = generation(Path(out_dir).parent.parent, out_dir)
        print(json.dumps({'status': 200, 'body': body}, ensure_ascii=False))
        return
    if cmd == 'check':
        project_dir, name, replace = Path(out_dir), sys.argv[3], sys.argv[4] == '1'
        o = project_dir / 'out' / name
        if not name or not o.is_dir():
            print(json.dumps({'status': 404, 'body': {'detail': f'no result {name}'}}, ensure_ascii=False))
            return
        g = generation(project_dir, o)
        if not g['available']:
            print(json.dumps({'status': 400, 'body': {'detail': g['reason']}}, ensure_ascii=False))
            return
        if (o / ks.AUTO_FILE).is_file() and not replace:
            print(json.dumps({'status': 409, 'body': {'detail': '这个结果已有候选关键帧。重新生成会替换候选（旧文件会保留为历史版本），请确认后再提交。'}}, ensure_ascii=False))
            return
        print(json.dumps({'status': 200, 'body': {'args': {'name': name, 'replace': replace}}}, ensure_ascii=False))
        return
    try:
        req = json.loads(sys.stdin.buffer.read().decode('utf-8') or 'null')
    except ValueError:
        print(json.dumps({'status': 400, 'body': {'detail': 'invalid JSON'}}))
        return
    try:
        body = ks.save_artist(out_dir, req, summary(out_dir))
        print(json.dumps({'status': 200, 'body': body}, ensure_ascii=False))
    except ks.KeyframeError as e:
        print(json.dumps({'status': e.status, 'body': {'detail': e.detail}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
