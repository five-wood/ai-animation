"""Retire superseded public assets only after the new release is verified live."""
from pathlib import Path
import shutil

root=Path('/data/video2rig/code/web/static').resolve(strict=True)
old=(root/'releases/ui-v1-0171ee2e5142').resolve(strict=True)
active=(root/'releases/ui-v1-056895d632eb').resolve(strict=True)
assert str(old)=='/data/video2rig/code/web/static/releases/ui-v1-0171ee2e5142'
assert str(active)=='/data/video2rig/code/web/static/releases/ui-v1-056895d632eb'
assert old.parent==root/'releases' and old!=active
assert '/static/releases/ui-v1-056895d632eb/app.js' in (root/'index.html').read_text()
backup=Path('/data/video2rig/web-release-backups/ui-v1-056895d632eb/previous-static')
assert backup.parent==Path('/data/video2rig/web-release-backups/ui-v1-056895d632eb')
assert not backup.exists()
print('Verified superseded public release:',old)
print('Nonpublic operational backup:',backup)
shutil.copytree(old,backup)
for name in ['index.html','app.css','app.js','run-page.js','results.js']:
    assert (backup/name).read_bytes()==(old/name).read_bytes()
shutil.rmtree(old)
assert (active/'app.js').is_file() and (root/'vendor/three.module.js').is_file()
print('Only active release remains public; previous release retained only for operational rollback')
