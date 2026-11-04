# Push local code to the training server, optionally upload the model and pull results.
#   .\sync.ps1                      # code only
#   .\sync.ps1 -Model ..\model\11101_01.fbx
#   .\sync.ps1 -Video D:\clips\roar.mp4
#   .\sync.ps1 -Pull m11101         # download projects/m11101/out + exam reports to .\results\m11101
param([string]$Model, [string]$Video, [string]$Pull)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
ssh animsrv "mkdir -p /data/video2rig/code /data/video2rig/inputs /data/video2rig/videos"
scp -rq "$here\v2r" "$here\tools" "$here\README.md" animsrv:/data/video2rig/code/
Write-Host "code synced"
if ($Model) {
    $dir = Split-Path -Parent (Resolve-Path $Model)
    scp -q (Resolve-Path $Model) animsrv:/data/video2rig/inputs/
    # textures next to the model (FBX references them by file name)
    Get-ChildItem $dir -File | Where-Object { $_.Extension -match '\.(png|jpg|jpeg|tga|tif|tiff|dds|bmp)$' } |
        ForEach-Object { scp -q $_.FullName animsrv:/data/video2rig/inputs/ }
    Write-Host "model uploaded -> /data/video2rig/inputs/$(Split-Path -Leaf $Model)"
}
if ($Video) {
    scp -q (Resolve-Path $Video) animsrv:/data/video2rig/videos/
    Write-Host "video uploaded -> /data/video2rig/videos/$(Split-Path -Leaf $Video)"
}
if ($Pull) {
    $dst = Join-Path $here "results\$Pull"
    New-Item -ItemType Directory -Force $dst | Out-Null
    ssh animsrv "cd /data/video2rig/projects/$Pull && tar czf /tmp/v2r_pull.tgz --exclude=preview_frames --exclude=frames reference_two_views.png rig.json studio.json out exam 2>/dev/null; true"
    scp -q animsrv:/tmp/v2r_pull.tgz "$dst\pull.tgz"
    tar -xzf "$dst\pull.tgz" -C $dst
    Remove-Item "$dst\pull.tgz"
    Write-Host "results in $dst"
}
