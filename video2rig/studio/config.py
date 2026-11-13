"""Runtime configuration (environment variables) for the user-facing studio."""
import os

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.environ.get('V2R_ROOT', '/data/video2rig')
PROJ = os.path.join(ROOT, 'projects')
INPUTS = os.path.join(ROOT, 'inputs')
JOBS_DIR = os.path.join(ROOT, 'jobs')
DATA = os.environ.get('STUDIO_DATA', os.path.join(ROOT, 'studio'))
DB_PATH = os.path.join(DATA, 'studio.db')
UPLOAD_VIDEOS = os.path.join(DATA, 'videos')
UPLOAD_MODELS = os.path.join(DATA, 'models')
EXPORTS = os.path.join(DATA, 'exports')

# "v2r": submit to the internal dashboard (single GPU queue) and read its job files.
# "fake": local development without GPU/Blender (copies fixture outputs).
ENGINE = os.environ.get('STUDIO_ENGINE', 'v2r')
V2R_API = os.environ.get('V2R_API', 'http://127.0.0.1:8600')
FAKE_FIXTURE = os.environ.get('STUDIO_FAKE_FIXTURE', '')
FAKE_SPEED = float(os.environ.get('STUDIO_FAKE_SPEED', '1'))

BLENDER = os.environ.get('V2R_BLENDER', '/opt/unimate/blender-4.0.2-linux-x64/blender')
VENDOR = os.environ.get('STUDIO_VENDOR', os.path.join(CODE, 'web', 'static', 'vendor'))
KEYFRAME_SCRIPT = os.path.join(CODE, 'tools', 'max2026', 'run_keyframes.py')

INVITE_CODE = os.environ.get('STUDIO_INVITE_CODE', '')
OPEN_SIGNUP = os.environ.get('STUDIO_OPEN_SIGNUP', '1') == '1'
ADMIN_USER = os.environ.get('STUDIO_ADMIN_USER', '')
ADMIN_PASSWORD = os.environ.get('STUDIO_ADMIN_PASSWORD', '')
# "project:显示名,project2:显示名" published automatically the first time they are seen.
PUBLISH = os.environ.get('STUDIO_PUBLISH', '')
COOKIE_SECURE = os.environ.get('STUDIO_COOKIE_SECURE', '0') == '1'
DISABLE_WORKER = os.environ.get('STUDIO_DISABLE_WORKER', '0') == '1'

MAX_VIDEO_MB = int(os.environ.get('STUDIO_MAX_VIDEO_MB', '300'))
MAX_MODEL_MB = int(os.environ.get('STUDIO_MAX_MODEL_MB', '400'))
MAX_ACTIVE_WORKS_PER_USER = int(os.environ.get('STUDIO_MAX_ACTIVE_WORKS', '3'))
MAX_PREPARING_PER_USER = int(os.environ.get('STUDIO_MAX_PREPARING', '1'))
DEFAULT_SOLVER = os.environ.get('STUDIO_SOLVER', 'legacy')

for d in (DATA, UPLOAD_VIDEOS, UPLOAD_MODELS, EXPORTS):
    os.makedirs(d, exist_ok=True)
