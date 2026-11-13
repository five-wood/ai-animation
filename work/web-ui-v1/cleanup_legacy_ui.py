"""One-off allowlisted removal of superseded UI artifacts, never service/data changes."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

root = Path('/data/video2rig/code/web/static').resolve(strict=True)
assert str(root) == '/data/video2rig/code/web/static'
current = 'ui-v1-0171ee2e5142'
current_dir = (root / 'releases' / current).resolve(strict=True)
entry = root / 'index.html'
assert hashlib.sha256(entry.read_bytes()).hexdigest() == 'd156b51f2a2ffbaeefb6976c31f736aa0134dc54dc0755421b0445b0952b2b8e'
text = entry.read_text()
assert f'/static/releases/{current}/app.js' in text
assert f'/static/releases/{current}/app.css' in text
assert '/static/app.js' not in text and '/static/app.css' not in text
old_ids = ['ui-v1-de658b0daec6', 'ui-v1-69e776c4c01e', 'ui-v1-392d5be52448', 'ui-v1-c467e17faf09']
targets = [root / name for name in ['app.js', 'app.css', 'run-page.js']]
targets += [current_dir / 'previous-index.html']
targets += [root / 'releases' / name for name in old_ids]
targets += [Path('/tmp') / f'v2r-{name}.tar.gz' for name in old_ids]
resolved = []
for path in targets:
    if not path.exists():
        continue
    assert not path.is_symlink(), f'Refuse symlink: {path}'
    target = path.resolve(strict=True)
    assert target == path, f'Unexpected resolved target: {target}'
    assert target != root and target != current_dir and target != entry and target != root / 'vendor'
    resolved.append(target)
    print('REMOVE:', target, '(directory)' if target.is_dir() else '(file)')
pid = subprocess.check_output(['systemctl', 'show', 'video2rig-web', '-p', 'MainPID'], text=True).strip()
if '--apply' in sys.argv:
    for target in resolved:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    assert entry.is_file() and (root / 'vendor' / 'three.module.js').is_file()
    assert (current_dir / 'app.js').is_file() and (current_dir / 'results.js').is_file()
    assert subprocess.check_output(['systemctl', 'show', 'video2rig-web', '-p', 'MainPID'], text=True).strip() == pid
    print('Removed allowlisted legacy assets; active entry, vendor and service PID unchanged:', pid)
else:
    print('Dry-run only. Targets resolved and verified; pass --apply to remove.')
