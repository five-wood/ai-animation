"""Real end-to-end check against the deployed studio (GPU job through the 8600 queue).
python work/studio_real_test.py http://10.0.3.117:8700 <admin_password>"""
import sys
import time
import uuid

import httpx

BASE, ADMIN_PW = sys.argv[1], sys.argv[2]
c = httpx.Client(base_url=BASE, headers={'X-Studio': '1'}, timeout=600)
r = c.post('/api/auth/login', json={'username': 'admin', 'password': ADMIN_PW})
assert r.status_code == 200, r.text
chars = c.get('/api/characters').json()
print('characters:', [(x['id'], x['name']) for x in chars])
ch = next(x for x in chars if '11101' in x['name'])
d = c.get(f'/api/characters/{ch["id"]}').json()
print('preview:', d['preview'])
assert c.get(d['preview']['url']).status_code == 200
assert c.get(f'/api/characters/{ch["id"]}/reference?download=1').status_code == 200
with open(r'D:\Project\ai-animation\ladingwu.mp4', 'rb') as f:
    v = c.post(f'/api/characters/{ch["id"]}/videos', files={'file': ('拉丁舞.mp4', f, 'video/mp4')}).json()
print('video:', v)
w = c.post('/api/works', json={'character_id': ch['id'], 'video_id': v['id'], 'title': '验收-拉丁舞'}).json()
t0 = time.time()
last = None
while True:
    w = c.get(f'/api/works/{w["id"]}').json()
    p = (w['status'], (w.get('progress') or {}).get('text'), (w.get('progress') or {}).get('percent'))
    if p != last:
        print(f'{time.time() - t0:6.0f}s', p)
        last = p
    if w['status'] not in ('waiting', 'queued', 'running'):
        break
    time.sleep(3)
print('final:', w['status'], w.get('message'), w.get('files'))
assert w['status'] == 'ready', w
while w['kf_status'] in ('waiting', 'running', 'none'):
    time.sleep(3)
    w = c.get(f'/api/works/{w["id"]}').json()
print('keyframes:', w['kf_status'], w.get('kf_error'))
kv = c.get(f'/api/works/{w["id"]}/keyframes').json()
print('auto keys:', len(kv['auto']['keyframes']), 'frames', kv['frames'], 'fps', kv['fps'])
keys = [{'id': k['id'], 'frame': k['frame'], 'type': k['type'], 'note': '', 'origin': 'auto', 'auto_id': k['id'], 'auto_frame': k['frame']}
        for k in kv['auto']['keyframes']]
r = c.put(f'/api/works/{w["id"]}/keyframes', json={'base_revision': kv['revision'], 'keyframes': keys, 'removed_auto_ids': []})
print('save:', r.status_code, r.json().get('revision'))
for fmt in ('fbx',):
    full = c.get(f'/api/works/{w["id"]}/download/{fmt}')
    print('full', fmt, full.status_code, len(full.content))
e = c.post(f'/api/works/{w["id"]}/exports', json={'format': 'fbx', 'interp': 'smooth'}).json()
while e['status'] in ('waiting', 'running'):
    time.sleep(3)
    e = next(x for x in c.get(f'/api/works/{w["id"]}/exports').json() if x['id'] == e['id'])
print('keys export:', e['status'], e.get('error'))
if e['url']:
    blob = c.get(e['url']).content
    open(r'D:\Project\ai-animation\work\studio_keys_export.fbx', 'wb').write(blob)
    print('keys fbx bytes', len(blob))
print('WORK', w['id'], 'total', round(time.time() - t0), 's')
