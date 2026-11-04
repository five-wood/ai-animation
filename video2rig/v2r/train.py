"""Teach DINOv2 one new job: find every joint of this one monster."""
import os
import json
import time
import math
import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from v2r.data import PoseViews
from v2r.model import JointReader, gaussian_targets, decode


def train(project, epochs=40, batch=32, lr=3e-4, backbone="vit_base_patch14_dinov2.lvd142m", img=448,
          workers=12, sigma=2.0, device="cuda"):
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    tr = PoseViews(project, "train", target_w=img)
    va = PoseViews(project, "val", target_w=img)
    K = tr.K
    print(f"[v2r] train items={len(tr)} val items={len(va)} K={K} net={tr.W}x{tr.H} heat={tr.hw}x{tr.hh}")
    model = JointReader(K, n_views=len(tr.views), backbone=backbone, img_size=(tr.H, tr.W)).to(device)
    bb_params = list(model.backbone.parameters())
    head_params = [p for n, p in model.named_parameters() if not n.startswith("backbone.")]
    opt = torch.optim.AdamW([{"params": bb_params, "lr": lr * 0.1}, {"params": head_params, "lr": lr}],
                            weight_decay=0.03)
    steps = epochs * math.ceil(len(tr) / batch)
    warm = min(500, steps // 20)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * 0.5 * (1 + math.cos(math.pi * min(1.0, s / steps))))
    scaler = torch.cuda.amp.GradScaler()
    ckpt_dir = os.path.join(project, "model")
    os.makedirs(ckpt_dir, exist_ok=True)
    stride_x, stride_y = tr.W / tr.hw, tr.H / tr.hh
    log = open(os.path.join(ckpt_dir, "train_log.jsonl"), "a")
    best = 1e9
    t0 = time.time()
    step = 0
    for ep in range(epochs):
        tr.epoch = ep
        dl = DataLoader(tr, batch_size=batch, shuffle=True, num_workers=workers, drop_last=True,
                        pin_memory=True, persistent_workers=False)
        model.train()
        tl = []
        for b in dl:
            x = b["img"].to(device, non_blocking=True)
            view = b["view"].to(device)
            uv = b["uv"].to(device)
            inside = b["inside"].to(device)
            depth = b["depth"].to(device)
            uv_hm = torch.stack([uv[..., 0] / stride_x, uv[..., 1] / stride_y], -1) - 0.5
            with torch.autocast("cuda", dtype=torch.bfloat16):
                hm, dp = model(x, view)
            hm, dp = hm.float(), dp.float()
            tgt = gaussian_targets(uv_hm, inside, tr.hh, tr.hw, sigma)
            # focal-style weighted BCE on heatmaps
            pos_w = 1 + 40 * tgt
            l_hm = (F.binary_cross_entropy_with_logits(hm, tgt, reduction="none") * pos_w).mean()
            # depth: read at the true joint location
            g = tgt / tgt.sum((-1, -2), keepdim=True).clamp(min=1e-6)
            d_pred = (dp * g).sum((-1, -2))
            l_d = (F.smooth_l1_loss(d_pred, depth, reduction="none", beta=0.05) * inside).sum() / inside.sum().clamp(min=1)
            loss = l_hm * 10 + l_d
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            step += 1
            tl.append([l_hm.item(), l_d.item()])
            if step % 50 == 0:
                m = np.mean(tl[-50:], 0)
                print(f"[v2r] ep {ep} step {step}/{steps} hm {m[0]:.4f} depth {m[1]:.4f} "
                      f"{(time.time() - t0) / 60:.1f}min", flush=True)
        ev = evaluate(model, va, device, batch)
        rec = {"epoch": ep, "train_hm": float(np.mean(tl, 0)[0]), "train_depth": float(np.mean(tl, 0)[1]), **ev,
               "minutes": (time.time() - t0) / 60}
        log.write(json.dumps(rec) + "\n")
        log.flush()
        print(f"[v2r] == epoch {ep}: val px err median {ev['px_med']:.2f} p90 {ev['px_p90']:.2f} "
              f"(visible {ev['px_med_vis']:.2f}) depth-order acc {ev['depth_order']:.3f}", flush=True)
        state = {"model": model.state_dict(), "K": K, "views": tr.views, "backbone": backbone,
                 "net_wh": [tr.W, tr.H], "heat_wh": [tr.hw, tr.hh], "img": img, "epoch": ep, "val": ev}
        torch.save(state, os.path.join(ckpt_dir, "last.pt"))
        if ev["px_mean"] < best:
            best = ev["px_mean"]
            torch.save(state, os.path.join(ckpt_dir, "best.pt"))
    print(f"[v2r] training done in {(time.time() - t0) / 60:.1f} min, best val px mean {best:.2f}")


@torch.no_grad()
def evaluate(model, ds, device, batch):
    model.eval()
    dl = DataLoader(ds, batch_size=batch, shuffle=False, num_workers=8)
    errs, errs_vis, order = [], [], []
    sx, sy = ds.W / ds.hw, ds.H / ds.hh
    for b in dl:
        x = b["img"].to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            hm, dp = model(x, b["view"].to(device))
        uv, conf, d = decode(hm.float(), dp.float())
        uv = (uv + 0.5) * torch.tensor([sx, sy], device=device)
        e = (uv.cpu() - b["uv"]).norm(dim=-1) * b["px_scale"].float()[:, None]  # in panel pixels
        m = b["inside"] > 0
        errs.append(e[m])
        errs_vis.append(e[m & (b["vis"] > 0)])
        # pairwise depth ordering among mirror pairs (near vs far leg)
        for i, k in enumerate(ds.rig["keypoints"]):
            j = k["mirror"]
            if j > i:
                gt = b["depth"][:, i] - b["depth"][:, j]
                pr = d.cpu()[:, i] - d.cpu()[:, j]
                sel = gt.abs() > 0.02
                order.append((torch.sign(gt[sel]) == torch.sign(pr[sel])).float())
    e = torch.cat(errs).numpy()
    ev = torch.cat(errs_vis).numpy()
    o = torch.cat(order).numpy() if order else np.array([1.0])
    model.train()
    return {"px_mean": float(e.mean()), "px_med": float(np.median(e)), "px_p90": float(np.percentile(e, 90)),
            "px_med_vis": float(np.median(ev)), "depth_order": float(o.mean())}
