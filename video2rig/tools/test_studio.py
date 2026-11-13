"""End-to-end API test for the user studio against a running fake-engine server.

python video2rig/tools/test_studio.py http://127.0.0.1:8711 <video.mp4>
Requires STUDIO_ENGINE=fake (see work/studio-dev/setup_local.py) and the admin admin/admin123.
"""
import json
import os
import sys
import time
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8711'
VIDEO = sys.argv[2] if len(sys.argv) > 2 else 'ladingwu.mp4'
H = {'X-Studio': '1'}
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise SystemExit(f'FAIL: {msg}')
    ok += 1
    print('  ok', msg)


def client():
    return httpx.Client(base_url=BASE, headers=H, timeout=60)


def wait(c, url, pred, limit=90):
    t = time.time()
    while time.time() - t < limit:
        r = c.get(url).json()
        if pred(r):
            return r
        time.sleep(1)
    raise SystemExit(f'FAIL: timeout waiting on {url}: {r}')


anon = client()
check(anon.get('/api/characters').status_code == 401, 'anonymous blocked')
check(httpx.post(BASE + '/api/auth/login', json={}).status_code == 403, 'csrf header required')
name = 'u' + uuid.uuid4().hex[:6]
u = client()
r = u.post('/api/auth/register', json={'username': name, 'password': 'secret1'})
check(r.status_code == 200 and r.json()['user']['role'] == 'user', 'register user')
check(u.post('/api/auth/register', json={'username': name, 'password': 'secret1'}).status_code == 409, 'duplicate name')
check(u.post('/api/auth/login', json={'username': name, 'password': 'bad'}).status_code == 400, 'bad password')
chars = u.get('/api/characters').json()
check(len(chars) >= 1 and all(c['status'] == 'ready' for c in chars), f'library lists {len(chars)} ready characters')
char = next(c for c in chars if '11101' in c['name'])
d = u.get(f'/api/characters/{char["id"]}').json()
check(d['preview'] and d['preview']['kind'] == 'fbx', 'fbx preview available')
check(u.get(d['preview']['url']).status_code == 200, 'fbx asset served')
check(u.get(f'/api/characters/{char["id"]}/asset/..%2F..%2Fsecret.fbx').status_code in (403, 404), 'asset traversal blocked')
ref = u.get(f'/api/characters/{char["id"]}/reference?download=1')
check(ref.status_code == 200 and 'attachment' in ref.headers.get('content-disposition', ''), 'reference download')

with open(VIDEO, 'rb') as f:
    r = u.post(f'/api/characters/{char["id"]}/videos', files={'file': ('我的动作.mp4', f, 'video/mp4')})
check(r.status_code == 200, f'video upload {r.text[:200]}')
vid = r.json()['id']
check(u.post(f'/api/characters/{char["id"]}/videos', files={'file': ('x.txt', b'abc', 'text/plain')}).status_code == 400, 'non-video rejected')
other = client()
other.post('/api/auth/register', json={'username': 'o' + uuid.uuid4().hex[:6], 'password': 'secret1'})
check(other.get(f'/api/videos/{vid}/file').status_code == 404, "other user cannot read video")

w = u.post('/api/works', json={'character_id': char['id'], 'video_id': vid, 'title': '挥拳'}).json()
check(w['status'] == 'waiting', 'work created')
check(other.get(f'/api/works/{w["id"]}').status_code == 404, 'other user cannot see work')
w = wait(u, f'/api/works/{w["id"]}', lambda x: x['status'] not in ('waiting', 'queued', 'running'))
check(w['status'] == 'ready' and w['files'].get('fbx') and w['files'].get('compare'), f'work ready {w["status"]}')
check(u.get(f'/api/works/{w["id"]}/media/model.glb').status_code == 200, 'glb preview')
dl = u.get(f'/api/works/{w["id"]}/download/fbx')
check(dl.status_code == 200 and '.fbx' in dl.headers.get('content-disposition', ''), 'full fbx download')
w = wait(u, f'/api/works/{w["id"]}', lambda x: x['kf_status'] in ('done', 'failed'))
check(w['kf_status'] == 'done', 'auto keyframes after generation')
kv = u.get(f'/api/works/{w["id"]}/keyframes').json()
check(kv['auto'] and len(kv['auto']['keyframes']) > 3 and kv['revision'] == 0, 'keyframe view')
keys = [{'id': k['id'], 'frame': k['frame'], 'type': k['type'], 'note': '', 'origin': 'auto', 'auto_id': k['id'], 'auto_frame': k['frame']}
        for k in kv['auto']['keyframes'][:4]]
keys.append({'id': 'a001', 'frame': 100 if all(k['frame'] != 100 for k in keys) else 101, 'type': 'custom', 'note': '手动', 'origin': 'artist', 'auto_id': None, 'auto_frame': None})
removed = [k['id'] for k in kv['auto']['keyframes'][4:]]
r = u.put(f'/api/works/{w["id"]}/keyframes', json={'base_revision': 0, 'keyframes': keys, 'removed_auto_ids': removed})
check(r.status_code == 200 and r.json()['revision'] == 1 and r.json()['artist']['author'] == name, 'save keyframes r1')
r = u.put(f'/api/works/{w["id"]}/keyframes', json={'base_revision': 0, 'keyframes': keys, 'removed_auto_ids': removed})
check(r.status_code == 409, 'stale save 409')
e = u.post(f'/api/works/{w["id"]}/exports', json={'format': 'fbx', 'interp': 'smooth'}).json()
check(e['options']['frames'] == sorted(k['frame'] for k in keys), 'export uses saved keyframes')
e = wait(u, f'/api/works/{w["id"]}/exports', lambda x: x[0]['status'] in ('done', 'failed'))[0]
check(e['status'] == 'done' and u.get(e['url']).status_code == 200, 'keyframe export download')
check(other.get(e['url']).status_code == 404, 'other user cannot download export')

# needs_review path + forced retry
with open(VIDEO, 'rb') as f:
    vid2 = u.post(f'/api/characters/{char["id"]}/videos', files={'file': ('bad_clip.mp4', f, 'video/mp4')}).json()['id']
w2 = u.post('/api/works', json={'character_id': char['id'], 'video_id': vid2}).json()
w2 = wait(u, f'/api/works/{w2["id"]}', lambda x: x['status'] not in ('waiting', 'queued', 'running'))
check(w2['status'] == 'needs_review' and w2['message'], 'bad video -> needs_review with message')
w2 = u.post(f'/api/works/{w2["id"]}/retry', json={'force': True}).json()
w2 = wait(u, f'/api/works/{w2["id"]}', lambda x: x['status'] not in ('waiting', 'queued', 'running'))
check(w2['status'] == 'ready' and w2['forced'], 'forced preview ready')
check(u.delete(f'/api/works/{w2["id"]}').status_code == 200 and u.get(f'/api/works/{w2["id"]}').status_code == 404, 'delete work')

# character upload -> review -> admin approve -> prepared
fbx = open(VIDEO, 'rb').read()[:2048]
r = u.post('/api/characters', data={'name': '我的怪兽'}, files=[('files', ('monster.fbx', fbx, 'application/octet-stream')), ('files', ('tex.png', b'png', 'image/png'))])
check(r.status_code == 200 and r.json()['status'] == 'uploaded', 'character uploaded')
cid = r.json()['id']
check(u.post('/api/characters', data={'name': 'x'}, files=[('files', ('a.exe', b'x', 'application/octet-stream'))]).status_code == 400, 'bad model rejected')
check(other.get(f'/api/characters/{cid}').status_code == 404, 'unpublished character private')
c = wait(u, f'/api/characters/{cid}', lambda x: x['inspect']['status'] in ('done', 'failed'))
check(c['inspect']['status'] == 'done' and c['preview']['inspected'] and c['orient_editable'], 'uploaded model inspected, orientation editable')
check(u.get(c['preview']['url']).status_code == 200, 'inspected preview glb served')
check(other.put(f'/api/characters/{cid}/orient', json={'matrix': [1, 0, 0, 0, 0, -1, 0, 1, 0]}).status_code in (403, 404), 'others cannot orient')
check(u.put(f'/api/characters/{cid}/orient', json={'matrix': [1, 0, 0, 0, 1, 0, 0, 0, -1]}).status_code == 400, 'mirror rejected')
r = u.put(f'/api/characters/{cid}/orient', json={'matrix': [1, 0, 0, 0, 0, -1, 0, 1, 0]})
check(r.status_code == 200 and r.json()['orient']['up'] == '+Y' and r.json()['orient']['facing'] == '-Y', f'orient saved {r.text[:120]}')
r = u.post(f'/api/characters/{cid}/prepare').json()
check(r['status'] == 'pending_review', 'prepare needs review')
check(u.put(f'/api/characters/{cid}/orient', json={'matrix': [1, 0, 0, 0, 1, 0, 0, 0, 1]}).status_code == 400, 'orientation locked after submit')
check(u.get('/api/admin/overview').status_code == 403, 'admin api blocked for users')
adm = client()
check(adm.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).status_code == 200, 'admin login')
check(adm.patch(f'/api/admin/characters/{cid}', json={'approve': True}).status_code == 200, 'admin approve')
c = wait(u, f'/api/characters/{cid}', lambda x: x['status'] in ('ready', 'failed'), limit=60)
check(c['status'] == 'ready', 'uploaded character prepared')
job_log = adm.get(f'/api/admin/overview').json()
pc = next(x for x in job_log['characters'] if x['id'] == cid)
check(pc['project'] and pc['project'].startswith('st'), 'own project for uploaded character')
fake_dir = os.path.join(os.environ.get('V2R_ROOT', ''), 'studio', 'fake_jobs')
plog = [open(os.path.join(fake_dir, f), encoding='utf-8').read() for f in os.listdir(fake_dir) if f'-pipeline-{pc["project"]}-' in f] if os.path.isdir(fake_dir) else []
check(plog and '"--up=+Y"' in plog[-1] and '"--facing=-Y"' in plog[-1], f'pipeline got --up/--facing from the web orientation: {plog[-1][:120] if plog else "no log"}')
check(c['preview'] and c['preview'].get('inspected') and u.get(c['preview']['url']).status_code == 200, 'uploaded character previewable (inspected glb)')
check(not c['orient_editable'] and c['orient']['up'] == '+Y', 'orientation kept and locked once ready')
check(adm.patch(f'/api/admin/characters/{cid}', json={'published': True}).status_code == 200, 'publish')

# ---- re-make an already-trained character (the dashboard answers 409 for a pipeline into a trained project)
proj_v1 = pc['project']
check(u.post(f'/api/characters/{cid}/prepare').status_code == 403, 'owner cannot re-make without admin (approval mode)')
check(adm.patch(f'/api/admin/characters/{cid}', json={'remake': True}).status_code == 200, 'admin re-make accepted')
mid = adm.get(f'/api/characters/{cid}').json()
check(mid['status'] == 'ready' and 'remake' in mid, 'still usable while re-making')
check(adm.patch(f'/api/admin/characters/{cid}', json={'remake': True}).status_code == 400, 'second re-make refused while running')


def remake_done(_):
    row = next(x for x in adm.get('/api/admin/overview').json()['characters'] if x['id'] == cid)
    return not row['pending_project'] and row['project'] != proj_v1 and row


row = wait(adm, '/api/admin/overview', lambda _: remake_done(_), limit=90)
row = remake_done(None)
check(row['project'] != proj_v1 and proj_v1 in row['prev_projects'], f'switched to new project, old kept: {proj_v1} -> {row["project"]}')
check(os.path.isfile(os.path.join(os.environ['V2R_ROOT'], 'projects', proj_v1, 'model', 'best.pt')), 'old trained project untouched on disk')
check(adm.get(f'/api/characters/{cid}').json()['status'] == 'ready', 'ready after re-make')
# system character (no uploaded model): re-make must be refused with a clear message, never sent to 8600
sys_c = next(x for x in adm.get('/api/admin/overview').json()['characters'] if x['owner'] == '系统')
r = adm.patch(f'/api/admin/characters/{sys_c["id"]}', json={'remake': True})
check(r.status_code == 400 and '内部看板' in r.json()['detail'], 'system character re-make refused')

# ---- retry a failed first-time make whose project already has a rig (prepare ran, training crashed)
with open(VIDEO, 'rb') as f:
    head = f.read(2048)
r = adm.post('/api/characters', data={'name': '重试测试'}, files=[('files', ('noarm_model.fbx', head, 'application/octet-stream'))])
fid = r.json()['id']
wait(adm, f'/api/characters/{fid}', lambda x: x['inspect']['status'] in ('done', 'failed'))
import sqlite3  # noqa: E402  (test-only: mark the uploaded model so the fake pipeline fails once)
fdir = sqlite3.connect(os.path.join(os.environ['V2R_ROOT'], 'studio', 'studio.db')).execute(
    'SELECT files_dir FROM characters WHERE id = ?', (fid,)).fetchone()[0]
open(os.path.join(fdir, 'FAKE_FAIL'), 'w').close()
check(adm.post(f'/api/characters/{fid}/prepare').status_code == 200, 'admin starts first make')
wait(adm, f'/api/characters/{fid}', lambda x: x['status'] in ('ready', 'failed'), limit=60)
frow = next(x for x in adm.get('/api/admin/overview').json()['characters'] if x['id'] == fid)
check(frow['status'] == 'failed', 'first make fails (fake: no armature)')
fproj = frow['project']
os.makedirs(os.path.join(os.environ['V2R_ROOT'], 'projects', fproj), exist_ok=True)
open(os.path.join(os.environ['V2R_ROOT'], 'projects', fproj, 'rig.json'), 'w').write('{}')   # what a real crash leaves behind
os.remove(os.path.join(fdir, 'FAKE_FAIL'))
check(adm.patch(f'/api/admin/characters/{fid}', json={'approve': True}).status_code == 200, 'retry accepted')
wait(adm, f'/api/characters/{fid}', lambda x: x['status'] in ('ready', 'failed'), limit=60)
frow = next(x for x in adm.get('/api/admin/overview').json()['characters'] if x['id'] == fid)
check(frow['project'] != fproj and 'already contains' not in frow['error_raw'],
      f'retry used a fresh project instead of a 409 ({fproj} -> {frow["project"]}): {frow["error_raw"][:80]}')

# ---- keyframes: auto-mark again on a result that already has candidates (dashboard 409 without replace)
w1 = next(x for x in u.get('/api/works').json() if x['status'] == 'ready')
check(u.post(f'/api/works/{w1["id"]}/keyframes/auto').status_code == 200, 're-run auto keyframes')
w1 = wait(u, f'/api/works/{w1["id"]}', lambda x: x['kf_status'] in ('done', 'failed'))
check(w1['kf_status'] == 'done', 'auto keyframes again succeeds (replace)')
check(any(x['id'] == cid for x in other.get('/api/characters').json()), 'published visible to others')
ov = adm.get('/api/admin/overview').json()
check(any(x['username'] == name for x in ov['users']), 'admin overview')
check(u.post('/api/auth/logout').status_code == 200 and u.get('/api/works').status_code == 401, 'logout')
print(f'ALL {ok} CHECKS PASSED')
