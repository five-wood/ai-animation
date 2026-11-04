"""Cameras, triangulation and a differentiable forward-kinematics skeleton.

The FK reproduces Blender's pose evaluation for standard bones
(inherit rotation + scale, no constraints):
    A_root = W^-1 G W M_root R_root
    A_i    = A_parent (M_parent^-1 M_i) R_i
    world  = W A
with M = bone.matrix_local (armature space), R = Euler-XYZ local rotation,
G = world-space global transform = T(c + t) Rg T(-c).
"""
import math
import numpy as np
import torch


# ----------------------------------------------------------------------------- cameras
class Cams:
    def __init__(self, studio, device="cpu"):
        self.studio = studio
        self.V = len(studio["cams"])
        self.K = np.array([c["K"] for c in studio["cams"]], float)
        self.R = np.array([c["R"] for c in studio["cams"]], float)
        self.t = np.array([c["t"] for c in studio["cams"]], float)
        self.P = np.einsum("vij,vjk->vik", self.K, np.concatenate([self.R, self.t[:, :, None]], 2))
        self.Kt = torch.tensor(self.K, dtype=torch.float32, device=device)
        self.Rt = torch.tensor(self.R, dtype=torch.float32, device=device)
        self.tt = torch.tensor(self.t, dtype=torch.float32, device=device)
        self.w, self.h = studio["panel_w"], studio["panel_h"]

    def project_np(self, X):
        """X (...,3) -> (V,...,3) [u,v,z]"""
        Xc = np.einsum("vij,...j->v...i", self.R, X) + self.t.reshape(self.V, *([1] * (X.ndim - 1)), 3)
        uv = np.einsum("vij,v...j->v...i", self.K, Xc)
        return np.concatenate([uv[..., :2] / uv[..., 2:3], Xc[..., 2:3]], -1)

    def project_t(self, X):
        """X (T,K,3) torch -> uv (T,V,K,2), z (T,V,K)"""
        Xc = torch.einsum("vij,tkj->tvki", self.Rt, X) + self.tt[None, :, None, :]
        uvw = torch.einsum("vij,tvkj->tvki", self.Kt, Xc)
        z = uvw[..., 2].clamp(min=1e-4)
        return uvw[..., :2] / z[..., None], Xc[..., 2]


def triangulate(P, uv, w):
    """P (V,3,4), uv (...,V,2), w (...,V) -> X (...,3) weighted DLT."""
    shp = uv.shape[:-2]
    V = uv.shape[-2]
    uvf = uv.reshape(-1, V, 2).astype(np.float64)
    wf = np.maximum(w.reshape(-1, V).astype(np.float64), 1e-3)
    # rows: w*(u P2 - P0), w*(v P2 - P1)   -> (N, 2V, 4)
    r0 = uvf[..., 0:1] * P[None, :, 2] - P[None, :, 0]
    r1 = uvf[..., 1:2] * P[None, :, 2] - P[None, :, 1]
    A = np.concatenate([r0 * wf[..., None], r1 * wf[..., None]], 1)
    # normalise for conditioning
    A = A / np.linalg.norm(A, axis=-1, keepdims=True).clip(1e-12) * np.concatenate([wf, wf], 1)[..., None]
    _, _, Vt = np.linalg.svd(A)
    X = Vt[:, -1]
    d = X[:, 3:4]
    d = np.where(np.abs(d) < 1e-12, 1e-12, d)
    return (X[:, :3] / d).reshape(*shp, 3)


def kabsch(A, B, w=None):
    """Find R,t minimising sum w |R A + t - B|^2.  A,B (N,3)."""
    if w is None:
        w = np.ones(len(A))
    w = w / max(w.sum(), 1e-9)
    ca, cb = (w[:, None] * A).sum(0), (w[:, None] * B).sum(0)
    H = ((A - ca) * w[:, None]).T @ (B - cb)
    U, S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1, 1, d])
    R = Vt.T @ D @ U.T
    return R, cb - R @ ca


# ----------------------------------------------------------------------------- rotations
def euler_xyz_np(e):
    """Blender Euler XYZ (radians) -> matrix = Rz Ry Rx."""
    x, y, z = e[..., 0], e[..., 1], e[..., 2]
    cx, sx, cy, sy, cz, sz = np.cos(x), np.sin(x), np.cos(y), np.sin(y), np.cos(z), np.sin(z)
    R = np.stack([
        cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz,
        cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz,
        -sy, sx * cy, cx * cy], -1)
    return R.reshape(*e.shape[:-1], 3, 3)


def euler_xyz_t(e):
    x, y, z = e[..., 0], e[..., 1], e[..., 2]
    cx, sx, cy, sy, cz, sz = torch.cos(x), torch.sin(x), torch.cos(y), torch.sin(y), torch.cos(z), torch.sin(z)
    R = torch.stack([
        cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz,
        cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz,
        -sy, sx * cy, cx * cy], -1)
    return R.reshape(*e.shape[:-1], 3, 3)


def mat_to_euler_xyz_np(R):
    """Inverse of euler_xyz_np (Blender XYZ)."""
    sy = np.clip(-R[..., 2, 0], -1, 1)
    y = np.arcsin(sy)
    x = np.arctan2(R[..., 2, 1], R[..., 2, 2])
    z = np.arctan2(R[..., 1, 0], R[..., 0, 0])
    return np.stack([x, y, z], -1)


def rot6d_to_mat(x):
    a1, a2 = x[..., 0:3], x[..., 3:6]
    b1 = torch.nn.functional.normalize(a1, dim=-1)
    b2 = torch.nn.functional.normalize(a2 - (b1 * a2).sum(-1, keepdim=True) * b1, dim=-1)
    b3 = torch.cross(b1, b2, dim=-1)
    return torch.stack([b1, b2, b3], -1)  # columns


def mat_to_rot6d(R):
    return np.concatenate([R[..., :, 0], R[..., :, 1]], -1)


def mat_to_quat_np(R):
    """(...,3,3) -> (...,4) wxyz"""
    R = np.asarray(R)
    shp = R.shape[:-2]
    Rf = R.reshape(-1, 3, 3)
    q = np.zeros((Rf.shape[0], 4))
    for i, m in enumerate(Rf):
        tr = m[0, 0] + m[1, 1] + m[2, 2]
        if tr > 0:
            s = math.sqrt(tr + 1.0) * 2
            q[i] = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
        elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
            s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
            q[i] = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
        elif m[1, 1] > m[2, 2]:
            s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
            q[i] = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
        else:
            s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
            q[i] = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    # keep a continuous sign along the first axis (time)
    q = q.reshape(*shp, 4)
    return q


def quat_to_mat_np(q):
    q = np.asarray(q, float)
    q = q / np.linalg.norm(q, axis=-1, keepdims=True)
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    R = np.stack([
        1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
        2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
        2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)
    return R.reshape(*q.shape[:-1], 3, 3)


def global_from_params(Rg, tr, c):
    """Rg (T,3,3) torch, tr (T,3), c (3,) -> G (T,4,4) = T(c+tr) Rg T(-c)."""
    T = Rg.shape[0]
    G = torch.zeros(T, 4, 4, dtype=Rg.dtype, device=Rg.device)
    G[:, :3, :3] = Rg
    G[:, :3, 3] = c + tr - torch.einsum("tij,j->ti", Rg, c)
    G[:, 3, 3] = 1
    return G


# ----------------------------------------------------------------------------- skeleton
class Skeleton:
    def __init__(self, rig, device="cpu", dtype=torch.float32):
        self.rig = rig
        bones = rig["bones"]
        self.B = len(bones)
        self.parent = [b["parent"] for b in bones]
        M = np.array([b["rest"] for b in bones], float)
        self.W = np.array(rig["world_matrix"], float)
        self.Winv = np.linalg.inv(self.W)
        off = np.zeros_like(M)
        for i, p in enumerate(self.parent):
            off[i] = (np.linalg.inv(M[p]) @ M[i]) if p >= 0 else M[i]
        self.M = M
        self.length = np.array([b["length"] for b in bones], float)
        self.active = [i for i, b in enumerate(bones) if b["active"]]
        self.A = len(self.active)
        lim = np.array([b["limits"] for b in bones], float)  # (B,3,2) degrees
        self.limits = np.radians(lim)
        kps = rig["keypoints"]
        self.K = len(kps)
        self.kp_bone = np.array([k["bone"] for k in kps])
        self.kp_tail = np.array([k["type"] == "tail" for k in kps])
        self.kp_tail_len = np.array([(k.get("tail_len", bones[k["bone"]]["length"]) if k["type"] == "tail" else 0.0)
                                     for k in kps], float)
        self.center = np.array(rig["center"], float)
        self.size = float(rig["size"])
        self.rest_kp = np.array([k["rest_world"] for k in kps], float)
        t = lambda a: torch.tensor(a, dtype=dtype, device=device)
        self.t_off, self.t_W, self.t_Winv = t(off), t(self.W), t(self.Winv)
        self.t_len = t(self.length)
        self.t_lim = t(self.limits[self.active])
        self.t_center = t(self.center)
        self.t_kp_bone = torch.tensor(self.kp_bone, device=device)
        self.t_kp_tail = torch.tensor(self.kp_tail, device=device)
        self.t_kp_tail_len = t(self.kp_tail_len)
        self.device, self.dtype = device, dtype

    def fk(self, G, eul_active):
        """G (T,4,4) torch, eul_active (T,A,3) radians -> world (T,B,4,4), kps (T,K,3)."""
        T = G.shape[0]
        E = torch.zeros(T, self.B, 3, dtype=self.dtype, device=self.device)
        if self.A:
            E[:, self.active] = eul_active
        return self.fk_rot(G, euler_xyz_t(E))

    def fk_rot(self, G, R):
        """G (T,4,4), R (T,B,3,3) local rotations -> world (T,B,4,4), kps (T,K,3)."""
        T = G.shape[0]
        R4 = torch.zeros(T, self.B, 4, 4, dtype=self.dtype, device=self.device)
        R4[..., :3, :3] = R
        R4[..., 3, 3] = 1
        A = [None] * self.B
        GW = torch.einsum("ij,tjk,kl->til", self.t_Winv, G, self.t_W)
        for i in range(self.B):
            p = self.parent[i]
            if p < 0:
                A[i] = GW @ self.t_off[i] @ R4[:, i]
            else:
                A[i] = A[p] @ self.t_off[i] @ R4[:, i]
        A = torch.stack(A, 1)
        world = torch.einsum("ij,tbjk->tbik", self.t_W, A)
        Wk = world[:, self.t_kp_bone]  # (T,K,4,4)
        tail_local = torch.zeros(self.K, 4, dtype=self.dtype, device=self.device)
        tail_local[:, 1] = self.t_kp_tail_len
        tail_local[:, 3] = 1
        kp = torch.einsum("tkij,kj->tki", Wk, tail_local)[..., :3]
        return world, kp

    def fk_np(self, G, eul_active):
        with torch.no_grad():
            w, k = self.fk(torch.tensor(G, dtype=self.dtype, device=self.device),
                           torch.tensor(eul_active, dtype=self.dtype, device=self.device))
        return w.cpu().numpy(), k.cpu().numpy()

    def local_quats(self, eul_active):
        """(T,A,3) rad -> (T,B,4) wxyz for every bone (identity for inactive / roots)."""
        T = eul_active.shape[0]
        E = np.zeros((T, self.B, 3))
        E[:, self.active] = eul_active
        q = mat_to_quat_np(euler_xyz_np(E))
        return q

    def bone_dirs(self, world):
        """world (T,B,4,4) -> unit head->tail directions (T,B,3)."""
        y = world[..., :3, 1]
        return y / np.linalg.norm(y, axis=-1, keepdims=True).clip(1e-9)
