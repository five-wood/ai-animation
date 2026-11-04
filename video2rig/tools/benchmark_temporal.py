"""Small reproducible synthetic benchmark of legacy and temporal fitting.

Run on the server, without rendering or model inference::

    cd /data/video2rig/code
    /data/video2rig/.venv/bin/python tools/benchmark_temporal.py --quick --out /tmp/temporal.json
    /data/video2rig/.venv/bin/python tools/benchmark_temporal.py --unit-only

The default 2 s half-open window has 48/60/120 frames at 24/30/60 Hz.
--quick reduces optimiser iterations, NOT the physical signal or frame-rate set;
quick results are smoke-test results, not evidence of solver convergence.
No GT pose, GT depth or GT 3D position is passed into either solver. Both receive
identical 2D observations and observation-only uniform-confidence DLT. Legacy
always receives confidence .9; temporal uses .9 too unless --weights fused is
selected. Fused weights use observation evidence only, with absent heatmap
quality falling back to neutral factors. DLT stays identical to isolate weights.
Temporal initial=None deliberately exercises its own observation-based warm fit.

Root rotations use SO(3) Slerp; translations and unwrapped active local Euler
angles use linear interpolation. GT at every rate is recomputed by exact FK,
never by interpolating keypoint positions. The recorded Blender kp3d is read and
checked against source-frame FK before the benchmark starts. Noise and sparse
single-view outlier pulses are deterministic functions of physical time, not
independent random draws on each frame-rate grid.
"""
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import sys
import time
import unittest

import numpy as np
import torch
from scipy.spatial.transform import Rotation, Slerp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2r.geom import (Cams, Skeleton, euler_xyz_np, euler_xyz_t,
                      mat_to_euler_xyz_np, quat_to_mat_np)
from v2r.solve import fit as legacy_fit, triangulate_all
from v2r.solve_temporal import fit_temporal, rotation_acceleration_loss


FPS = (24, 30, 60)
SOURCE_FPS = 30.0


def interpolate_linear(source_t, values, target_t):
    """Time-axis interpolation, preserving all non-time dimensions."""
    flat = np.asarray(values).reshape(len(source_t), -1)
    out = np.stack([np.interp(target_t, source_t, c) for c in flat.T], axis=-1)
    return out.reshape((len(target_t),) + values.shape[1:])


def load_source(project, motion):
    paths = {"rig": project / "rig.json", "studio": project / "studio.json", "motion": motion}
    payload = {}
    fingerprints = {}
    for key, path in paths.items():
        raw = path.read_bytes()  # Real files only: no synthetic/fallback rig.
        payload[key] = json.loads(raw)
        fingerprints[key] = {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}
    rig, studio, motion_data = payload["rig"], payload["studio"], payload["motion"]
    if float(motion_data.get("fps", SOURCE_FPS)) != SOURCE_FPS:
        raise ValueError("This benchmark expects the source motion at exactly 30 fps")
    frames = motion_data["frames"]
    G = np.asarray([f["G"] for f in frames], dtype=float)
    quat = np.asarray([f["quat"] for f in frames], dtype=float)
    kp_recorded = np.asarray([f["kp3d"] for f in frames], dtype=float)
    sk = Skeleton(rig, device="cpu", dtype=torch.float64)
    if G.shape != (len(frames), 4, 4) or quat.shape != (len(frames), sk.B, 4):
        raise ValueError("Unexpected source G/quat shape")
    if len(frames) < 4 or not all(np.isfinite(a).all() for a in (G, quat, kp_recorded)):
        raise ValueError("Motion must have >=4 finite frames")
    if (np.linalg.norm(quat, axis=-1) < 1e-12).any():
        raise ValueError("Zero source quaternion")
    local_R = quat_to_mat_np(quat)  # Source uses Blender wxyz, NOT scipy xyzw.
    with torch.no_grad():
        _, source_kp = sk.fk_rot(torch.tensor(G), torch.tensor(local_R))
    if kp_recorded.shape != tuple(source_kp.shape):
        raise ValueError("Unexpected recorded kp3d shape")
    fk_err = float(np.max(np.abs(source_kp.numpy() - kp_recorded)) / sk.size)
    if fk_err > 1e-3:
        raise ValueError(f"Source FK differs from Blender kp3d by {fk_err:.6g} of size")
    # Inactive rotations cannot be represented by the fitter, so fail rather than
    # silently creating an unfair benchmark with unreachable ground truth.
    inactive = sorted(set(range(sk.B)) - set(sk.active))
    if inactive and np.max(np.abs(local_R[:, inactive] - np.eye(3))) > 1e-5:
        raise ValueError("Source has nonidentity rotations on inactive bones")
    eul = np.unwrap(mat_to_euler_xyz_np(local_R[:, sk.active]), axis=0)
    if not np.allclose(euler_xyz_np(eul), local_R[:, sk.active], atol=1e-6):
        raise ValueError("Source local Euler conversion is not a rotation round trip")
    source_t = np.arange(len(frames), dtype=float) / SOURCE_FPS
    return rig, studio, sk, source_t, G, eul, fingerprints, fk_err


def sample_gt(sk, source_t, G, eul, times):
    if times[0] < source_t[0] or times[-1] > source_t[-1] + 1e-9:
        raise ValueError("Requested interval exceeds source motion; extrapolation is forbidden")
    sampled_G = np.tile(np.eye(4), (len(times), 1, 1))
    sampled_G[:, :3, :3] = Slerp(source_t, Rotation.from_matrix(G[:, :3, :3]))(times).as_matrix()
    sampled_G[:, :3, 3] = interpolate_linear(source_t, G[:, :3, 3], times)
    sampled_eul = interpolate_linear(source_t, eul, times)
    limits = sk.limits[sk.active]
    excess = np.maximum(limits[..., 0] - sampled_eul, sampled_eul - limits[..., 1])
    if excess.size and excess.max() > 1e-4:
        raise ValueError("Interpolated local motion lies outside solver joint limits")
    world, kp = sk.fk_np(sampled_G, sampled_eul)
    return {"G": sampled_G, "eul": sampled_eul, "world": world, "kp": kp}


class ContinuousCorruption:
    """One seeded noise field and three short smooth pulses shared by all rates."""
    def __init__(self, views, joints, start, duration, seed, pixel_scale):
        rng = np.random.default_rng(seed)
        self.frequency = np.array([.65, 1.35, 2.4, 3.8])
        self.phase = rng.uniform(0, 2 * np.pi, (4, views, joints, 2))
        self.coeff = rng.normal(size=(4, views, joints, 2))
        # RMS approx .002 of projected body size (about 1 px for a 500 px body).
        self.coeff *= .002 * pixel_scale / np.sqrt(2.)
        self.events = []
        for fraction in (.24, .53, .79):
            direction = rng.normal(size=2)
            direction /= np.linalg.norm(direction)
            self.events.append({
                "time_seconds": float(start + fraction * duration),
                "half_width_seconds": .042,
                "view": int(rng.integers(views)),
                "joint": int(rng.integers(joints)),
                "offset_px": (direction * .075 * pixel_scale).tolist(),
            })

    def evaluate(self, times):
        angles = 2 * np.pi * times[:, None, None, None, None] * self.frequency[None, :, None, None, None]
        delta = (np.sin(angles + self.phase[None]) * self.coeff[None]).sum(axis=1)
        mask = np.zeros(delta.shape[:-1], dtype=bool)
        for event in self.events:
            x = (times - event["time_seconds"]) / event["half_width_seconds"]
            inside = np.abs(x) < 1
            bump = np.where(inside, .5 * (1 + np.cos(np.pi * np.clip(x, -1, 1))), 0)
            v, k = event["view"], event["joint"]
            delta[:, v, k] += bump[:, None] * np.asarray(event["offset_px"])
            mask[:, v, k] |= inside
        return delta, mask


def angular_acceleration(rotations, fps):
    """SO(3) body increment log differences / dt**2, rad/s**2 (not frame units).

    A physical reporting metric, independent of the solver's chordal surrogate
    and its 30 Hz coefficient normalisation. All bones contribute equally.
    """
    delta = np.swapaxes(rotations[:-1], -1, -2) @ rotations[1:]
    rv = Rotation.from_matrix(delta.reshape(-1, 3, 3)).as_rotvec()
    rv = rv.reshape(delta.shape[:-2] + (3,))
    return np.diff(rv, axis=0) * float(fps) ** 2


def rms_vector(x):
    return float(np.sqrt(np.mean(np.sum(np.asarray(x) ** 2, axis=-1))))


def metrics(sol, gt, sk, fps):
    err = np.linalg.norm(sol["kp"] - gt["kp"], axis=-1) / sk.size * 100
    # Active bone head->tail direction ignores unobservable axial twist.
    a = sk.bone_dirs(np.asarray(sol["world"]))[:, sk.active]
    b = sk.bone_dirs(gt["world"])[:, sk.active]
    cross = np.linalg.norm(np.cross(a, b), axis=-1)
    angle = np.degrees(np.arctan2(cross, np.sum(a * b, axis=-1)))
    result = {"joint_mean_pct_size": float(err.mean()),
              "joint_p90_pct_size": float(np.percentile(err, 90)),
              "bone_direction_mean_deg": float(angle.mean())}
    for label, R, gt_R in (
        ("root", sol["G"][:, :3, :3], gt["G"][:, :3, :3]),
        ("local", euler_xyz_np(sol["eul"]), euler_xyz_np(gt["eul"]))):
        acceleration = angular_acceleration(R, fps)
        gt_acceleration = angular_acceleration(gt_R, fps)
        result[f"{label}_rotation_accel_rms_rad_s2"] = rms_vector(acceleration)
        result[f"{label}_rotation_accel_gt_rms_rad_s2"] = rms_vector(gt_acceleration)
        result[f"{label}_rotation_accel_error_rms_rad_s2"] = rms_vector(acceleration - gt_acceleration)
    # The fitted global centre height (Blender Z up), not G's pivot-dependent
    # translation column and not a convenient body keypoint.
    def height(pose):
        return (np.einsum("tij,j->ti", pose["G"][:, :3, :3], sk.center)
                + pose["G"][:, :3, 3])[:, 2]
    h, hg = height(sol), height(gt)
    result.update(root_height_amplitude_pct_size=float(np.ptp(h) / sk.size * 100),
                  root_height_gt_amplitude_pct_size=float(np.ptp(hg) / sk.size * 100),
                  root_height_rmse_pct_size=float(np.sqrt(np.mean((h - hg) ** 2)) / sk.size * 100))
    return result


class RotationAccelerationTests(unittest.TestCase):
    """CPU-only checks: no files, CUDA or solver invocation required."""
    def test_analytic_signal_rate_invariance(self):
        losses = []
        for fps in FPS:
            t = torch.arange(2 * fps + 1, dtype=torch.float64) / fps
            # Fixed-axis theta=.3*t**2 has exactly constant angular acceleration.
            E = torch.stack((t * 0, t * 0, .3 * t.square()), dim=-1)
            losses.append(float(rotation_acceleration_loss(euler_xyz_t(E), fps)))
        expected = .6 ** 2 / 30. ** 4
        np.testing.assert_allclose(losses, expected, rtol=.003, atol=1e-12)
        self.assertLess(max(losses) / min(losses) - 1, .003)

    def test_constant_rotation_zero_finite_gradient(self):
        for fps in FPS:
            E = torch.tensor([.23, -.17, .41], dtype=torch.float64).repeat(9, 1).requires_grad_()
            R = euler_xyz_t(E)
            R.retain_grad()
            loss = rotation_acceleration_loss(R, fps)
            loss.backward()
            self.assertLess(abs(float(loss)), 1e-24)
            self.assertTrue(bool(torch.isfinite(R.grad).all()))
            self.assertTrue(bool(torch.isfinite(E.grad).all()))
            self.assertLess(float(E.grad.abs().max()), 1e-18)
        # Also guard the short-clip branch, where there is no acceleration sample.
        for frames in (1, 2):
            R = torch.eye(3, dtype=torch.float64).repeat(frames, 1, 1).requires_grad_()
            loss = rotation_acceleration_loss(R, 30)
            loss.backward()
            self.assertEqual(float(loss), 0.)
            self.assertTrue(bool(torch.isfinite(R.grad).all()))


def run_unit_tests():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RotationAccelerationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("rotation_acceleration_loss unit tests failed")
    return {"passed": True, "tests_run": result.testsRun}


def seed_everything(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def save_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=Path, default=Path("/data/video2rig/projects/lizard"))
    parser.add_argument("--motion", type=Path, help="Default: PROJECT/exam/frames/motion_gt.json")
    parser.add_argument("--out", type=Path, default=Path("benchmark_temporal.json"))
    parser.add_argument("--quick", action="store_true", help="Use short 120-iteration warm/legacy and 100-iteration refine")
    parser.add_argument("--unit-only", action="store_true", help="Run cheap CPU rotation-loss tests and exit")
    parser.add_argument("--mode", choices=("clean", "noise", "both"), default="both")
    parser.add_argument("--weights", choices=("uniform", "fused"), default="uniform",
                        help="Temporal observation weights only; legacy always uses uniform .9")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--duration", type=float, default=2., help="Seconds; >.5 and <=2 to keep each run <=120 frames")
    parser.add_argument("--start", type=float, default=0., help="Source start time, in seconds")
    parser.add_argument("--seed", type=int, default=20260714)
    parser.add_argument("--iters", type=int, help="Temporal refinement iterations (default 850, quick 100)")
    parser.add_argument("--warm-iters", type=int, help="Both baseline iterations and temporal warm iterations (default 900, quick 120)")
    parser.add_argument("--verbose", action="store_true", help="Show solver iteration logs")
    args = parser.parse_args(argv)
    unit_report = run_unit_tests()
    if args.unit_only:
        return 0
    if not np.isfinite(args.duration) or not .5 < args.duration <= 2.:
        parser.error("--duration must be finite, >.5 and <=2 seconds")
    if not np.isfinite(args.start) or args.start < 0:
        parser.error("--start must be finite and nonnegative")
    iters = args.iters if args.iters is not None else (100 if args.quick else 850)
    warm_iters = args.warm_iters if args.warm_iters is not None else (120 if args.quick else 900)
    if min(iters, warm_iters) <= 0:
        parser.error("Iteration counts must be positive")
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        parser.error("CUDA is unavailable; explicitly choose --device cpu for a CPU benchmark")
    # Reproducibility rather than maximum throughput; environment is recorded too.
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    seed_everything(args.seed)
    motion = args.motion or args.project / "exam" / "frames" / "motion_gt.json"
    rig, studio, sk, source_t, G, E, hashes, source_fk_error = load_source(args.project, motion)
    cams = Cams(studio, device="cpu")
    # Fixed reference grid defines pixel noise strength independently of test fps.
    reference_t = args.start + np.arange(int(np.ceil(args.duration * 60))) / 60.
    reference_t = reference_t[reference_t < args.start + args.duration - 1e-10]
    reference = sample_gt(sk, source_t, G, E, reference_t)
    reference_uv = np.moveaxis(cams.project_np(reference["kp"]), 0, 1)[..., :2]
    pixel_scale = float(np.median(np.ptp(reference_uv, axis=2).max(-1)))
    corruption = ContinuousCorruption(cams.V, sk.K, args.start, args.duration, args.seed, pixel_scale)
    common_t = args.start + np.arange(int(np.ceil(args.duration * 24))) / 24.
    # At most the last timestamp in each tested grid; never extrapolate to 24 Hz.
    last_time = min(args.start + (int(np.ceil(args.duration * f)) - 1) / f for f in FPS)
    common_t = common_t[(common_t <= last_time + 1e-10) & (common_t < args.start + args.duration - 1e-10)]
    report = {
        "schema": "temporal-benchmark-v1", "unit_tests": unit_report, "inputs": hashes,
        "config": {"fps": list(FPS), "source_fps": SOURCE_FPS, "start_seconds": args.start,
                   "duration_seconds": args.duration, "half_open_interval": True,
                   "seed": args.seed, "quick": args.quick, "device": args.device,
                   "legacy_iters": warm_iters, "temporal_warm_iters": warm_iters,
                   "temporal_iters": iters, "temporal_initial": None,
                   "weights": args.weights, "legacy_weights": "uniform 0.9 including outliers",
                   "temporal_weights": args.weights, "heat_quality": None, "depth": None,
                   "position_strength": 400., "local_strength": 24., "root_strength": 40.,
                   "preprocessing": "shared raw uniform-weight DLT; no cleanup or GT initialisation; optional temporal-only fused weights",
                   "source_fk_max_abs_pct_size": source_fk_error * 100,
                   "pixel_scale": pixel_scale, "outlier_events": corruption.events,
                   "noise_rms_fraction_pixel_scale": .002,
                   "common_24hz_times_seconds": common_t.tolist(),
                   "iteration_budget_note": "legacy baseline matches temporal warm only; temporal adds refinement and built-in polish"},
        "environment": {"python": sys.version, "numpy": np.__version__, "torch": torch.__version__,
                        "scipy": __import__("scipy").__version__, "cuda": torch.version.cuda,
                        "device_name": torch.cuda.get_device_name(torch.device(args.device)) if args.device.startswith("cuda") else "cpu",
                        "deterministic_algorithms": True},
        "runs": [], "cross_fps_24hz": [],
    }
    modes = ("clean", "noise") if args.mode == "both" else (args.mode,)
    for mode in modes:
        common_tracks = {"legacy": {}, "temporal": {}}
        gt_tracks = {}
        for fps in FPS:
            times = args.start + np.arange(int(np.ceil(args.duration * fps))) / fps
            times = times[times < args.start + args.duration - 1e-10]
            gt = sample_gt(sk, source_t, G, E, times)
            uv = np.moveaxis(cams.project_np(gt["kp"]), 0, 1)[..., :2]
            delta, outlier_mask = corruption.evaluate(times)
            if mode == "noise":
                uv = uv + delta
            weights = np.full(uv.shape[:-1], .9, dtype=float)
            X3, res = triangulate_all(cams, uv, weights)
            temporal_weights = weights
            if args.weights == "fused":
                # Optional ablation; unavailable heat-quality arrays stay neutral
                # via the API's documented fallback, not fabricated heatmaps.
                from v2r.observations import observation_weights
                temporal_weights, _ = observation_weights(rig, studio, uv, weights, res, fps,
                                                           heat_quality=None)
            gt_tracks[fps] = interpolate_linear(times, gt["kp"], common_t)
            for solver in ("legacy", "temporal"):
                seed_everything(args.seed)
                print(f"[benchmark] {mode} {fps}Hz T={len(times)} {solver}", flush=True)
                if args.device.startswith("cuda"):
                    torch.cuda.synchronize(torch.device(args.device))
                started = time.perf_counter()
                if solver == "legacy":
                    sol = legacy_fit(rig, studio, uv.copy(), weights.copy(), X3.copy(),
                                     depth=None, iters=warm_iters, device=args.device, verbose=args.verbose)
                else:
                    sol = fit_temporal(rig, studio, uv.copy(), temporal_weights.copy(), X3.copy(), fps=fps,
                                       depth=None, iters=iters, warm_iters=warm_iters, device=args.device,
                                       verbose=args.verbose, initial=None, position_strength=400.,
                                       local_strength=24., root_strength=40.)
                if args.device.startswith("cuda"):
                    torch.cuda.synchronize(torch.device(args.device))
                elapsed = time.perf_counter() - started
                if not all(np.isfinite(sol[k]).all() for k in ("G", "eul", "world", "kp")):
                    raise RuntimeError(f"Nonfinite {mode}/{fps}/{solver} result")
                m = metrics(sol, gt, sk, fps)
                used_weights = weights if solver == "legacy" else temporal_weights
                row = {"mode": mode, "fps": fps, "frames": len(times), "solver": solver,
                       "weight_mode": "uniform" if solver == "legacy" else args.weights,
                       "weight_min": float(used_weights.min()), "weight_mean": float(used_weights.mean()),
                       "seconds": elapsed, "outlier_observation_count": int(outlier_mask.sum()) if mode == "noise" else 0,
                       "observation_rms_px": float(np.sqrt(np.mean(delta ** 2))) if mode == "noise" else 0.,
                       "metrics": m}
                report["runs"].append(row)
                common_tracks[solver][fps] = interpolate_linear(times, sol["kp"], common_t)
                print(f"  joint={m['joint_mean_pct_size']:.3f}%size "
                      f"bone={m['bone_direction_mean_deg']:.3f}deg "
                      f"root/local accel={m['root_rotation_accel_rms_rad_s2']:.3f}/"
                      f"{m['local_rotation_accel_rms_rad_s2']:.3f}rad/s^2 "
                      f"height amp={m['root_height_amplitude_pct_size']:.3f}%size ({elapsed:.1f}s)", flush=True)
                save_report(args.out, report)  # Preserve completed runs if a later run is interrupted.
                del sol
        for solver, tracks in common_tracks.items():
            for a, b in itertools.combinations(FPS, 2):
                difference = tracks[a] - tracks[b]
                gt_difference = gt_tracks[a] - gt_tracks[b]
                # The small GT interpolation floor is reported explicitly; no
                # smoothing or nearest-frame alignment hides rate sensitivity.
                row = {"mode": mode, "solver": solver, "fps_pair": [a, b], "samples_24hz": len(common_t),
                       "joint_trajectory_mean_pct_size": float(np.linalg.norm(difference, axis=-1).mean() / sk.size * 100),
                       "joint_trajectory_rms_pct_size": rms_vector(difference) / sk.size * 100,
                       "gt_interpolation_floor_mean_pct_size": float(np.linalg.norm(gt_difference, axis=-1).mean() / sk.size * 100),
                       "error_trajectory_difference_mean_pct_size": float(np.linalg.norm(difference - gt_difference, axis=-1).mean() / sk.size * 100)}
                report["cross_fps_24hz"].append(row)
                print(f"[benchmark] {mode} {solver} {a}/{b}Hz @24Hz: "
                      f"{row['joint_trajectory_mean_pct_size']:.3f}%size "
                      f"(GT interp floor {row['gt_interpolation_floor_mean_pct_size']:.4f}%)", flush=True)
        save_report(args.out, report)
    print(f"[benchmark] wrote {args.out.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
