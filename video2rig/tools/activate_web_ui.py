"""Verify a staged UI release; optionally switch ONLY the current HTML entry.
Never restarts a service or retains an older Web implementation.
Usage: python activate_web_ui.py ui-v1-<digest>
Activate: add --activate --expected-entry-sha256 <observed-current-entry-hash>
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('release')
parser.add_argument('--activate', action='store_true')
parser.add_argument('--expected-entry-sha256', help='Required observed current-entry digest for activation')
args = parser.parse_args()
assert re.fullmatch(r'ui-v1-[a-f0-9]{12}', args.release), 'unsafe release id'
root = Path('/data/video2rig/code/web/static').resolve(strict=True)
release = (root / 'releases' / args.release).resolve(strict=True)
assert release.parent == root / 'releases'
manifest = json.loads((release / 'manifest.json').read_text())
for name, expected in manifest['files'].items():
    assert name in {'index.html', 'app.css', 'app.js', 'run-page.js', 'results.js'}
    assert hashlib.sha256((release / name).read_bytes()).hexdigest() == expected, name
entry = root / 'index.html'
pid = subprocess.check_output(['systemctl', 'show', 'video2rig-web', '-p', 'MainPID'], text=True).strip()
if args.activate:
    expected = args.expected_entry_sha256 or ''
    assert re.fullmatch(r'[a-f0-9]{64}', expected), 'Provide observed --expected-entry-sha256'
    old_hash = hashlib.sha256(entry.read_bytes()).hexdigest()
    assert old_hash == expected, 'Live entry changed: stop and inspect'
    target = root / 'index.next.tmp'
    assert target.parent == root
    target.write_bytes((release / 'index.html').read_bytes())
    os.replace(target, entry)  # Atomic replacement on same filesystem; no service restart.
    (release / 'activation.json').write_text(json.dumps({'old_entry_sha256':old_hash, 'new_entry_sha256':hashlib.sha256(entry.read_bytes()).hexdigest(), 'service_pid':pid}, indent=2))
    print('Activated static entry:', args.release)
else:
    print('Verified release:', args.release)
assert subprocess.check_output(['systemctl', 'show', 'video2rig-web', '-p', 'MainPID'], text=True).strip() == pid
print(pid, '(unchanged; no restart performed)')
