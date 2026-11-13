"""Read-only: summarise every saved artist annotation against its candidate file."""
import glob, json, os
P = '/data/video2rig/projects'
rows = sorted(glob.glob(f'{P}/*/out/*/keyframes_artist.json'))
autos = sorted(glob.glob(f'{P}/*/out/*/keyframes_auto.json'))
print('candidate files:', len(autos), [a.split('/')[-4] + '/' + a.split('/')[-2] for a in autos])
print('artist files:', len(rows))
for f in rows:
    d = os.path.dirname(f)
    a = json.load(open(f))
    try:
        auto = json.load(open(d + '/keyframes_auto.json'))
    except Exception:
        auto = {'keyframes': []}
    cand = {k['id']: k for k in auto['keyframes']}
    ks = a['keyframes']
    origin = {o: [k for k in ks if k['origin'] == o] for o in ('auto', 'ladder', 'artist')}
    moved = [(k['auto_id'], k['auto_frame'], k['frame']) for k in origin['auto'] if k['frame'] != k['auto_frame']]
    retyped = [(k['auto_id'], cand[k['auto_id']]['type'], k['type']) for k in origin['auto'] if k['auto_id'] in cand and cand[k['auto_id']]['type'] != k['type']]
    hist = len(glob.glob(d + '/keyframes_artist_history/rev*.json'))
    print(f"\n== {f.split('/')[-4]}/{f.split('/')[-2]}  r{a['revision']}  by {a.get('author')!r}  reviewed={a.get('reviewed')}  saved {a.get('saved_at')}  history={hist}")
    print(f"   total {len(ks)}: kept candidates {len(origin['auto'])}/{len(cand)}, removed {len(a.get('removed_auto_ids', []))} {a.get('removed_auto_ids')}, "
          f"ladder adopted {len(origin['ladder'])}, artist added {len(origin['artist'])}")
    print('   moved:', moved)
    print('   retyped:', retyped)
    print('   added:', [(k['frame'], k['type'], k.get('note', '')[:30]) for k in origin['artist']])
    print('   notes:', (a.get('notes') or '')[:200])
    print('   base matches current candidates:', a.get('base_auto_sha256') and __import__('hashlib').sha256(open(d + '/keyframes_auto.json', 'rb').read()).hexdigest() == a.get('base_auto_sha256'))
