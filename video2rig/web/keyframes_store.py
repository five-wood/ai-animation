"""Keyframe candidates and artist annotations for one result directory (stdlib only).

Contract: docs/关键帧数据与网页接口约定_20261111.md

* ``keyframes_auto.json`` is written by the extraction scripts and is only READ here.
* ``keyframes_artist.json`` is replaced atomically on every save; each revision is also
  kept as ``keyframes_artist_history/rev{N:04d}.json`` (append-only, never overwritten).

The caller (web/server.py) validates project/result names and passes an existing result
directory. Functions here raise ``KeyframeError(status, detail)``; the server maps it to
an HTTP error. A module lock serialises read-check-write so two saves based on the same
revision cannot both succeed inside one server process.
"""
import hashlib
import json
import math
import os
import re
import tempfile
import threading
from datetime import datetime

AUTO_FILE = 'keyframes_auto.json'
ARTIST_FILE = 'keyframes_artist.json'
HISTORY_DIR = 'keyframes_artist_history'
ARTIST_SCHEMA = 'v2r.keyframes.artist/1'
TYPES = ('start', 'end', 'contact', 'extreme', 'hold_start', 'hold_end', 'impact',
         'anticipation', 'rebound', 'technical', 'breakdown', 'custom')
ORIGINS = ('auto', 'artist', 'ladder')
MAX_KEYS = 500
MAX_NOTE = 500
MAX_NOTES = 2000
MAX_AUTHOR = 40
ID_RE = re.compile(r'^[A-Za-z0-9_\-]{1,32}$')
HISTORY_RE = re.compile(r'^rev(\d{4,})\.json$')
LOCK = threading.Lock()


class KeyframeError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail if isinstance(detail, str) else detail.get('message', ''))
        self.status = status
        self.detail = detail


def bad(message):
    return KeyframeError(400, message)


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def read_json(path, with_sha=False):
    """(value, error[, sha256]). Missing file -> (None, None); broken file -> (None, message).
    The digest is computed from the same bytes that were parsed."""
    sha = None
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except FileNotFoundError:
        return (None, None, None) if with_sha else (None, None)
    except OSError as e:
        result = (None, f'{os.path.basename(path)} 无法读取：{e.strerror or e}')
        return result + (None,) if with_sha else result
    try:
        value, error = json.loads(raw.decode('utf-8-sig')), None
        sha = hashlib.sha256(raw).hexdigest()
    except (UnicodeDecodeError, ValueError) as e:
        value, error = None, f'{os.path.basename(path)} 不是有效的 JSON：{e}'
    return (value, error, sha) if with_sha else (value, error)


def inside(base, path):
    base, path = os.path.realpath(base), os.path.realpath(path)
    if not (path == base or path.startswith(base + os.sep)):
        raise KeyframeError(403, 'outside result directory')
    return path


def _positive(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def timing(auto, summary):
    """fps / frames: keyframes_auto.json first, then summary.json."""
    fps = frames = None
    for source in (auto, summary):
        if not isinstance(source, dict):
            continue
        if fps is None:
            fps = _positive(source.get('fps'))
        if frames is None and is_int(source.get('frames')) and source['frames'] > 0:
            frames = source['frames']
    return fps, frames


def source_video_name(summary, auto=None):
    """Basename of summary.source_video (server absolute path), else auto.source_video."""
    for source in (summary, auto):
        if isinstance(source, dict) and isinstance(source.get('source_video'), str) and source['source_video'].strip():
            return source['source_video'].strip().replace('\\', '/').rstrip('/').split('/')[-1] or None
    return None


def history_max(out_dir):
    folder = os.path.join(out_dir, HISTORY_DIR)
    best = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    for name in names:
        m = HISTORY_RE.match(name)
        if m:
            best = max(best, int(m.group(1)))
    return best


def load_state(out_dir):
    auto, auto_error, auto_sha = read_json(os.path.join(out_dir, AUTO_FILE), with_sha=True)
    if auto is not None and not (isinstance(auto, dict) and isinstance(auto.get('keyframes'), list)):
        auto, auto_error = None, f'{AUTO_FILE} 格式不符合约定（缺少 keyframes 列表）'
    artist, artist_error = read_json(os.path.join(out_dir, ARTIST_FILE))
    if artist is not None and not (isinstance(artist, dict) and isinstance(artist.get('keyframes'), list)):
        artist, artist_error = None, f'{ARTIST_FILE} 格式不符合约定（缺少 keyframes 列表）'
    revision = artist.get('revision') if artist is not None and is_int(artist.get('revision')) else 0
    if artist_error:
        # A damaged artist file: the client must base on the newest kept history revision.
        # (New revision numbers always continue after history either way; see save_artist.)
        revision = history_max(out_dir)
    return {
        'auto': auto, 'auto_error': auto_error,
        'auto_sha256': auto_sha if auto is not None else None,
        'artist': artist, 'artist_error': artist_error, 'revision': max(0, revision),
    }


def view(out_dir, summary, video):
    """Payload for GET .../keyframes (video is resolved by the server)."""
    state = load_state(out_dir)
    fps, frames = timing(state['auto'], summary if isinstance(summary, dict) else None)
    errors = {k: state[k + '_error'] for k in ('auto', 'artist') if state[k + '_error']}
    return {
        'auto': state['auto'], 'artist': state['artist'], 'video': video,
        'fps': fps, 'frames': frames, 'revision': state['revision'],
        'auto_sha256': state['auto_sha256'],
        'video_name': source_video_name(summary if isinstance(summary, dict) else None, state['auto']),
        'errors': errors,
    }


def _auto_index(auto):
    keys = {}
    if isinstance(auto, dict):
        for k in auto.get('keyframes') or []:
            if isinstance(k, dict) and isinstance(k.get('id'), str) and is_int(k.get('frame')):
                keys[k['id']] = k
    return keys


def _ladder_index(auto):
    ladder = auto.get('breakdown_ladder') if isinstance(auto, dict) else None
    if not isinstance(ladder, dict) or ladder.get('error') or not isinstance(ladder.get('breakdowns'), list):
        return None
    out = {}
    for b in ladder['breakdowns']:
        if isinstance(b, dict) and is_int(b.get('rank')) and is_int(b.get('frame')):
            out[b['rank']] = b
    return out


def _text(body, key, limit, label):
    value = body.get(key, '')
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise bad(f'{label}必须是文字')
    if len(value) > limit:
        raise bad(f'{label}不能超过 {limit} 字（当前 {len(value)} 字）')
    return value


def validate_artist(body, auto, frames):
    """Return the normalised artist document (without server-filled fields)."""
    if not isinstance(body, dict):
        raise bad('请求体必须是 JSON 对象')
    author = _text(body, 'author', MAX_AUTHOR, '作者').strip()
    notes = _text(body, 'notes', MAX_NOTES, '总备注')
    reviewed = body.get('reviewed', False)
    if not isinstance(reviewed, bool):
        raise bad('reviewed 必须是 true 或 false')
    keys = body.get('keyframes')
    if not isinstance(keys, list):
        raise bad('keyframes 必须是数组')
    if len(keys) > MAX_KEYS:
        raise bad(f'关键帧数量不能超过 {MAX_KEYS} 个（当前 {len(keys)} 个）')
    removed = body.get('removed_auto_ids', [])
    if removed is None:
        removed = []
    if not isinstance(removed, list):
        raise bad('removed_auto_ids 必须是数组')
    if not is_int(frames) or frames <= 0:
        raise bad('无法确定这个结果的帧数（keyframes_auto.json / summary.json 都没有 frames），不能校验帧号')
    auto_keys = _auto_index(auto)
    ladder = _ladder_index(auto)
    out, ids, by_frame, used_auto, ranks = [], set(), {}, set(), set()
    for i, k in enumerate(keys):
        where = f'第 {i + 1} 个关键帧'
        if not isinstance(k, dict):
            raise bad(f'{where}必须是对象')
        kid = k.get('id')
        if not isinstance(kid, str) or not ID_RE.match(kid):
            raise bad(f'{where}的 id 无效（只能用字母、数字、_、-，最长 32 位）')
        where = f'关键帧 {kid}'
        if kid in ids:
            raise bad(f'id {kid} 重复')
        ids.add(kid)
        frame = k.get('frame')
        if not is_int(frame):
            raise bad(f'{where}的帧号必须是整数')
        if not 0 <= frame < frames:
            raise bad(f'{where}的帧号 {frame} 超出范围 0–{frames - 1}')
        if frame in by_frame:
            raise bad(f'第 {frame} 帧同时有 {by_frame[frame]} 和 {kid}，同一帧只能有一个关键帧')
        by_frame[frame] = kid
        kind = k.get('type')
        if kind not in TYPES:
            raise bad(f'{where}的类型 {kind!r} 不在约定的 12 种类型内')
        note = _text(k, 'note', MAX_NOTE, f'{where}的备注')
        origin = k.get('origin')
        if origin not in ORIGINS:
            raise bad(f'{where}的 origin 必须是 auto、artist 或 ladder')
        entry = {'id': kid, 'frame': frame, 'type': kind, 'note': note, 'origin': origin,
                 'auto_id': None, 'auto_frame': None}
        if origin == 'auto':
            aid = k.get('auto_id')
            if not isinstance(aid, str) or not aid:
                raise bad(f'{where}来自候选（origin=auto），必须带 auto_id')
            if aid not in auto_keys:
                raise bad(f'{where}的 auto_id {aid} 在 keyframes_auto.json 里不存在（候选文件可能已重新生成）')
            if aid in used_auto:
                raise bad(f'候选 {aid} 被引用了两次')
            expected = auto_keys[aid]['frame']
            if k.get('auto_frame') is not None and k.get('auto_frame') != expected:
                raise bad(f'{where}的 auto_frame 与候选文件不一致（候选里是第 {expected} 帧）')
            used_auto.add(aid)
            entry['auto_id'], entry['auto_frame'] = aid, expected
        elif origin == 'ladder':
            rank = k.get('ladder_rank')
            if not is_int(rank) or rank < 1:
                raise bad(f'{where}来自过渡键阶梯，ladder_rank 必须是正整数')
            if rank in ranks:
                raise bad(f'过渡键名次 {rank} 出现了两次')
            ranks.add(rank)
            if k.get('auto_id') is not None:
                raise bad(f'{where}来自过渡键阶梯，auto_id 必须为 null')
            af = k.get('auto_frame')
            if ladder is not None:
                if rank not in ladder:
                    raise bad(f'过渡键阶梯里没有第 {rank} 名')
                expected = ladder[rank]['frame']
                if af is not None and af != expected:
                    raise bad(f'{where}的 auto_frame 与阶梯不一致（第 {rank} 名在第 {expected} 帧）')
                af = expected
            elif not is_int(af) or not 0 <= af < frames:
                raise bad(f'{where}的 auto_frame 必须是阶梯里原来的帧号')
            entry['auto_frame'] = af
            entry['ladder_rank'] = rank
        else:
            if k.get('auto_id') is not None or k.get('auto_frame') is not None:
                raise bad(f'{where}是美术新增（origin=artist），auto_id 和 auto_frame 必须为 null')
            if k.get('ladder_rank') is not None:
                raise bad(f'{where}是美术新增，不能带 ladder_rank')
        out.append(entry)
    seen_removed = []
    for aid in removed:
        if not isinstance(aid, str) or aid not in auto_keys:
            raise bad(f'removed_auto_ids 里的 {aid!r} 在 keyframes_auto.json 里不存在')
        if aid in used_auto:
            raise bad(f'候选 {aid} 同时出现在保留列表和删除列表里')
        if aid in seen_removed:
            raise bad(f'removed_auto_ids 里 {aid} 重复')
        seen_removed.append(aid)
    out.sort(key=lambda e: e['frame'])
    return {'author': author, 'reviewed': reviewed, 'notes': notes,
            'keyframes': out, 'removed_auto_ids': sorted(seen_removed)}


def _write_json(path, value, exclusive=False):
    folder = os.path.dirname(path)
    fd, temp = tempfile.mkstemp(dir=folder, prefix='.' + os.path.basename(path) + '-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(value, f, ensure_ascii=False, indent=1)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        if exclusive:
            # History is append-only: never replace an existing revision.
            if os.path.exists(path):
                raise KeyframeError(409, {'message': f'历史版本 {os.path.basename(path)} 已存在，请刷新后重试'})
            try:
                os.link(temp, path)
            except FileExistsError:
                raise KeyframeError(409, {'message': f'历史版本 {os.path.basename(path)} 已存在，请刷新后重试'})
            except OSError:
                os.replace(temp, path)
        else:
            os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save_artist(out_dir, body, summary=None, now=None):
    """Validate and save one revision. Raises KeyframeError (400 / 409 / 503)."""
    if not isinstance(body, dict):
        raise bad('请求体必须是 JSON 对象')
    base = body.get('base_revision')
    if not is_int(base) or base < 0:
        raise bad('base_revision 必须是不小于 0 的整数')
    with LOCK:
        state = load_state(out_dir)
        if state['auto_error']:
            raise KeyframeError(503, {'message': f'候选文件暂时无法读取（可能正在重新生成）：{state["auto_error"]}'})
        if base != state['revision']:
            raise KeyframeError(409, {
                'message': f'已有人保存了新版本 r{state["revision"]}，你的修改基于 r{base}',
                'current_revision': state['revision'], 'artist': state['artist']})
        _, frames = timing(state['auto'], summary if isinstance(summary, dict) else None)
        doc = validate_artist(body, state['auto'], frames)
        revision = max(state['revision'], history_max(out_dir)) + 1
        saved = {'schema': ARTIST_SCHEMA, 'revision': revision,
                 'saved_at': (now or datetime.now().astimezone()).isoformat(timespec='seconds'),
                 'author': doc['author'], 'base_auto_sha256': state['auto_sha256'],
                 'reviewed': doc['reviewed'], 'notes': doc['notes'],
                 'keyframes': doc['keyframes'], 'removed_auto_ids': doc['removed_auto_ids']}
        history = os.path.join(out_dir, HISTORY_DIR)
        if os.path.lexists(history) and not os.path.isdir(history):
            raise KeyframeError(500, {'message': f'{HISTORY_DIR} 不是目录'})
        os.makedirs(history, exist_ok=True)
        inside(out_dir, history)
        _write_json(inside(out_dir, os.path.join(history, f'rev{revision:04d}.json')), saved, exclusive=True)
        target = os.path.join(out_dir, ARTIST_FILE)
        inside(out_dir, os.path.dirname(target))
        _write_json(target, saved)
        return {'ok': True, 'revision': revision, 'saved_at': saved['saved_at'], 'artist': saved}
