# Copy the local working-tree code into the server's scratch code dir (deployed code + local changes).
# /data/video2rig/code is never written.  Usage: powershell -File work\surface_solver\sync_code.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
$stage = Join-Path $env:TEMP "v2r_surface_code"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $stage | Out-Null
$files = git -C $root ls-files -m -o --exclude-standard video2rig/v2r video2rig/tools
foreach ($f in $files) {
    $src = Join-Path $root $f
    if (-not (Test-Path $src)) { continue }
    $rel = $f.Substring("video2rig/".Length)
    $dst = Join-Path $stage $rel
    New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
    $t = [IO.File]::ReadAllText($src) -replace "`r", ""
    [IO.File]::WriteAllText($dst, $t)
}
ssh -o BatchMode=yes animsrv 'C=/data/video2rig/experiments/surface_solver/code; if [ ! -d $C/v2r ]; then mkdir -p $C && cp -r /data/video2rig/code/v2r /data/video2rig/code/tools $C/; fi; rm -rf /tmp/v2r_surface_code'
scp -rq $stage animsrv:/tmp/
ssh -o BatchMode=yes animsrv 'cp -r /tmp/v2r_surface_code/. /data/video2rig/experiments/surface_solver/code/ && rm -rf /tmp/v2r_surface_code && find /data/video2rig/experiments/surface_solver/code -name __pycache__ -prune -exec rm -rf {} +'
Remove-Item -Recurse -Force $stage
Write-Host "synced:" ($files -join ", ")
