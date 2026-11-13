"""Overlay the Max-evaluated skeleton (and the source fit) on the two-view video.

python overlay_video.py <run_dir> <source_dir> <video.mp4> [--frames 0,40,...] [--mp4]
Max samples (cm, Max world, 30fps target frames) -> source world units (/100) -> studio cameras.
The body is drawn WITH the per-frame lowering dz added back so it overlays the video; feet are drawn
where Max actually puts them (planted anchors), which is the intended contact correction.
Writes overlay_frames.png (contact sheet) and optionally overlay.mp4 (needs ffmpeg on PATH or FFMPEG env).
"""
from pathlib import Path
import argparse, json, os, subprocess, tempfile
import numpy as np
from PIL import Image, ImageDraw

CHAINS = [['Bip001 Pelvis', 'Bip001 Spine', 'Bip001 Spine1', 'Bip001 Neck', 'Bip001 Head'],
          ['Bip001 Spine1', 'Bip001 L Clavicle', 'Bip001 L UpperArm', 'Bip001 L Forearm', 'Bip001 L Hand'],
          ['Bip001 Spine1', 'Bip001 R Clavicle', 'Bip001 R UpperArm', 'Bip001 R Forearm', 'Bip001 R Hand'],
          ['Bip001 Pelvis', 'Bip001 L Thigh', 'Bip001 L Calf', 'Bip001 L Foot', 'Bip001 L Toe0'],
          ['Bip001 Pelvis', 'Bip001 R Thigh', 'Bip001 R Calf', 'Bip001 R Foot', 'Bip001 R Toe0'],
          ['Bip001 Head', 'Bone001', 'Bone002', 'Bone142'], ['Bip001 Head', 'Bone0013', 'Bone0021', 'Bone0022'],
          ['Bip001 Pelvis', 'Bone004']]


def col(rows):
    a = np.eye(4)
    a[:, :3] = np.array(rows, float)
    return a.T


def project(cam, X):
    K, R, t = np.array(cam['K']), np.array(cam['R']), np.array(cam['t'])
    Xc = X @ R.T + t
    uv = Xc @ K.T
    return uv[:, :2] / uv[:, 2:3] + np.array([cam['x0'], cam['y0']])


def frame_image(video, n, tmp):
    ff = os.environ.get('FFMPEG', 'ffmpeg')
    p = Path(tmp) / f'f{n:04d}.png'
    if not p.exists():
        subprocess.run([ff, '-v', 'error', '-y', '-i', str(video), '-vf', f'select=eq(n\\,{n})', '-vframes', '1', str(p)], check=True)
    return Image.open(p).convert('RGB')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run')
    ap.add_argument('source')
    ap.add_argument('video')
    ap.add_argument('--frames', default=None, help='target (30fps) frames for the contact sheet')
    ap.add_argument('--mp4', action='store_true')
    a = ap.parse_args()
    run, src = Path(a.run), Path(a.source)
    studio = json.loads((src / 'studio.json').read_text(encoding='utf8'))
    S = json.loads((run / 'max_samples.json').read_text(encoding='utf8'))
    d = np.load(run / 'legal_dense.npz')
    dzs = d['dzs'] if 'dzs' in d.files else np.full(len(d['frames']), float(d['dz']))
    nodes_t = [str(x) for x in d['nodes']]
    target = d['target']  # source fit in Max cm
    mo = np.load(src / 'motion_temporal.npz')
    src_fps, tgt_fps = float(mo['fps']), 30.0
    times = np.array(S['times'])
    idx_int = {int(round(t)): i for i, t in enumerate(times) if abs(t - round(t)) < 1e-6}
    T = len(d['frames'])
    frames = [int(x) for x in a.frames.split(',')] if a.frames else list(np.linspace(0, T - 1, 12).round().astype(int))

    def skeleton(f, which):
        pts = {}
        for ch in CHAINS:
            for n in ch:
                if n in pts:
                    continue
                if which == 'max':
                    M = col(S['samples'][idx_int[f]][n]) if n in S['samples'][0] else None
                    if M is None:
                        continue
                    p = M[:3, 3].copy()
                    if not n.endswith(('Foot', 'Toe0')):
                        p[2] += dzs[f]
                else:
                    if n not in nodes_t:
                        continue
                    p = target[f, nodes_t.index(n), :3, 3].copy()
                pts[n] = p / 100.0
        return pts

    def draw(img, f):
        dr = ImageDraw.Draw(img)
        for which, colr, w in [('src', (120, 120, 120), 5), ('max', (230, 40, 40), 2)]:
            pts = skeleton(f, which)
            for cam in studio['cams']:
                P = {n: project(cam, p[None])[0] for n, p in pts.items()}
                for ch in CHAINS:
                    seq = [P[n] for n in ch if n in P]
                    for p0, p1 in zip(seq[:-1], seq[1:]):
                        dr.line([tuple(p0), tuple(p1)], fill=colr, width=w)
        return img

    with tempfile.TemporaryDirectory() as tmp:
        tiles = []
        for f in frames:
            n = int(round(f * src_fps / tgt_fps))
            img = draw(frame_image(a.video, n, tmp), f)
            ImageDraw.Draw(img).text((10, 10), f'Max frame {f} / video frame {n}', fill=(0, 0, 0))
            tiles.append(img.resize((img.width // 2, img.height // 2)))
        cols = 3
        rows = int(np.ceil(len(tiles) / cols))
        W, H = tiles[0].size
        sheet = Image.new('RGB', (W * cols, H * rows), 'white')
        for i, im in enumerate(tiles):
            sheet.paste(im, ((i % cols) * W, (i // cols) * H))
        sheet.save(run / 'overlay_frames.png')
        print('wrote', run / 'overlay_frames.png')
        if a.mp4:
            ff = os.environ.get('FFMPEG', 'ffmpeg')
            n_src = int(mo['G'].shape[0])
            subprocess.run([ff, '-v', 'error', '-y', '-i', str(a.video), '-start_number', '0', str(Path(tmp) / 'f%04d.png')], check=True)
            for n in range(n_src):
                f = int(round(n * tgt_fps / src_fps))
                img = draw(frame_image(a.video, n, tmp), min(f, T - 1))
                img.save(Path(tmp) / f'o{n:04d}.png')
            subprocess.run([ff, '-v', 'error', '-y', '-framerate', str(int(src_fps)), '-i', str(Path(tmp) / 'o%04d.png'),
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '23', str(run / 'overlay.mp4')], check=True)
            print('wrote', run / 'overlay.mp4')


if __name__ == '__main__':
    main()
