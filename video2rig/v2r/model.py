"""DINOv2 joint reader: heatmap per joint + relative depth per joint + view embedding."""
import math
import torch
import torch.nn as nn
import torch.nn.functional as F
import timm


class Up(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        self.conv = nn.Sequential(nn.Conv2d(cin, cout, 3, padding=1), nn.GroupNorm(8, cout), nn.GELU(),
                                  nn.Conv2d(cout, cout, 3, padding=1), nn.GroupNorm(8, cout), nn.GELU())

    def forward(self, x):
        return self.conv(F.interpolate(x, scale_factor=2, mode="bilinear", align_corners=False))


class JointReader(nn.Module):
    def __init__(self, n_kp, n_views=2, backbone="vit_base_patch14_dinov2.lvd142m", pretrained=True,
                 img_size=(504, 448), layers=(-4, -3, -2, -1), dense=False, dense_decoder=False):
        """dense: also a per-pixel head (dense correspondence): 3 rest-pose surface coordinates (-1..1 in the body
        box), a foreground logit and a log-scale (uncertainty) per heatmap cell.  Off by default (old readers).
        dense_decoder: the dense head gets its own projection + upsampling (from the backbone features) instead of
        sharing the keypoint decoder, so it can be trained on a frozen keypoint reader (two-step)."""
        super().__init__()
        self.backbone = timm.create_model(backbone, pretrained=pretrained, num_classes=0,
                                          img_size=img_size, dynamic_img_size=True)
        self.layers = layers
        d = self.backbone.embed_dim
        self.view_emb = nn.Embedding(n_views, d)
        nn.init.zeros_(self.view_emb.weight)
        self.proj = nn.Sequential(nn.Conv2d(d * len(layers), 256, 1), nn.GroupNorm(8, 256), nn.GELU())
        self.up1 = Up(256, 192)
        self.up2 = Up(192, 128)
        self.hm = nn.Conv2d(128, n_kp, 1)
        self.dp = nn.Conv2d(128, n_kp, 1)
        nn.init.constant_(self.hm.bias, -4.0)
        self.dense = bool(dense)
        self.dense_decoder = bool(dense and dense_decoder)
        if self.dense:
            self.dn = nn.Sequential(nn.Conv2d(128, 64, 3, padding=1), nn.GroupNorm(8, 64), nn.GELU(),
                                    nn.Conv2d(64, 5, 1))  # xyz, foreground logit, log-scale
        if self.dense_decoder:
            self.dproj = nn.Sequential(nn.Conv2d(d * len(layers), 256, 1), nn.GroupNorm(8, 256), nn.GELU())
            self.dup1 = Up(256, 192)
            self.dup2 = Up(192, 128)
        self.n_kp = n_kp
        self.patch = 14

    def features(self, x, view):
        B, _, H, W = x.shape
        bb = self.backbone
        t = bb.patch_embed(x)
        t = bb._pos_embed(t)
        t = t + self.view_emb(view)[:, None, :]
        t = bb.norm_pre(t)
        n_blocks = len(bb.blocks)
        want = {(l % n_blocks) for l in self.layers}
        outs = []
        for i, blk in enumerate(bb.blocks):
            t = blk(t)
            if i in want:
                outs.append(bb.norm(t) if i == n_blocks - 1 else t)
        npt = bb.num_prefix_tokens
        h, w = H // self.patch, W // self.patch
        fs = [o[:, npt:].transpose(1, 2).reshape(B, -1, h, w) for o in outs]
        return torch.cat(fs, 1)

    def forward(self, x, view, with_dense=False, frozen_trunk=False):
        """frozen_trunk: the keypoint reader's own parts (backbone, keypoint decoder) run without gradients
        (two-step training of the dense part only)."""
        with torch.set_grad_enabled(torch.is_grad_enabled() and not frozen_trunk):
            f0 = self.features(x, view)
            f = self.up2(self.up1(self.proj(f0)))
        if with_dense:
            if not self.dense:
                raise ValueError("this reader has no dense head")
            g = self.dup2(self.dup1(self.dproj(f0))) if self.dense_decoder else f
            return self.hm(f), self.dp(f), self.dn(g)  # + (B,5,4h,4w)
        return self.hm(f), self.dp(f)  # (B,K,4h,4w) logits, depth


def gaussian_targets(uv_hm, valid, hh, hw, sigma=1.5):
    """uv_hm (B,K,2) in heatmap pixels -> (B,K,hh,hw) gaussians."""
    dev = uv_hm.device
    ys = torch.arange(hh, device=dev).float()[None, None, :, None]
    xs = torch.arange(hw, device=dev).float()[None, None, None, :]
    g = torch.exp(-((xs - uv_hm[..., 0, None, None]) ** 2 + (ys - uv_hm[..., 1, None, None]) ** 2) / (2 * sigma ** 2))
    return g * valid[..., None, None]


def heatmap_quality(hm_logits, radius=5):
    """Heuristic ambiguity and peak-local spread. Not visibility or correctness probability.

    Secondary maxima outside the main peak neighbourhood distinguish an isolated peak
    from two competing body parts. Local second moment describes broad/flat peaks.
    """
    B, K, H, W = hm_logits.shape
    p = torch.sigmoid(hm_logits.float())
    peak, idx = p.flatten(2).max(-1)
    py, px = idx // W, idx % W
    ys = torch.arange(H, device=p.device)[None, None, :, None]
    xs = torch.arange(W, device=p.device)[None, None, None, :]
    d2 = (ys-py[...,None,None]).square() + (xs-px[...,None,None]).square()
    local = d2 <= radius ** 2
    second = p.masked_fill(local, 0).flatten(2).max(-1).values
    mass = p * local
    spread = (mass*d2).sum((-1,-2)) / mass.sum((-1,-2)).clamp(min=1e-8)
    return {"ambiguity_ratio": second / peak.clamp(min=1e-8),
            "spread_heatmap_px": spread.sqrt()}


def decode(hm_logits, dp, win=2):
    """Heatmaps -> uv in heatmap px (sub-pixel), confidence, depth at peak."""
    B, K, H, W = hm_logits.shape
    p = torch.sigmoid(hm_logits)
    flat = p.reshape(B, K, -1)
    conf, idx = flat.max(-1)
    py, px = (idx // W).float(), (idx % W).float()
    # soft-argmax in a small window around the peak
    ys = torch.arange(H, device=p.device).float()
    xs = torch.arange(W, device=p.device).float()
    my = ((ys[None, None, :] - py[..., None]).abs() <= win).float()
    mx = ((xs[None, None, :] - px[..., None]).abs() <= win).float()
    m = my[..., :, None] * mx[..., None, :]
    w = p * m
    s = w.sum((-1, -2)).clamp(min=1e-8)
    u = (w * xs[None, None, None, :]).sum((-1, -2)) / s
    v = (w * ys[None, None, :, None]).sum((-1, -2)) / s
    d = (dp * (w / s[..., None, None])).sum((-1, -2))
    return torch.stack([u, v], -1), conf, d
