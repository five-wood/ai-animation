"""Comparison video of an exam clip: input video | current temporal solve | temporal + perfect dense correspondence.
Each solved model is rendered (Open3D ray casting of the skinned mesh, simple shading) from both studio cameras with
the TRUE pose's outline drawn in green, plus per-frame limb / body orientation errors.

  surface_trial venv:  python dense_video.py <project> <tag> [<tag> ...]
Output: experiments/surface_solver/video/<project>_<tag>_dense.mp4 (+ a still of the frame with the largest gain).
"""
import glob
import json
import os
import subprocess
import sys

import cv2
import numpy as np
import torch

D = "/data/video2rig/experiments/surface_solver"
sys.path.insert(0, f"{D}/scripts")
import ab_multi  # noqa: E402  (puts the scratch code first on sys.path)
import ab_solver  # noqa: E402

mods = ab_solver.load_code(f"{D}/code")
import open3d as o3d  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from v2r.geom import Skeleton, quat_to_mat_np, load_surface, unit_rot_np  # noqa: E402

fonts = sorted(glob.glob("/usr/share/fonts/**/*CJK*Regular*.tt[cf]", recursive=True))
fonts = [f for f in fonts if "Sans" in f] or fonts
FONT = ImageFont.truetype(fonts[0], 22) if fonts else ImageFont.load_default()
FONT_S = ImageFont.truetype(fonts[0], 18) if fonts else ImageFont.load_default()
OUT = f"{D}/video"
os.makedirs(OUT, exist_ok=True)


def rays_of(cam):
    K, R, t = np.array(cam["K"]), np.array(cam["R"]), np.array(cam["t"])
    C = -R.T @ t
    u, v = np.meshgrid(np.arange(cam["w"]) + 0.5, np.arange(cam["h"]) + 0.5)
    d = np.stack([u, v, np.ones_like(u)], -1) @ np.linalg.inv(K).T @ R
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    return o3d.core.Tensor(np.concatenate([np.broadcast_to(C, d.shape), d], -1).astype(np.float32)), d


def render(V, F, rays, dirs, R):
    sc = o3d.t.geometry.RaycastingScene()
    sc.add_triangles(o3d.core.Tensor(V.astype(np.float32)), o3d.core.Tensor(F.astype(np.uint32)))
    ans = sc.cast_rays(rays)
    hit = np.isfinite(ans["t_hit"].numpy())
    n = ans["primitive_normals"].numpy()
    n = np.where(((n * dirs).sum(-1) > 0)[..., None], -n, n)  # face the camera
    nc = n @ R.T  # camera space: x right, y down, z forward
    light = np.array([-0.35, -0.55, -0.75])
    light /= np.linalg.norm(light)
    s = 0.30 + 0.70 * np.clip((nc * light).sum(-1), 0, 1)
    img = np.full(hit.shape + (3,), 205, np.float32)
    img[hit] = (np.array([150, 190, 222]) * s[hit, None])  # BGR beige
    return img.astype(np.uint8), hit


def outline(img, mask, color=(40, 200, 40)):
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(img, cs, -1, color, 2)
    return img


def per_frame_errors(sk, rig, w_gt, w_pr):
    act = sk.active
    a = sk.limb_dirs(w_gt)[:, act]
    b = sk.limb_dirs(w_pr)[:, act]
    limb = np.degrees(np.arccos(np.clip((a * b).sum(-1), -1, 1))).mean(1)
    bi = ab_multi.body_bone(rig)
    M = np.einsum("tji,tjk->tik", unit_rot_np(w_gt[:, bi, :3, :3]), unit_rot_np(w_pr[:, bi, :3, :3]))
    body = np.degrees(np.arccos(np.clip((np.trace(M, axis1=1, axis2=2) - 1) / 2, -1, 1)))
    return limb, body


def skin_all(sk, world, surf, chunk=10):
    out = []
    with torch.no_grad():
        for i in range(0, len(world), chunk):
            out.append(sk.skin_points(world[i:i + chunk].to(surf["V"].device), surf).cpu().numpy())
    return np.concatenate(out)


def text(img, xy, s, font=FONT, fill=(255, 255, 255), bg=None):
    im = Image.fromarray(img[..., ::-1])
    dr = ImageDraw.Draw(im)
    if bg is not None:
        x0, y0, x1, y1 = dr.textbbox(xy, s, font=font)
        dr.rectangle([x0 - 4, y0 - 3, x1 + 4, y1 + 3], fill=bg)
    dr.text(xy, s, font=font, fill=fill)
    return np.asarray(im)[..., ::-1].copy()


def main(proj, tags):
    for tag in tags:
        P = ab_multi.project_dir(proj)
        rig = json.load(open(f"{P}/rig.json"))
        studio = json.load(open(f"{P}/studio.json"))
        out_dir = f"{P}/out/{tag}"
        rd = {k: x for k, x in np.load(f"{out_dir}/reading.npz").items()}
        fps = float(np.load(f"{out_dir}/motion.npz")["fps"])
        gt = json.load(open(f"{P}/{tag}/frames/motion_gt.json"))
        sols = {}
        for name, kw in (("base", {}), ("dense", {"dense": {"uv": 1.0}})):
            torch.manual_seed(0)
            np.random.seed(0)
            sols[name] = ab_multi.solve_with_kwargs(mods, rig, studio, rd, fps, "temporal",
                                                    ab_multi.load_aux(kw, proj, tag))
            print(f"[video] {tag} solved {name}", flush=True)
        sk = Skeleton(rig)
        G = torch.tensor(np.array([f["G"] for f in gt["frames"]]), dtype=torch.float32)
        Rq = torch.tensor(quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]])), dtype=torch.float32)
        with torch.no_grad():
            w_gt, _ = sk.fk_rot(G, Rq)
        worlds = {"gt": w_gt, "base": torch.tensor(sols["base"]["world"]), "dense": torch.tensor(sols["dense"]["world"])}
        errs = {k: per_frame_errors(sk, rig, w_gt.numpy(), worlds[k].numpy()) for k in ("base", "dense")}
        sk_c = Skeleton(rig, device="cuda")
        surf = load_surface(f"{D}/surface/{ab_multi.base_project(proj)}.npz", device="cuda")
        Vs = {k: skin_all(sk_c, w, surf) for k, w in worlds.items()}
        F = surf["F"]
        cams = studio["cams"]
        rays = [rays_of(c) for c in cams]
        Rc = [np.array(c["R"]) for c in cams]
        cap = cv2.VideoCapture(f"{P}/{tag}/exam_video.mp4")
        T = len(gt["frames"])
        pw, ph = cams[0]["w"] // 2, cams[0]["h"] // 2
        head = 100
        W, H = 3 * pw, head + 2 * ph
        raw = f"{OUT}/{proj}_{tag}_dense.raw.mp4"
        vw = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
        gain = errs["base"][0] - errs["dense"][0]
        still_t = int(np.argmax(gain))
        title = ("考题（急停）" if tag == "exam_strike" else "考题（标准）")
        for t in range(T):
            ok, frame = cap.read()
            canvas = np.full((H, W, 3), 32, np.uint8)
            for v, c in enumerate(cams):
                x0 = 0
                inp = frame[:, v * c["w"]:(v + 1) * c["w"]] if ok else np.zeros((c["h"], c["w"], 3), np.uint8)
                _, hit_gt = render(Vs["gt"][t], F, rays[v][0], rays[v][1], Rc[v])
                tiles = [inp.copy()]
                for k in ("base", "dense"):
                    img, _ = render(Vs[k][t], F, rays[v][0], rays[v][1], Rc[v])
                    tiles.append(outline(img, hit_gt))
                for i, tile in enumerate(tiles):
                    tile = cv2.resize(tile, (pw, ph), interpolation=cv2.INTER_AREA)
                    if i > 0 and v == 0:
                        k = ("base", "dense")[i - 1]
                        tile = text(tile, (8, ph - 34), f"肢体 {errs[k][0][t]:4.1f}°  身体 {errs[k][1][t]:4.1f}°",
                                    font=FONT_S, bg=(0, 0, 0))
                    if i == 0:
                        tile = text(tile, (8, 6), "正面" if v == 0 else "侧面", font=FONT_S, bg=(0, 0, 0))
                    canvas[head + v * ph:head + (v + 1) * ph, x0 + i * pw:x0 + (i + 1) * pw] = tile
            labels = [("输入视频", f"{title}  第 {t + 1}/{T} 帧"),
                      ("现在的求解器", f"平均 肢体 {errs['base'][0].mean():.1f}° 身体 {errs['base'][1].mean():.1f}°"),
                      ("+ 完美稠密对应", f"平均 肢体 {errs['dense'][0].mean():.1f}° 身体 {errs['dense'][1].mean():.1f}°")]
            for i, (a, b) in enumerate(labels):
                canvas = text(canvas, (i * pw + 10, 6), a, font=FONT)
                canvas = text(canvas, (i * pw + 10, 40), b, font=FONT_S, fill=(200, 200, 200))
            canvas = text(canvas, (pw + 10, 70), "绿线 = 正确姿势的轮廓（越贴合越准）", font=FONT_S, fill=(80, 220, 80))
            vw.write(canvas)
            if t == still_t:
                cv2.imwrite(f"{OUT}/{proj}_{tag}_dense_f{t:03d}.png", canvas)
        vw.release()
        mp4 = f"{OUT}/{proj}_{tag}_dense.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-c:v", "libx264", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4], check=True)
        os.remove(raw)
        print(f"[video] {mp4}  still frame {still_t}: limb {errs['base'][0][still_t]:.1f} -> "
              f"{errs['dense'][0][still_t]:.1f}; clip mean limb {errs['base'][0].mean():.2f} -> "
              f"{errs['dense'][0].mean():.2f}, body {errs['base'][1].mean():.2f} -> {errs['dense'][1].mean():.2f}",
              flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
