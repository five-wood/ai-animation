# Surface trial environment (separate from /data/video2rig/.venv): SAM 2 + Open3D.
set -e
export PATH=/root/.local/bin:$PATH
E=/data/video2rig/experiments/surface_trial
mkdir -p $E/ckpt $E/out $E/data $E/src $E/tmp
cd $E
echo "== machine"; nproc; free -g | head -2
if [ ! -x .venv/bin/python ]; then uv venv --python 3.11 .venv; fi
export UV_LOCK_TIMEOUT=900
uv pip install --python .venv/bin/python torch==2.7.0 torchvision==0.22.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv/bin/python numpy opencv-python-headless scipy open3d setuptools wheel
if [ ! -d src/sam2 ]; then git clone --depth 1 https://github.com/facebookresearch/sam2.git src/sam2; fi
(cd src/sam2 && echo "sam2 commit: $(git log -1 --format='%H %cd')")
# no nvcc on this server: skip the optional CUDA post-processing extension
SAM2_BUILD_CUDA=0 uv pip install --python .venv/bin/python --no-build-isolation -e src/sam2
for m in sam2.1_hiera_large.pt sam2.1_hiera_tiny.pt; do
  [ -s ckpt/$m ] || curl -sSL -o ckpt/$m https://dl.fbaipublicfiles.com/segment_anything_2/092824/$m
done
ls -la ckpt
echo "== licences"
head -3 src/sam2/LICENSE
grep -iE "licen[cs]ed under|apache" src/sam2/README.md | head -5
.venv/bin/python - <<'EOF'
import importlib.metadata as md
for p in ("open3d", "sam2", "torch", "opencv-python-headless"):
    m = md.metadata(p)
    print(p, md.version(p), "|", m.get("License"), "|", [c for c in m.get_all("Classifier") or [] if "License" in c])
EOF
ls .venv/lib/python3.11/site-packages/ | grep -i open3d
.venv/bin/python -c "import torch, open3d as o3d, cv2, sam2; print('OK', torch.__version__, torch.cuda.is_available(), o3d.__version__, cv2.__version__); o3d.t.geometry.RaycastingScene(); print('raycasting OK')"
