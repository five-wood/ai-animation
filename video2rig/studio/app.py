"""video2rig 动作工坊: the user-facing web platform.

  cd /data/video2rig/code && ../.venv/bin/python -m uvicorn studio.app:app --host 0.0.0.0 --port 8700

Accounts (SQLite), a character library (existing trained projects + user uploads), a motion workbench
(reference picture, video upload, one-click generation, fit preview, keyframes, export).
Heavy work is submitted to the internal dashboard's single GPU queue (studio/engine.py); this
service never trains or solves by itself. Keyframe-only exports run Blender here (CPU).
"""
import glob
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import defaultdict

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config
from . import orient
from .db import DB, jdump, jparse
from .engine import Busy, Rejected, engine

sys.path.insert(0, config.CODE)
from web import keyframes_store  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, 'static')
NAME_RE = re.compile(r'^[A-Za-z0-9_\-\.]+$')
USER_RE = re.compile(r'^[A-Za-z0-9_\-\u4e00-\u9fff]{2,24}$')
VIDEO_EXT = ('.mp4', '.mov', '.webm', '.mkv', '.m4v')
MODEL_EXT = ('.fbx', '.glb')
TEXTURE_EXT = ('.png', '.jpg', '.jpeg', '.tga', '.tif', '.tiff', '.bmp', '.dds')
EXPORT_FORMATS = ('fbx', 'glb', 'bvh')
KEYFRAME_REQUIRED = ('Bip001', 'Bip001 Spine1', 'Bip001 Neck', 'Bip001 L Hand', 'Bip001 R Hand',
                     'Bip001 L Forearm', 'Bip001 R Forearm', 'Bip001 L Foot', 'Bip001 R Foot')
PROMPT = ('分屏画面，同一只角色被两台固定的摄影棚相机同时拍摄：左边是正面，右边是侧面。镜头完全不动，灰色摄影棚背景。'
          '角色在做：{动作描述}。整个视频画面保持干净清晰：地面一直空着，没有扬尘、碎片、特效、光效、拖影和模糊，'
          '角色的四肢、手、脚和手持物体始终完整可见。')

db = DB(config.DB_PATH)
app = FastAPI(title='video2rig studio', docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=STATIC), name='static')
if os.path.isdir(config.VENDOR):
    app.mount('/vendor', StaticFiles(directory=config.VENDOR), name='vendor')


# ============================================================================ helpers
def now():
    return time.time()


def jload(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def inside(base, path):
    base, path = os.path.realpath(base), os.path.realpath(path)
    if not (path == base or path.startswith(base + os.sep)):
        raise HTTPException(403, '不允许访问这个文件')
    return path


def clean_filename(name):
    name = os.path.basename((name or '').replace('\\', '/')).strip()
    name = ''.join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    if not name or name.startswith('.') or len(name) > 120:
        raise HTTPException(400, f'文件名不可用：{name or "(空)"}')
    return name


def clip_text(value, limit, label):
    value = str(value or '').strip()
    if len(value) > limit:
        raise HTTPException(400, f'{label}不能超过 {limit} 个字')
    return value


def save_upload(upload, dst, limit_mb):
    """Copy an UploadFile to dst. Sync on purpose: handlers using it are plain `def`, so FastAPI runs them
    in its threadpool and slow disk I/O (or antivirus scans on close) never blocks the event loop."""
    limit = limit_mb * 1024 * 1024
    size = 0
    tmp = dst + '.part'
    try:
        upload.file.seek(0)
        with open(tmp, 'wb') as f:
            while True:
                chunk = upload.file.read(1 << 22)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f'文件太大，最大 {limit_mb} MB')
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, '文件是空的')
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return size


def download_name(title, ext):
    base = re.sub(r'[\\/:*?"<>|\s]+', '_', title or 'animation').strip('_') or 'animation'
    return f'{base[:60]}.{ext}'


# ============================================================================ auth
COOKIE = 'studio_session'
FAILS = defaultdict(list)
FAILS_LOCK = threading.Lock()


def public_user(u):
    return {'id': u['id'], 'username': u['username'], 'role': u['role']} if u else None


def current_user(request: Request):
    u = db.session_user(request.cookies.get(COOKIE))
    if not u:
        raise HTTPException(401, '请先登录')
    return u


def admin_user(u=Depends(current_user)):
    if u['role'] != 'admin':
        raise HTTPException(403, '只有管理员可以操作')
    return u


class CsrfGuard:
    """Pure ASGI middleware (does not wrap the request body, so large uploads stream untouched).
    Mutating API calls must carry X-Studio: 1, which a cross-site form/page cannot send without CORS.
    API responses default to Cache-Control: no-store."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/'):
            return await self.inner(scope, receive, send)
        headers = dict(scope.get('headers') or [])
        if scope['method'] not in ('GET', 'HEAD', 'OPTIONS') and headers.get(b'x-studio') != b'1':
            return await JSONResponse({'detail': '请求来源无效'}, status_code=403)(scope, receive, send)

        async def send_wrapper(message):
            if message['type'] == 'http.response.start':
                hs = list(message.get('headers') or [])
                if not any(k.lower() == b'cache-control' for k, _ in hs):
                    hs.append((b'cache-control', b'no-store'))
                message = {**message, 'headers': hs}
            await send(message)

        return await self.inner(scope, receive, send_wrapper)


app.add_middleware(CsrfGuard)


def throttle(key):
    with FAILS_LOCK:
        recent = [t for t in FAILS[key] if t > now() - 600]
        FAILS[key] = recent
        if len(recent) >= 8:
            raise HTTPException(429, '尝试次数太多，请 10 分钟后再试')


def set_session(response, user_id):
    token = db.new_session(user_id)
    response.set_cookie(COOKIE, token, max_age=14 * 86400, httponly=True, samesite='lax',
                        secure=config.COOKIE_SECURE, path='/')


@app.on_event('startup')
def ensure_admin():
    if config.ADMIN_USER and config.ADMIN_PASSWORD:
        u = db.one('SELECT * FROM users WHERE username = ?', config.ADMIN_USER)
        if not u:
            db.create_user(config.ADMIN_USER, config.ADMIN_PASSWORD, role='admin')
        elif u['role'] != 'admin':
            db.run("UPDATE users SET role = 'admin' WHERE id = ?", u['id'])


@app.get('/api/config')
def site_config():
    return {'signup': config.OPEN_SIGNUP, 'invite_required': bool(config.INVITE_CODE), 'prompt': PROMPT,
            'max_video_mb': config.MAX_VIDEO_MB, 'max_model_mb': config.MAX_MODEL_MB}


@app.post('/api/auth/register')
def register(body: dict, request: Request):
    if not config.OPEN_SIGNUP:
        raise HTTPException(403, '暂未开放注册，请联系管理员开通账号')
    username = str(body.get('username') or '').strip()
    password = str(body.get('password') or '')
    if not USER_RE.match(username):
        raise HTTPException(400, '用户名需要 2–24 个字，可以用中文、字母、数字、_ 和 -')
    if len(password) < 6 or len(password) > 128:
        raise HTTPException(400, '密码至少 6 位')
    if config.INVITE_CODE and not secrets.compare_digest(str(body.get('invite') or ''), config.INVITE_CODE):
        throttle('invite:' + (request.client.host if request.client else ''))
        with FAILS_LOCK:
            FAILS['invite:' + (request.client.host if request.client else '')].append(now())
        raise HTTPException(400, '邀请码不正确')
    if db.one('SELECT id FROM users WHERE username = ?', username):
        raise HTTPException(409, '这个用户名已经有人用了')
    first = db.one('SELECT COUNT(*) AS n FROM users')['n'] == 0
    uid = db.create_user(username, password, role='admin' if first else 'user')
    response = JSONResponse({'user': public_user(db.one('SELECT * FROM users WHERE id = ?', uid))})
    set_session(response, uid)
    return response


@app.post('/api/auth/login')
def login(body: dict, request: Request):
    username = str(body.get('username') or '').strip()
    key = (request.client.host if request.client else '') + ':' + username.lower()
    throttle(key)
    u = db.check_login(username, str(body.get('password') or ''))
    if not u:
        with FAILS_LOCK:
            FAILS[key].append(now())
        raise HTTPException(400, '用户名或密码不正确')
    if u['disabled']:
        raise HTTPException(403, '这个账号已被停用，请联系管理员')
    response = JSONResponse({'user': public_user(u)})
    set_session(response, u['id'])
    return response


@app.post('/api/auth/logout')
def logout(request: Request):
    db.end_session(request.cookies.get(COOKIE))
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE, path='/')
    return response


@app.get('/api/auth/me')
def me(request: Request):
    return {'user': public_user(db.session_user(request.cookies.get(COOKIE)))}


@app.post('/api/auth/password')
def change_password(body: dict, request: Request, u=Depends(current_user)):
    if not db.check_login(u['username'], str(body.get('old') or '')):
        raise HTTPException(400, '原密码不正确')
    new = str(body.get('new') or '')
    if len(new) < 6:
        raise HTTPException(400, '新密码至少 6 位')
    db.set_password(u['id'], new)
    response = JSONResponse({'ok': True})
    set_session(response, u['id'])
    return response


# ============================================================================ characters
CHAR_STATUS = {'uploaded': '未制作', 'pending_review': '等待审核', 'preparing': '制作中',
               'ready': '可用', 'failed': '制作失败'}


def project_dir(project):
    if not project or not NAME_RE.fullmatch(project):
        return None
    return os.path.join(config.PROJ, project)


def project_ready(project):
    d = project_dir(project)
    return bool(d) and all(os.path.isfile(os.path.join(d, f)) for f in
                           ('rig.json', 'studio.json', 'reference_two_views.png', os.path.join('model', 'best.pt')))


def char_model_path(c):
    if c['model_path'] and os.path.isfile(c['model_path']):
        return c['model_path']
    d = project_dir(c['project'])
    if d:
        rig = jload(os.path.join(d, 'rig.json'), {}) or {}
        m = rig.get('model')
        if m and os.path.isfile(m):
            return m
    return None


def inspected_glb(c):
    """Server-side preview of an uploaded model (Blender import, same as prepare), if available."""
    if c['files_dir'] and c['inspect_status'] == 'done':
        f = os.path.join(c['files_dir'], 'inspect.glb')
        if os.path.isfile(f):
            return f
    return None


def char_preview(c):
    """What the browser can show for a character: an FBX/GLB model or the sample animation."""
    if inspected_glb(c):
        return {'kind': 'glb', 'url': f'/api/characters/{c["id"]}/inspect.glb?v={int(c["updated"])}', 'inspected': True}
    m = char_model_path(c)
    if m and m.lower().endswith(MODEL_EXT):
        return {'kind': os.path.splitext(m)[1][1:].lower(),
                'url': f'/api/characters/{c["id"]}/asset/{os.path.basename(m)}'}
    d = project_dir(c['project'])
    if d and os.path.isfile(os.path.join(d, 'out', 'exam', 'exam.glb')):
        return {'kind': 'glb', 'url': f'/api/characters/{c["id"]}/sample.glb'}
    return None


def thumb_file(c):
    cands = []
    if c['files_dir']:
        cands.append(os.path.join(c['files_dir'], 'thumb.png'))
    d = project_dir(c['project'])
    if d:
        cands += [os.path.join(d, 'ref_front.png'), os.path.join(d, 'reference_two_views.png')]
    return next((p for p in cands if os.path.isfile(p)), None)


def can_see_char(c, u):
    return c['published'] or c['owner_id'] == u['id'] or u['role'] == 'admin'


def get_char(cid, u, need_ready=False):
    c = db.one('SELECT * FROM characters WHERE id = ?', cid)
    if not c or not can_see_char(c, u):
        raise HTTPException(404, '找不到这个角色')
    if need_ready and c['status'] != 'ready':
        raise HTTPException(400, '这个角色还不能用来制作动作')
    return c


def char_view(c, u, detail=False):
    v = {'id': c['id'], 'name': c['name'], 'status': c['status'], 'status_text': CHAR_STATUS.get(c['status'], c['status']),
         'published': bool(c['published']), 'mine': c['owner_id'] == u['id'], 'created': c['created'],
         'thumb': f'/api/characters/{c["id"]}/thumb?v={int(c["updated"])}' if thumb_file(c) else None,
         'has_reference': bool(project_dir(c['project'])) and os.path.isfile(
             os.path.join(project_dir(c['project']), 'reference_two_views.png'))}
    if c['status'] in ('preparing', 'failed', 'pending_review'):
        v['progress'] = prepare_progress(c)
    if c['pending_project']:   # still usable; a new version is being trained in the background
        v['remake'] = prepare_progress(c, c['pending_project'])
    elif c['remake_error'] and (c['owner_id'] == u['id'] or u['role'] == 'admin'):
        v['remake_failed'] = True
    if detail:
        v['preview'] = char_preview(c)
        v['works'] = db.one('SELECT COUNT(*) AS n FROM works WHERE character_id = ? AND user_id = ? AND deleted = 0',
                            c['id'], u['id'])['n']
        v['error'] = friendly_prepare_error(c) if c['status'] == 'failed' else None
        if c['files_dir']:   # uploaded by a user: orientation can be fixed before preparing
            rep = jparse(c['inspect'], {}) or {}
            v['orient'] = jparse(c['orient'], None)
            v['inspect'] = {'status': c['inspect_status'], 'flat_in_z': rep.get('flat_in_z'), 'extent': rep.get('extent'),
                            'bones': rep.get('bones'), 'error': INSPECT_ERRORS.get(rep.get('error'), rep.get('error'))}
            v['orient_editable'] = c['status'] in ('uploaded', 'failed') and (c['owner_id'] == u['id'] or u['role'] == 'admin')
    return v


INSPECT_ERRORS = {'NO_ARMATURE': '这个模型没有骨骼，无法制作动作。请上传带骨骼绑定的 FBX。',
                  'NO_SKINNED_MESH': '模型没有绑定到骨骼上的网格（蒙皮），无法制作动作。',
                  'IMPORT_FAILED': '模型文件无法读取，可能已损坏或格式不受支持。'}


@app.get('/api/characters')
def list_characters(scope: str = 'all', u=Depends(current_user)):
    if scope == 'mine':
        rows = db.all('SELECT * FROM characters WHERE owner_id = ? ORDER BY created DESC', u['id'])
    else:
        rows = db.all("SELECT * FROM characters WHERE published = 1 AND status = 'ready' ORDER BY created DESC")
    return [char_view(c, u) for c in rows]


@app.get('/api/characters/{cid}')
def character(cid: int, u=Depends(current_user)):
    return char_view(get_char(cid, u), u, detail=True)


@app.get('/api/characters/{cid}/thumb')
def character_thumb(cid: int, u=Depends(current_user)):
    f = thumb_file(get_char(cid, u))
    if not f:
        raise HTTPException(404, '没有缩略图')
    return FileResponse(f, headers={'Cache-Control': 'private, max-age=86400'})


@app.post('/api/characters/{cid}/thumb')
def upload_thumb(cid: int, file: UploadFile = File(...), u=Depends(current_user)):
    c = get_char(cid, u)
    if c['owner_id'] != u['id'] or not c['files_dir']:
        raise HTTPException(403, '只能给自己上传的角色设置缩略图')
    save_upload(file, os.path.join(c['files_dir'], 'thumb.png'), 5)
    db.update('characters', cid)
    return {'ok': True}


@app.get('/api/characters/{cid}/reference')
def character_reference(cid: int, download: int = 0, u=Depends(current_user)):
    c = get_char(cid, u)
    d = project_dir(c['project'])
    f = os.path.join(d, 'reference_two_views.png') if d else None
    if not f or not os.path.isfile(f):
        raise HTTPException(404, '这个角色还没有参考图')
    return FileResponse(f, filename=download_name(c['name'] + '_参考图', 'png') if download else None)


@app.get('/api/characters/{cid}/asset/{name:path}')
def character_asset(cid: int, name: str, u=Depends(current_user)):
    c = get_char(cid, u)
    m = char_model_path(c)
    if not m:
        raise HTTPException(404, '模型文件不存在')
    base = os.path.basename(name.replace('\\', '/'))
    folder = os.path.dirname(m)
    if base != os.path.basename(m) and not base.lower().endswith(TEXTURE_EXT):
        raise HTTPException(404, '文件不存在')
    f = inside(folder, os.path.join(folder, base))
    if not os.path.isfile(f):
        raise HTTPException(404, '文件不存在')
    return FileResponse(f, headers={'Cache-Control': 'private, max-age=3600'})


@app.get('/api/characters/{cid}/inspect.glb')
def character_inspected(cid: int, u=Depends(current_user)):
    f = inspected_glb(get_char(cid, u))
    if not f:
        raise HTTPException(404, '预览还没准备好')
    return FileResponse(f, media_type='model/gltf-binary', headers={'Cache-Control': 'private, max-age=3600'})


@app.put('/api/characters/{cid}/orient')
def set_orient(cid: int, body: dict, u=Depends(current_user)):
    """Save the user's 90-degree orientation fix (3x3, three.js coordinates) -> prepare --up/--facing."""
    c = get_char(cid, u)
    if not c['files_dir'] or (c['owner_id'] != u['id'] and u['role'] != 'admin'):
        raise HTTPException(403, '只能调整自己上传的角色')
    if c['status'] not in ('uploaded', 'failed'):
        raise HTTPException(400, '角色已经提交制作，不能再调整朝向')
    try:
        o = orient.solve(body.get('matrix'))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e) or '朝向数据无效')
    db.update('characters', cid, orient=jdump(o))
    return {'ok': True, 'orient': o}


@app.get('/api/characters/{cid}/sample.glb')
def character_sample(cid: int, u=Depends(current_user)):
    c = get_char(cid, u)
    d = project_dir(c['project'])
    f = os.path.join(d, 'out', 'exam', 'exam.glb') if d else None
    if not f or not os.path.isfile(f):
        raise HTTPException(404, '没有示例动画')
    return FileResponse(f, media_type='model/gltf-binary', headers={'Cache-Control': 'private, max-age=3600'})


@app.post('/api/characters')
def upload_character(name: str = Form(...), files: list[UploadFile] = File(...), u=Depends(current_user)):
    name = clip_text(name, 40, '角色名')
    if not name:
        raise HTTPException(400, '请给角色起个名字')
    models = [f for f in files if (f.filename or '').lower().endswith(MODEL_EXT)]
    if len(models) != 1:
        raise HTTPException(400, '请上传 1 个 FBX 模型文件（贴图可以一起选上）')
    for f in files:
        if f not in models and not (f.filename or '').lower().endswith(TEXTURE_EXT):
            raise HTTPException(400, f'不支持的文件类型：{f.filename}（只接受 FBX 和贴图图片）')
    if len(files) > 40:
        raise HTTPException(400, '一次最多上传 40 个文件')
    folder = os.path.join(config.UPLOAD_MODELS, f'u{u["id"]}_{uuid.uuid4().hex[:12]}')
    os.makedirs(folder)
    try:
        ext = os.path.splitext(models[0].filename)[1].lower()
        model_path = os.path.join(folder, 'model' + ext)
        save_upload(models[0], model_path, config.MAX_MODEL_MB)
        for f in files:
            if f is not models[0]:
                save_upload(f, os.path.join(folder, clean_filename(f.filename)), 50)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    t = now()
    # inspect_status 'waiting': the worker loads it in Blender (as prepare will) to build the preview.
    cid = db.run('INSERT INTO characters (name, model_path, files_dir, owner_id, status, published, inspect_status, created, updated) '
                 "VALUES (?, ?, ?, ?, 'uploaded', 0, 'waiting', ?, ?)", name, model_path, folder, u['id'], t, t)
    return char_view(db.one('SELECT * FROM characters WHERE id = ?', cid), u, detail=True)


@app.post('/api/characters/{cid}/prepare')
def request_prepare(cid: int, u=Depends(current_user)):
    c = get_char(cid, u)
    if c['owner_id'] != u['id'] and u['role'] != 'admin':
        raise HTTPException(403, '只能制作自己上传的角色')
    if c['status'] == 'ready' and c['files_dir']:
        # Re-make of an uploaded character that already works.
        if c['pending_project']:
            raise HTTPException(400, '这个角色正在重新制作，请等它完成')
        if u['role'] != 'admin' and os.environ.get('STUDIO_PREPARE_APPROVAL', '1') == '1':
            raise HTTPException(403, '重新制作需要管理员操作，请联系管理员')
        start_remake(c)
        return char_view(db.one('SELECT * FROM characters WHERE id = ?', cid), u, detail=True)
    if c['status'] not in ('uploaded', 'failed'):
        raise HTTPException(400, '这个角色已经在制作或已经可用')
    if c['inspect_status'] == 'failed':
        rep = jparse(c['inspect'], {}) or {}
        raise HTTPException(400, INSPECT_ERRORS.get(rep.get('error'), '模型检查没有通过，无法制作'))
    busy = db.one("SELECT COUNT(*) AS n FROM characters WHERE owner_id = ? AND status IN ('pending_review', 'preparing')",
                  u['id'])['n']
    if busy >= config.MAX_PREPARING_PER_USER and u['role'] != 'admin':
        raise HTTPException(429, '你已经有角色在制作中，请等它完成后再提交')
    if u['role'] == 'admin' or os.environ.get('STUDIO_PREPARE_APPROVAL', '1') != '1':
        start_prepare(c)
    else:
        db.update('characters', cid, status='pending_review', error=None)
    return char_view(db.one('SELECT * FROM characters WHERE id = ?', cid), u, detail=True)


def new_project_name(cid):
    for _ in range(20):
        p = f'st{cid}_{uuid.uuid4().hex[:6]}'
        if not os.path.exists(os.path.join(config.PROJ, p)):
            return p
    raise HTTPException(500, '无法分配项目名')


def start_prepare(c):
    """(Re)start making a character. The dashboard refuses a pipeline into a project that already holds a rig or a
    trained reader (409), and run_all.sh would skip prepare there anyway - so a re-make always gets a fresh project.
    The old project is kept untouched (other users' works and exports still point at it)."""
    project = c['project']
    if not project or project_has_training(project):
        old = jparse(c['prev_projects'], []) or []
        if project:
            old.append(project)
        project = new_project_name(c['id'])
        db.update('characters', c['id'], prev_projects=jdump(old))
    db.update('characters', c['id'], status='preparing', project=project, remote_job=None, error=None)


def start_remake(c):
    """Re-make a character that is already usable: train into a NEW project in the background; the character keeps
    working on its current project until the new one is ready, then switches (old project kept on disk)."""
    db.update('characters', c['id'], pending_project=new_project_name(c['id']), remote_job=None, remake_error=None)


def project_has_training(project):
    d = project_dir(project)
    return bool(d) and (os.path.exists(os.path.join(d, 'rig.json')) or os.path.exists(os.path.join(d, 'model', 'best.pt'))
                        or bool(glob.glob(os.path.join(d, 'train_data', 'meta_*.json'))))


@app.patch('/api/characters/{cid}')
def rename_character(cid: int, body: dict, u=Depends(current_user)):
    c = get_char(cid, u)
    if c['owner_id'] != u['id'] and u['role'] != 'admin':
        raise HTTPException(403, '只能修改自己上传的角色')
    name = clip_text(body.get('name'), 40, '角色名')
    if not name:
        raise HTTPException(400, '角色名不能为空')
    db.update('characters', cid, name=name)
    return {'ok': True}


@app.delete('/api/characters/{cid}')
def delete_character(cid: int, u=Depends(current_user)):
    c = get_char(cid, u)
    if c['owner_id'] != u['id']:
        raise HTTPException(403, '只能删除自己上传的角色')
    if c['status'] in ('preparing', 'ready', 'pending_review'):
        raise HTTPException(400, '已提交制作或已可用的角色不能删除，请联系管理员')
    db.run('DELETE FROM characters WHERE id = ?', cid)
    if c['files_dir']:
        shutil.rmtree(inside(config.UPLOAD_MODELS, c['files_dir']), ignore_errors=True)
    return {'ok': True}


def prepare_progress(c, project=None):
    """{percent, text} for a character being prepared (from project files, no technical detail)."""
    if project is None:
        if c['status'] == 'pending_review':
            return {'percent': 0, 'text': '已提交，等待管理员审核'}
        if c['status'] == 'failed':
            return {'percent': 0, 'text': '制作失败'}
    if not c['remote_job']:
        return {'percent': 1, 'text': '排队中'}
    st = engine().status(c['remote_job'])
    if st['status'] == 'queued':
        return {'percent': 2, 'text': '排队中'}
    d = project_dir(project or c['project'])
    if not d or not os.path.isfile(os.path.join(d, 'rig.json')):
        return {'percent': 5, 'text': '分析骨骼'}
    if os.path.isfile(os.path.join(d, 'model', 'best.pt')) and os.path.isfile(os.path.join(d, 'exam.log')):
        return {'percent': 95, 'text': '自我检验'}
    train = os.path.join(d, 'train.log')
    if os.path.isfile(train):
        from .engine import tail_text
        eps = re.findall(r'ep (\d+) step', tail_text(train, 4000))
        ep = int(eps[-1]) if eps else 0
        return {'percent': 35 + min(55, int(55 * (ep + 1) / 20)), 'text': '学习角色外观'}
    td = os.path.join(d, 'train_data')
    n = len(os.listdir(td)) if os.path.isdir(td) else 0
    return {'percent': 10 + min(25, n // 250), 'text': '生成练习姿势'}


def friendly_prepare_error(c):
    e = c['error'] or ''
    if 'NO_ARMATURE' in e:
        return '这个模型没有骨骼，无法制作动作。请上传带骨骼绑定的 FBX。'
    return '角色制作没有成功。请确认模型带骨骼、贴图齐全后重试，或联系管理员。'


# ============================================================================ videos
def video_info(path):
    try:
        import cv2
    except ImportError:
        return {}
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return {'error': '无法读取'}
        return {'fps': cap.get(cv2.CAP_PROP_FPS) or 0, 'frames': int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
                'width': int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0), 'height': int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)}
    finally:
        cap.release()


@app.post('/api/characters/{cid}/videos')
def upload_video(cid: int, file: UploadFile = File(...), u=Depends(current_user)):
    c = get_char(cid, u, need_ready=True)
    orig = clean_filename(file.filename)
    ext = os.path.splitext(orig)[1].lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, '请上传 MP4 / MOV / WEBM 视频')
    folder = os.path.join(config.UPLOAD_VIDEOS, f'u{u["id"]}')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'v{uuid.uuid4().hex[:16]}{ext}')
    size = save_upload(file, path, config.MAX_VIDEO_MB)
    info = video_info(path)
    warnings = []
    if info.get('error'):
        os.unlink(path)
        raise HTTPException(400, '这个视频无法读取，请换一个 MP4 文件')
    if info.get('width') and info.get('height') and info['width'] / info['height'] < 1.4:
        warnings.append('画面比较窄，看起来不像左右分屏的双视角视频。请确认左边是正面、右边是侧面。')
    if info.get('frames') and info.get('fps') and info['frames'] / info['fps'] > 20:
        warnings.append('视频超过 20 秒，生成会比较慢，建议只保留需要的动作片段。')
    if info.get('frames') and info['frames'] < 12:
        os.unlink(path)
        raise HTTPException(400, '视频太短了，至少需要半秒的动作')
    vid = db.run('INSERT INTO videos (user_id, character_id, file, orig_name, size, info, created) VALUES (?, ?, ?, ?, ?, ?, ?)',
                 u['id'], c['id'], path, orig, size, jdump(info), now())
    return {'id': vid, 'name': orig, 'size': size, 'info': info, 'warnings': warnings,
            'url': f'/api/videos/{vid}/file'}


@app.get('/api/videos/{vid}/file')
def video_file(vid: int, u=Depends(current_user)):
    v = db.one('SELECT * FROM videos WHERE id = ?', vid)
    if not v or (v['user_id'] != u['id'] and u['role'] != 'admin') or not os.path.isfile(v['file']):
        raise HTTPException(404, '视频不存在')
    return FileResponse(v['file'], headers={'Cache-Control': 'private, max-age=3600'})


# ============================================================================ works
WORK_TEXT = {'waiting': '排队中', 'queued': '排队中', 'running': '生成中', 'ready': '已完成',
             'needs_review': '需要换个视频', 'failed': '生成失败', 'cancelled': '已取消'}
ACTIVE = ('waiting', 'queued', 'running')


def get_work(wid, u):
    w = db.one('SELECT * FROM works WHERE id = ? AND deleted = 0', wid)
    if not w or (w['user_id'] != u['id'] and u['role'] != 'admin'):
        raise HTTPException(404, '找不到这个作品')
    return w


def work_dir(w):
    d = project_dir(w['project'])
    if not d or not NAME_RE.fullmatch(w['result_name']):
        raise HTTPException(404, '作品文件不存在')
    return os.path.join(d, 'out', w['result_name'])


def work_files(w):
    try:
        o = work_dir(w)
    except HTTPException:
        return {}
    r = w['result_name']
    out = {}
    if w['status'] == 'ready':
        for fmt in EXPORT_FORMATS:
            if os.path.isfile(os.path.join(o, f'{r}.{fmt}')):
                out[fmt] = True
        out['compare'] = os.path.isfile(os.path.join(o, 'lab.mp4'))
    return out


def animate_progress(w):
    if w['status'] == 'waiting':
        return {'percent': 1, 'text': '排队中'}
    if not w['remote_job']:
        return None
    st = engine().status(w['remote_job'])
    if st['status'] == 'queued':
        ahead = queue_ahead(w['remote_job'])
        return {'percent': 2, 'text': f'排队中，前面还有 {ahead} 个任务' if ahead else '排队中，马上开始'}
    log = st['log']
    steps = [('done in', 100, '完成'), ('exported', 92, '生成对比视频'), ('solved in', 82, '导出动画文件'),
             ('temporal it', 74, '让动作更顺滑'), ('solve stage 2', 70, '拟合骨骼动作'), ('solve stage 1', 60, '拟合骨骼动作'),
             ('solve stage 0', 48, '拟合骨骼动作'), ('check:', 40, '检查两个画面是否一致'),
             ('joints read', 30, '识别关节位置'), ('[v2r] video', 12, '读取视频')]
    for key, pct, text in steps:
        if key in log:
            return {'percent': pct, 'text': text}
    return {'percent': 5, 'text': '准备开始'}


_QUEUE_CACHE = {'t': 0, 'rows': []}


def queue_ahead(job_id):
    if config.ENGINE != 'v2r':
        return 0
    if now() - _QUEUE_CACHE['t'] > 5:
        rows = []
        try:
            for fn in os.listdir(config.JOBS_DIR):
                if fn.endswith('.json'):
                    j = jload(os.path.join(config.JOBS_DIR, fn), {}) or {}
                    if j.get('status') in ('queued', 'running'):
                        rows.append((j.get('created') or 0, j.get('id')))
        except OSError:
            pass
        _QUEUE_CACHE.update(t=now(), rows=sorted(rows))
    ids = [r[1] for r in _QUEUE_CACHE['rows']]
    return ids.index(job_id) if job_id in ids else 0


def work_view(w, detail=False):
    c = db.one('SELECT id, name FROM characters WHERE id = ?', w['character_id']) or {'id': None, 'name': '(已删除)'}
    v = {'id': w['id'], 'title': w['title'], 'status': w['status'], 'status_text': WORK_TEXT.get(w['status'], w['status']),
         'character': {'id': c['id'], 'name': c['name']}, 'created': w['created'], 'updated': w['updated'],
         'forced': bool(w['forced']), 'kf_status': w['kf_status'], 'thumb': None,
         'video': f'/api/videos/{w["video_id"]}/file'}
    if w['status'] == 'ready':
        try:
            o = work_dir(w)
            if os.path.isfile(os.path.join(o, 'preview_frames', '00000_front.png')):
                v['thumb'] = f'/api/works/{w["id"]}/thumb'
        except HTTPException:
            pass
    if w['status'] in ACTIVE:
        v['progress'] = animate_progress(w)
    if w['status'] in ('failed', 'needs_review'):
        v['message'] = friendly_work_error(w)
    if detail:
        v['files'] = work_files(w)
        summary = {}
        try:
            summary = jload(os.path.join(work_dir(w), 'summary.json'), {}) or {}
        except HTTPException:
            pass
        v['fps'] = summary.get('fps')
        v['frames'] = summary.get('frames')
        v['kf_error'] = w['kf_error']
    return v


def friendly_work_error(w):
    if w['status'] == 'needs_review':
        return ('视频里左右两个画面的动作对不上（可能有遮挡、特效、镜头移动，或左右画面放反了）。'
                '建议用参考图重新生成一段干净的视频；也可以先“仍然生成预览”看看效果。')
    e = w['error'] or ''
    if 'interrupted' in e:
        return '服务器重启打断了这次生成，请点“重试”。'
    if 'cancel' in e:
        return '这次生成已取消。'
    return '生成没有成功。请确认上传的是左右分屏（左正面、右侧面）的视频后重试。'


@app.get('/api/works')
def list_works(character: int = 0, u=Depends(current_user)):
    if character:
        rows = db.all('SELECT * FROM works WHERE user_id = ? AND character_id = ? AND deleted = 0 ORDER BY created DESC',
                      u['id'], character)
    else:
        rows = db.all('SELECT * FROM works WHERE user_id = ? AND deleted = 0 ORDER BY created DESC LIMIT 200', u['id'])
    return [work_view(w) for w in rows]


@app.post('/api/works')
def create_work(body: dict, u=Depends(current_user)):
    try:
        cid, vid = int(body.get('character_id')), int(body.get('video_id'))
    except (TypeError, ValueError):
        raise HTTPException(400, '请先选择角色并上传视频')
    c = get_char(cid, u, need_ready=True)
    v = db.one('SELECT * FROM videos WHERE id = ? AND user_id = ?', vid, u['id'])
    if not v or not os.path.isfile(v['file']):
        raise HTTPException(400, '视频不存在，请重新上传')
    active = db.one(f"SELECT COUNT(*) AS n FROM works WHERE user_id = ? AND deleted = 0 AND status IN {ACTIVE}", u['id'])['n']
    if active >= config.MAX_ACTIVE_WORKS_PER_USER and u['role'] != 'admin':
        raise HTTPException(429, f'你已经有 {active} 个动画在生成中，请等它们完成后再提交')
    title = clip_text(body.get('title') or os.path.splitext(v['orig_name'])[0], 40, '作品名') or '新动作'
    t = now()
    # Inserted as 'creating' so the worker never submits it before the final result name (needs the id) is set.
    wid = db.run('INSERT INTO works (user_id, character_id, video_id, title, project, result_name, status, created, updated) '
                 "VALUES (?, ?, ?, ?, ?, ?, 'creating', ?, ?)", u['id'], cid, vid, title, c['project'],
                 f'tmp_{uuid.uuid4().hex}', t, t)
    db.update('works', wid, result_name=f'st_w{wid}_{uuid.uuid4().hex[:6]}', status='waiting')
    return work_view(db.one('SELECT * FROM works WHERE id = ?', wid), detail=True)


@app.get('/api/works/{wid}')
def work(wid: int, u=Depends(current_user)):
    return work_view(get_work(wid, u), detail=True)


@app.patch('/api/works/{wid}')
def rename_work(wid: int, body: dict, u=Depends(current_user)):
    get_work(wid, u)
    title = clip_text(body.get('title'), 40, '作品名')
    if not title:
        raise HTTPException(400, '作品名不能为空')
    db.update('works', wid, title=title)
    return {'ok': True}


@app.post('/api/works/{wid}/cancel')
def cancel_work(wid: int, u=Depends(current_user)):
    w = get_work(wid, u)
    if w['status'] not in ACTIVE:
        raise HTTPException(400, '这个作品没有在生成')
    if w['remote_job']:
        engine().cancel(w['remote_job'])
    db.update('works', wid, status='cancelled', error='cancelled by user')
    return work_view(db.one('SELECT * FROM works WHERE id = ?', wid), detail=True)


@app.post('/api/works/{wid}/retry')
def retry_work(wid: int, body: dict, u=Depends(current_user)):
    w = get_work(wid, u)
    if w['status'] not in ('failed', 'needs_review', 'cancelled'):
        raise HTTPException(400, '只有失败或取消的作品可以重试')
    force = body.get('force') is True
    db.update('works', wid, status='waiting', remote_job=None, error=None, stage=None, forced=1 if force else 0,
              result_name=f'st_w{wid}_{uuid.uuid4().hex[:6]}', kf_status='none', kf_job=None, kf_error=None)
    return work_view(db.one('SELECT * FROM works WHERE id = ?', wid), detail=True)


@app.delete('/api/works/{wid}')
def delete_work(wid: int, u=Depends(current_user)):
    w = get_work(wid, u)
    if w['status'] in ACTIVE and w['remote_job']:
        engine().cancel(w['remote_job'])
    db.update('works', wid, deleted=1)
    return {'ok': True}


PREVIEW_FILES = {'model.glb': None, 'compare.mp4': 'lab.mp4'}


@app.get('/api/works/{wid}/media/{name}')
def work_media(wid: int, name: str, u=Depends(current_user)):
    w = get_work(wid, u)
    if w['status'] != 'ready' or name not in PREVIEW_FILES:
        raise HTTPException(404, '文件不存在')
    o = work_dir(w)
    fn = PREVIEW_FILES[name] or f'{w["result_name"]}.glb'
    f = inside(o, os.path.join(o, fn))
    if not os.path.isfile(f):
        raise HTTPException(404, '文件不存在')
    return FileResponse(f, media_type='model/gltf-binary' if fn.endswith('.glb') else None,
                        headers={'Cache-Control': 'private, max-age=3600'})


@app.get('/api/works/{wid}/thumb')
def work_thumb(wid: int, u=Depends(current_user)):
    w = get_work(wid, u)
    f = os.path.join(work_dir(w), 'preview_frames', '00000_front.png')
    if not os.path.isfile(f):
        raise HTTPException(404)
    return FileResponse(f, headers={'Cache-Control': 'private, max-age=86400'})


@app.get('/api/works/{wid}/download/{fmt}')
def work_download(wid: int, fmt: str, u=Depends(current_user)):
    w = get_work(wid, u)
    if fmt not in EXPORT_FORMATS or w['status'] != 'ready':
        raise HTTPException(404, '文件不存在')
    o = work_dir(w)
    f = inside(o, os.path.join(o, f'{w["result_name"]}.{fmt}'))
    if not os.path.isfile(f):
        raise HTTPException(404, '这个格式暂时没有生成')
    return FileResponse(f, filename=download_name(w['title'], fmt))


# ---------------------------------------------------------------------------- keyframes
def keyframe_route(w):
    """'v2r' when the Biped candidate extractor applies to this result, else 'local' (generic)."""
    o = work_dir(w)
    rig = jload(os.path.join(project_dir(w['project']), 'rig.json'), {}) or {}
    names = {k.get('name') for k in rig.get('keypoints', []) if isinstance(k, dict)}
    if (config.ENGINE == 'fake' or os.path.isfile(config.KEYFRAME_SCRIPT)) and all(n in names for n in KEYFRAME_REQUIRED) \
            and os.path.isfile(os.path.join(o, 'reading.npz')):
        return 'v2r'
    return 'local'


@app.get('/api/works/{wid}/keyframes')
def work_keyframes(wid: int, u=Depends(current_user)):
    w = get_work(wid, u)
    if w['status'] != 'ready':
        raise HTTPException(400, '动画生成完成后才能标记关键帧')
    o = work_dir(w)
    state = keyframes_store.view(o, jload(os.path.join(o, 'summary.json'), {}), None)
    state.pop('video_name', None)
    state['kf_status'] = w['kf_status']
    return state


@app.put('/api/works/{wid}/keyframes')
def save_work_keyframes(wid: int, body: dict, u=Depends(current_user)):
    w = get_work(wid, u)
    if w['status'] != 'ready':
        raise HTTPException(400, '动画生成完成后才能标记关键帧')
    o = work_dir(w)
    body = dict(body)
    body['author'] = u['username'][:40]
    try:
        return keyframes_store.save_artist(o, body, jload(os.path.join(o, 'summary.json'), {}))
    except keyframes_store.KeyframeError as e:
        raise HTTPException(e.status, e.detail)


@app.post('/api/works/{wid}/keyframes/auto')
def auto_keyframes(wid: int, u=Depends(current_user)):
    w = get_work(wid, u)
    if w['status'] != 'ready':
        raise HTTPException(400, '动画生成完成后才能自动标记')
    if w['kf_status'] in ('waiting', 'running'):
        raise HTTPException(400, '正在自动标记，请稍等')
    db.update('works', wid, kf_status='waiting', kf_job=None, kf_error=None)
    return {'ok': True, 'kf_status': 'waiting'}


# ---------------------------------------------------------------------------- exports
def export_view(e):
    return {'id': e['id'], 'kind': e['kind'], 'options': jparse(e['options'], {}), 'status': e['status'],
            'error': e['error'], 'created': e['created'],
            'url': f'/api/exports/{e["id"]}/download' if e['status'] == 'done' else None}


@app.get('/api/works/{wid}/exports')
def list_exports(wid: int, u=Depends(current_user)):
    get_work(wid, u)
    return [export_view(e) for e in db.all('SELECT * FROM exports WHERE work_id = ? ORDER BY created DESC LIMIT 20', wid)]


@app.post('/api/works/{wid}/exports')
def create_export(wid: int, body: dict, u=Depends(current_user)):
    w = get_work(wid, u)
    if w['status'] != 'ready':
        raise HTTPException(400, '动画生成完成后才能导出')
    fmt = body.get('format')
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(400, '请选择导出格式')
    if fmt == 'bvh':
        raise HTTPException(400, 'BVH 格式每一帧都要写完整姿势，不能只保留关键帧。请选 FBX 或 GLB。')
    interp = 'LINEAR' if body.get('interp') == 'linear' else 'BEZIER'
    o = work_dir(w)
    artist = jload(os.path.join(o, keyframes_store.ARTIST_FILE)) or {}
    auto = jload(os.path.join(o, keyframes_store.AUTO_FILE)) or {}
    frames = [k['frame'] for k in (artist.get('keyframes') or auto.get('keyframes') or [])
              if isinstance(k, dict) and isinstance(k.get('frame'), int)]
    if len(frames) < 2:
        raise HTTPException(400, '至少需要 2 个关键帧。请先自动标记或手动添加关键帧并保存。')
    pending = db.one("SELECT id FROM exports WHERE work_id = ? AND status IN ('waiting', 'running')", wid)
    if pending:
        raise HTTPException(400, '上一个导出还在进行中，请稍等')
    t = now()
    eid = db.run("INSERT INTO exports (work_id, user_id, kind, options, status, created, updated) VALUES (?, ?, 'keys', ?, 'waiting', ?, ?)",
                 wid, u['id'], jdump({'format': fmt, 'interp': interp, 'frames': sorted(set(frames)),
                                      'revision': artist.get('revision', 0)}), t, t)
    return export_view(db.one('SELECT * FROM exports WHERE id = ?', eid))


@app.get('/api/exports/{eid}/download')
def export_download(eid: int, u=Depends(current_user)):
    e = db.one('SELECT * FROM exports WHERE id = ?', eid)
    if not e or (e['user_id'] != u['id'] and u['role'] != 'admin') or e['status'] != 'done' or not e['file']:
        raise HTTPException(404, '导出文件不存在')
    w = db.one('SELECT * FROM works WHERE id = ?', e['work_id'])
    f = inside(config.EXPORTS, e['file'])
    if not os.path.isfile(f):
        raise HTTPException(404, '导出文件已过期，请重新导出')
    fmt = jparse(e['options'], {}).get('format', 'fbx')
    return FileResponse(f, filename=download_name((w or {}).get('title', 'animation') + '_关键帧', fmt))


# ============================================================================ admin
@app.get('/api/admin/overview')
def admin_overview(u=Depends(admin_user)):
    users = db.all('SELECT u.id, u.username, u.role, u.disabled, u.created, u.last_login, '
                   '(SELECT COUNT(*) FROM works w WHERE w.user_id = u.id AND w.deleted = 0) AS works '
                   'FROM users u ORDER BY u.id')
    chars = []
    for c in db.all('SELECT * FROM characters ORDER BY id DESC'):
        owner = db.one('SELECT username FROM users WHERE id = ?', c['owner_id']) if c['owner_id'] else None
        v = char_view(c, u)
        v.update(project=c['project'], owner=owner['username'] if owner else '系统',
                 error_raw=((c['remake_error'] or '') + '\n' + (c['error'] or '')).strip()[-600:],
                 pending_project=c['pending_project'], prev_projects=jparse(c['prev_projects'], []) or [],
                 can_remake=bool(c['files_dir'] and c['model_path'] and os.path.isfile(c['model_path'])) and c['status'] == 'ready'
                 and not c['pending_project'])
        chars.append(v)
    works = []
    for w in db.all('SELECT * FROM works WHERE deleted = 0 ORDER BY created DESC LIMIT 100'):
        v = work_view(w)
        owner = db.one('SELECT username FROM users WHERE id = ?', w['user_id'])
        v.update(owner=owner['username'] if owner else '?', project=w['project'], result=w['result_name'],
                 job=w['remote_job'], error_raw=(w['error'] or '')[-400:])
        works.append(v)
    return {'users': users, 'characters': chars, 'works': works, 'engine': config.ENGINE}


@app.patch('/api/admin/users/{uid}')
def admin_user_update(uid: int, body: dict, u=Depends(admin_user)):
    target = db.one('SELECT * FROM users WHERE id = ?', uid)
    if not target:
        raise HTTPException(404, '用户不存在')
    if 'disabled' in body:
        if uid == u['id']:
            raise HTTPException(400, '不能停用自己')
        db.run('UPDATE users SET disabled = ? WHERE id = ?', 1 if body['disabled'] else 0, uid)
        if body['disabled']:
            db.run('DELETE FROM sessions WHERE user_id = ?', uid)
    if body.get('role') in ('user', 'admin'):
        if uid == u['id'] and body['role'] != 'admin':
            raise HTTPException(400, '不能取消自己的管理员身份')
        db.run('UPDATE users SET role = ? WHERE id = ?', body['role'], uid)
    if body.get('password'):
        if len(str(body['password'])) < 6:
            raise HTTPException(400, '密码至少 6 位')
        db.set_password(uid, str(body['password']))
    return {'ok': True}


@app.post('/api/admin/users')
def admin_create_user(body: dict, u=Depends(admin_user)):
    username = str(body.get('username') or '').strip()
    password = str(body.get('password') or '')
    if not USER_RE.match(username) or len(password) < 6:
        raise HTTPException(400, '用户名 2–24 个字，密码至少 6 位')
    if db.one('SELECT id FROM users WHERE username = ?', username):
        raise HTTPException(409, '用户名已存在')
    db.create_user(username, password, role='admin' if body.get('role') == 'admin' else 'user')
    return {'ok': True}


@app.patch('/api/admin/characters/{cid}')
def admin_character_update(cid: int, body: dict, u=Depends(admin_user)):
    c = db.one('SELECT * FROM characters WHERE id = ?', cid)
    if not c:
        raise HTTPException(404, '角色不存在')
    if 'name' in body:
        name = clip_text(body['name'], 40, '角色名')
        if name:
            db.update('characters', cid, name=name)
    if 'published' in body:
        if body['published'] and c['status'] != 'ready':
            raise HTTPException(400, '角色制作完成后才能公开')
        db.update('characters', cid, published=1 if body['published'] else 0)
    if body.get('approve'):
        if c['status'] not in ('pending_review', 'failed', 'uploaded'):
            raise HTTPException(400, '这个角色不需要审核')
        start_prepare(c)
    if body.get('remake'):
        if not c['files_dir'] or not c['model_path'] or not os.path.isfile(c['model_path']):
            raise HTTPException(400, '这是训练服务器上的项目，请在内部看板里重新制作')
        if c['status'] != 'ready':
            raise HTTPException(400, '只有可用的角色才能重新制作')
        if c['pending_project']:
            raise HTTPException(400, '这个角色正在重新制作')
        start_remake(c)
    if body.get('reject'):
        if c['status'] != 'pending_review':
            raise HTTPException(400, '这个角色不在审核中')
        db.update('characters', cid, status='uploaded')
    return {'ok': True}


@app.get('/api/admin/works/{wid}/log')
def admin_work_log(wid: int, u=Depends(admin_user)):
    w = db.one('SELECT * FROM works WHERE id = ?', wid)
    if not w:
        raise HTTPException(404)
    return {'log': engine().status(w['remote_job'])['log'][-12000:] if w['remote_job'] else ''}


# ============================================================================ background worker
def sync_characters():
    """Register trained projects as (unpublished) characters; publish those listed in STUDIO_PUBLISH."""
    publish = {}
    for item in filter(None, (s.strip() for s in config.PUBLISH.split(','))):
        p, _, label = item.partition(':')
        publish[p.strip()] = label.strip() or p.strip()
    try:
        names = sorted(os.listdir(config.PROJ))
    except OSError:
        return
    known = {c['project'] for c in db.all('SELECT project FROM characters WHERE project IS NOT NULL')}
    for p in names:
        # st* projects belong to user-uploaded characters and are registered by their own record.
        if p in known or p.startswith('.') or (p.startswith('st') and p not in publish) or not NAME_RE.fullmatch(p):
            continue
        if not project_ready(p):
            continue
        t = now()
        db.run('INSERT INTO characters (name, project, status, published, created, updated) VALUES (?, ?, ?, ?, ?, ?)',
               publish.get(p, p), p, 'ready', 1 if p in publish else 0, t, t)


def tick_characters():
    for c in db.all("SELECT * FROM characters WHERE status = 'preparing'"):
        if not c['remote_job']:
            try:
                o = jparse(c['orient'], None) or {}
                # --up rotates the model upright, --facing tells which way it then looks (studio/orient.py).
                extra = [f'--up={o.get("up", "+Z")}', f'--facing={o.get("facing", "-Y")}']
                if not c['model_path'] or not os.path.isfile(c['model_path']):
                    restore_after_failed_remake(c, 'model file missing')
                    continue
                jid = engine().submit(c['project'], 'pipeline', {'model': c['model_path'], 'extra': extra})
                db.update('characters', c['id'], remote_job=jid)
            except Busy:
                pass
            except Rejected as e:
                if e.kind == 'project_exists':
                    # The target project already has training (e.g. a crashed earlier run): use a fresh one.
                    start_prepare(db.one('SELECT * FROM characters WHERE id = ?', c['id']))
                    print(f'[studio] character {c["id"]}: project {c["project"]} already trained, moved to a new project', flush=True)
                else:
                    restore_after_failed_remake(c, str(e))
            continue
        st = engine().status(c['remote_job'])
        if st['status'] in ('queued', 'running'):
            continue
        if st['status'] == 'done' and project_ready(c['project']):
            db.update('characters', c['id'], status='ready', error=None)
        else:
            restore_after_failed_remake(c, (st['error'] or '') + '\n' + st['log'][-3000:])


def tick_remakes():
    """Background re-make of a usable character into pending_project; switch over only when it is trained."""
    for c in db.all("SELECT * FROM characters WHERE pending_project IS NOT NULL"):
        p = c['pending_project']
        if not c['remote_job']:
            if project_has_training(p):          # name collision with an existing trained project: pick another
                db.update('characters', c['id'], pending_project=new_project_name(c['id']))
                continue
            o = jparse(c['orient'], None) or {}
            extra = [f'--up={o.get("up", "+Z")}', f'--facing={o.get("facing", "-Y")}']
            try:
                jid = engine().submit(p, 'pipeline', {'model': c['model_path'], 'extra': extra})
                db.update('characters', c['id'], remote_job=jid)
            except Busy:
                pass
            except Rejected as e:
                if e.kind == 'project_exists':
                    db.update('characters', c['id'], pending_project=new_project_name(c['id']))
                else:
                    db.update('characters', c['id'], pending_project=None, remote_job=None, remake_error=str(e)[-3000:])
            continue
        st = engine().status(c['remote_job'])
        if st['status'] in ('queued', 'running'):
            continue
        if st['status'] == 'done' and project_ready(p):
            old = (jparse(c['prev_projects'], []) or []) + ([c['project']] if c['project'] else [])
            db.update('characters', c['id'], project=p, pending_project=None, remote_job=None, remake_error=None,
                      prev_projects=jdump(old), error=None)
            print(f'[studio] character {c["id"]} re-made: {c["project"]} -> {p}', flush=True)
        else:
            db.update('characters', c['id'], pending_project=None, remote_job=None,
                      remake_error=((st['error'] or '') + '\n' + st['log'][-3000:])[-3000:])


def restore_after_failed_remake(c, error):
    """A failed re-make must not take a working character away from users: fall back to the last trained project
    (status ready, error kept for the admin). A first-time make simply fails."""
    old = [p for p in (jparse(c['prev_projects'], []) or []) if project_ready(p)]
    if old:
        db.update('characters', c['id'], status='ready', project=old[-1], remote_job=None,
                  error=f'重新制作失败，已恢复为之前的版本：{error}'[-3000:])
    else:
        db.update('characters', c['id'], status='failed', error=error[-3000:])


def tick_works():
    for w in db.all("SELECT * FROM works WHERE deleted = 0 AND status IN ('waiting', 'queued', 'running') ORDER BY created"):
        if w['status'] == 'waiting':
            v = db.one('SELECT * FROM videos WHERE id = ?', w['video_id'])
            if not v or not os.path.isfile(v['file']):
                db.update('works', w['id'], status='failed', error='video missing')
                continue
            args = {'video': v['file'], 'name': w['result_name'], 'solver': config.DEFAULT_SOLVER,
                    'swap': False, 'force': bool(w['forced']), 'confirm_mismatch': False}
            try:
                jid = engine().submit(w['project'], 'animate', args)
                db.update('works', w['id'], status='queued', remote_job=jid)
            except Busy:
                pass
            except Rejected as e:
                if e.code == 409 and ('结果名称已存在' in str(e) or 'already exists' in str(e).lower()):
                    # Result name taken in the project (shouldn't happen: names are unique per work) - pick a new one.
                    db.update('works', w['id'], result_name=f'st_w{w["id"]}_{uuid.uuid4().hex[:6]}')
                else:
                    db.update('works', w['id'], status='failed', error=str(e))
            continue
        st = engine().status(w['remote_job'])
        s = st['status']
        if s in ('queued', 'running'):
            if s != w['status']:
                db.update('works', w['id'], status=s)
        elif s == 'done':
            o = work_dir(w)
            summary = jload(os.path.join(o, 'summary.json'), {}) or {}
            if os.path.isfile(os.path.join(o, f'{w["result_name"]}.glb')) and summary.get('status') in ('complete', 'forced_preview'):
                db.update('works', w['id'], status='ready', forced=1 if summary.get('forced') else 0, error=None)
                db.update('works', w['id'], kf_status='waiting')   # mark keyframes automatically
            else:
                db.update('works', w['id'], status='failed', error='finished without animation files')
        elif s == 'needs_review':
            db.update('works', w['id'], status='needs_review', error=st['log'][-2000:])
        else:
            db.update('works', w['id'], status='failed' if s != 'cancelled' else 'cancelled',
                      error=f'{s}: {st["error"]}\n{st["log"][-2000:]}')


def run_local_keys(wid, o, rig):
    try:
        r = subprocess.run([sys.executable, '-m', 'studio.auto_keys', o, rig], cwd=config.CODE,
                           capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            db.update('works', wid, kf_status='done', kf_error=None)
        else:
            db.update('works', wid, kf_status='failed', kf_error=(r.stdout + r.stderr)[-1500:])
    except Exception as e:  # noqa: BLE001
        db.update('works', wid, kf_status='failed', kf_error=str(e))


def tick_keyframes():
    for w in db.all("SELECT * FROM works WHERE deleted = 0 AND status = 'ready' AND kf_status IN ('waiting', 'running')"):
        o = work_dir(w)
        if w['kf_status'] == 'waiting':
            if keyframe_route(w) == 'v2r':
                try:
                    try:
                        jid = engine().submit(w['project'], 'keyframes', {
                            'name': w['result_name'], 'replace': os.path.isfile(os.path.join(o, keyframes_store.AUTO_FILE))})
                    except Rejected as e:
                        if e.kind != 'has_candidates':
                            raise
                        # Candidates appeared since we looked (e.g. generated from the dashboard): the user asked
                        # for a fresh auto-mark, so replace them (the dashboard keeps the old file as history).
                        jid = engine().submit(w['project'], 'keyframes', {'name': w['result_name'], 'replace': True})
                    db.update('works', w['id'], kf_status='running', kf_job=jid)
                except Busy:
                    pass
                except Rejected:
                    db.update('works', w['id'], kf_status='running', kf_job='local')
                    threading.Thread(target=run_local_keys, daemon=True,
                                     args=(w['id'], o, os.path.join(project_dir(w['project']), 'rig.json'))).start()
            else:
                db.update('works', w['id'], kf_status='running', kf_job='local')
                threading.Thread(target=run_local_keys, daemon=True,
                                 args=(w['id'], o, os.path.join(project_dir(w['project']), 'rig.json'))).start()
            continue
        if w['kf_job'] == 'local':
            continue
        st = engine().status(w['kf_job'])
        if st['status'] in ('queued', 'running'):
            continue
        if st['status'] == 'done':
            db.update('works', w['id'], kf_status='done', kf_error=None)
        else:
            # Biped extractor failed: fall back to the generic candidates.
            db.update('works', w['id'], kf_job='local')
            threading.Thread(target=run_local_keys, daemon=True,
                             args=(w['id'], o, os.path.join(project_dir(w['project']), 'rig.json'))).start()


def run_export(e):
    opt = jparse(e['options'], {})
    w = db.one('SELECT * FROM works WHERE id = ?', e['work_id'])
    o = work_dir(w)
    folder = os.path.join(config.EXPORTS, f'e{e["id"]}')
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, f'{w["result_name"]}_keys.{opt["format"]}')
    motion = next((os.path.join(o, f) for f in ('motion_temporal.npz', 'motion.npz') if os.path.isfile(os.path.join(o, f))), None)
    summary = jload(os.path.join(o, 'summary.json'), {}) or {}
    if config.ENGINE == 'fake':
        time.sleep(2 / config.FAKE_SPEED)
        shutil.copy2(os.path.join(o, f'{w["result_name"]}.{opt["format"]}'), out)
        return out
    if not motion:
        raise RuntimeError('motion.npz missing')
    cmd = [config.BLENDER, '-b', '--factory-startup', '--python', os.path.join(HERE, 'blender_export_keys.py'), '--',
           project_dir(w['project']), motion, out,
           json.dumps({'frames': opt['frames'], 'fps': summary.get('fps') or 24, 'format': opt['format'],
                       'interp': opt.get('interp', 'BEZIER'), 'name': w['result_name']})]
    with open(os.path.join(folder, 'export.log'), 'w') as log:
        r = subprocess.run(cmd, cwd=config.CODE, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    if r.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError(f'blender rc={r.returncode}')
    return out


def run_inspect(c):
    """Load an uploaded model in Blender like prepare does; write inspect.glb + report (CPU, ~10-60 s)."""
    folder = c['files_dir']
    glb, rep_path = os.path.join(folder, 'inspect.glb'), os.path.join(folder, 'inspect.json')
    if config.ENGINE == 'fake':
        # Dev: re-use the fixture's animated GLB; a model file whose name contains "lying" pretends to lie down.
        time.sleep(1 / config.FAKE_SPEED)
        if 'noarm' in (c['name'] or '').lower():
            return {'ok': False, 'error': 'NO_ARMATURE'}
        src = os.path.join(config.FAKE_FIXTURE, 'out', 'ladingwu_temporal', 'ladingwu_temporal.glb')
        shutil.copy2(src, glb)
        flat = 'lying' in (c['name'] or '').lower()
        return {'ok': True, 'bones': 176, 'meshes': 5, 'flat_in_z': flat, 'extent': [1.4, 1.5, 0.6] if flat else [1.4, 0.6, 1.5]}
    cmd = [config.BLENDER, '-b', '--factory-startup', '--python', os.path.join(HERE, 'blender_inspect.py'), '--',
           c['model_path'], glb, rep_path]
    with open(os.path.join(folder, 'inspect.log'), 'w') as log:
        subprocess.run(cmd, cwd=config.CODE, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    rep = jload(rep_path, None)
    if not rep:
        return {'ok': False, 'error': 'IMPORT_FAILED'}
    rep.pop('joints', None)
    return rep


def tick_inspect():
    c = db.one("SELECT * FROM characters WHERE inspect_status = 'waiting' AND files_dir IS NOT NULL ORDER BY created LIMIT 1")
    if not c:
        return False
    db.update('characters', c['id'], inspect_status='running')
    try:
        rep = run_inspect(c)
    except Exception as ex:  # noqa: BLE001
        traceback.print_exc()
        rep = {'ok': False, 'error': 'IMPORT_FAILED', 'detail': str(ex)[:300]}
    db.update('characters', c['id'], inspect_status='done' if rep.get('ok') else 'failed', inspect=jdump(rep))
    return True


def export_loop():
    while True:
        time.sleep(1.5)
        try:
            if tick_inspect():
                continue
            e = db.one("SELECT * FROM exports WHERE status = 'waiting' ORDER BY created LIMIT 1")
            if not e:
                continue
            db.update('exports', e['id'], status='running')
            try:
                f = run_export(e)
                db.update('exports', e['id'], status='done', file=f, error=None)
            except Exception as ex:  # noqa: BLE001
                traceback.print_exc()
                db.update('exports', e['id'], status='failed', error='导出失败，请稍后重试或联系管理员')
                print('[studio] export failed', e['id'], ex, flush=True)
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def worker_loop():
    last_sync = 0
    while True:
        try:
            if now() - last_sync > 60:
                sync_characters()
                last_sync = now()
            tick_characters()
            tick_remakes()
            tick_works()
            tick_keyframes()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(2)


@app.on_event('startup')
def start_worker():
    sync_characters()
    # Local jobs do not survive a restart.
    db.run("UPDATE works SET kf_status = 'waiting', kf_job = NULL WHERE kf_status = 'running' AND kf_job = 'local'")
    db.run("UPDATE exports SET status = 'waiting' WHERE status = 'running'")
    db.run("UPDATE characters SET inspect_status = 'waiting' WHERE inspect_status = 'running'")
    # Characters uploaded before inspection existed: inspect them too (only while they can still be changed).
    db.run("UPDATE characters SET inspect_status = 'waiting' WHERE inspect_status = 'none' AND files_dir IS NOT NULL "
           "AND status IN ('uploaded', 'failed')")
    if not config.DISABLE_WORKER:
        threading.Thread(target=worker_loop, daemon=True).start()
        threading.Thread(target=export_loop, daemon=True).start()


# ============================================================================ pages
@app.get('/kf-core.js')
def kf_core():
    # Bundled copy of web/static/keyframes-core.js (same keyframe contract as the internal dashboard).
    return FileResponse(os.path.join(STATIC, 'kf-core.js'), media_type='text/javascript')


@app.get('/', response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(STATIC, 'index.html'), headers={'Cache-Control': 'no-cache'})


@app.get('/healthz')
def healthz():
    return {'ok': True, 'engine': config.ENGINE}
