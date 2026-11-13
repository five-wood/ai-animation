# Local fake-engine studio server on :8711 (fresh data each run).
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$here\setup_local.py"
$env:V2R_ROOT = "$here\root"
$env:STUDIO_ENGINE = "fake"
$env:STUDIO_FAKE_FIXTURE = "$here\fixture"
$env:STUDIO_VENDOR = "$here\vendor"
$env:STUDIO_PUBLISH = "m11101_tex:11101 怪物,lizard_demo:示例蜥蜴"
$env:STUDIO_FAKE_SPEED = "0.7"
$env:STUDIO_ADMIN_USER = "admin"
$env:STUDIO_ADMIN_PASSWORD = "admin123"
$env:PYTHONIOENCODING = "utf-8"
Set-Location "$here\..\..\video2rig"
python -m uvicorn studio.app:app --host 127.0.0.1 --port 8711
