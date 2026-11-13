"""Create an immutable static-only UI release. Does not deploy or restart anything.
Run: python video2rig/tools/package_web_ui.py
"""
from pathlib import Path
import hashlib
import json
import tarfile
from datetime import datetime, timezone

repo = Path(__file__).resolve().parents[2]
source = repo / 'video2rig' / 'web' / 'static'
names = ['index.html', 'app.css', 'app.js', 'run-page.js', 'results.js', 'task-ui.js', 'keyframes.js', 'keyframes-core.js']
contents = {n: (source / n).read_bytes() for n in names}
digest = hashlib.sha256(b''.join(contents[n] for n in names)).hexdigest()[:12]
release = f'ui-v1-{digest}'
out = repo / 'work' / 'web-ui-v1' / release
out.mkdir(parents=True, exist_ok=True)
# Root and preview entry both load only this release's own assets.
entry = contents['index.html'].decode('utf-8').replace('/static/app.css?v=workspace-v1', f'/static/releases/{release}/app.css').replace('/static/app.js?v=workspace-v1', f'/static/releases/{release}/app.js')
contents['index.html'] = entry.encode('utf-8')
for name, data in contents.items():
    (out / name).write_bytes(data)
manifest = {'release': release, 'created_utc': datetime.now(timezone.utc).isoformat(), 'scope': 'static-only; no restart; no algorithms or API changes', 'files': {n: hashlib.sha256(data).hexdigest() for n, data in contents.items()}}
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
archive = out.parent / f'{release}.tar.gz'
with tarfile.open(archive, 'w:gz') as tar:
    for name in names + ['manifest.json']:
        tar.add(out / name, arcname=f'{release}/{name}')
print(json.dumps({'release': release, 'directory': str(out), 'archive': str(archive), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}, indent=2))
