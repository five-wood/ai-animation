# video2rig: animate any monster from an AI video

This reproduces the method from `Videos/AI Can Now Animate Any Monster. Opus 5.5 Built the Tool`:

> Don't animate the monster yourself. Film it with **two cameras in one picture** (front + side), let
> Seedance/Kling make the video, then **read every joint** with a DINOv2 reader taught on *this one monster*,
> make the **two rays cross** (triangulation) to get 3D, and put the move on the skeleton.

```
model (FBX/GLB, rigged)              ── v2r prepare ──▶ rig.json, studio.json, reference_two_views.png
                                                         (feed the reference picture to Seedance)
3000 random poses (inside limits,     ── v2r gen ─────▶ train_data/  (RGBA + exact 2D joints, depth, visibility)
 half of them fast = motion blur)
DINOv2 + heatmap/depth head           ── v2r train ───▶ model/best.pt (≈ 25 min on a 4090)
made-up known motion, same studio     ── v2r exam ────▶ exam/exam_report.json  (bone-by-bone error)
Seedance two-view video               ── v2r animate ─▶ out/<clip>/<clip>.fbx .glb .bvh .blend, lab.mp4, check.json
```

## Web dashboard

**http://10.0.3.117:8600** (systemd service `video2rig-web`, code in `web/`, starts automatically with the server)

| Page | Contents |
|---|---|
| 项目概览 | Current character, readiness, next action, preparation stages, recent results, downloadable two-view reference; rig details are collapsed. |
| 生成动画 | Current project is the sole target. Upload/select a video, compare reference and preview, explicitly acknowledge unbound or cross-project inputs; advanced/diagnostic options are collapsed and never silently remembered. |
| 动画结果 | Search/filter results, bounded equal-height video/3D comparison, unified exact-second timeline, frame stepping/speed, FBX-first grouped exports, anomaly jumps, collapsed technical diagnostics and stable-copy form. Duration mismatches are explicitly labeled; no time normalization. |
| 训练情况 | Conclusion, current-project task, training curves, guarded single-step operations, collapsed technical logs. |
| 训练数据 | Paged native-button thumbnails, blur filter, labels/augmentation, ←/→ stepping within the current page. |
| 验证报告 | Conclusion and next action, key metrics, bounded recording, per-bone error detail; automatic pass is not animation-quality acceptance. |

**新建角色** is an independent dialog; the existing full prepare → data → train → exam pipeline is retained. This first release does not claim pre-prepare model preview. **任务中心** is a global dialog with current/all-project scope, clear project ownership, logs, cancellation and result navigation. Browsing or refreshing does not cancel server jobs.

Web UI V1 regression commands (Node.js, no packages/network/GPU jobs):

```bash
node tools/test_run_page.cjs
node video2rig/tools/test_results.cjs
node video2rig/tools/test_workspace_routes.cjs
```

Static-only release packaging: `python video2rig/tools/package_web_ui.py`. This produces a content-addressed directory and manifest under `work/web-ui-v1/`; it never publishes algorithms. `tools/activate_web_ui.py` verifies a staged release and atomically replaces only the HTML entry when passed `--activate --expected-entry-sha256 <observed-current-entry-hash>`. It never restarts the job-owning Web service or retains a legacy entry. Only the active release is deployed; old entry backups, legacy root JS/CSS and intermediate preview releases have been removed. Shared Three vendor assets remain in place.

Project/video pairing:

- **新建角色** creates and trains a separate project. Existing prepared/trained projects cannot
  be silently reused for a different model; choose a new project name (empty failed projects may be retried).
- **生成动画** always uses the current project from the header. Reference and video previews
  are displayed together; unbound input requires explicit acknowledgement, mismatches require diagnostic confirmation.
- Refreshing the file list/tab preserves selection; successful uploads are selected automatically,
  failures are reported separately. Drafts persist per project in localStorage; force/matching confirmations do not.
- Videos can be manually associated via POST `/api/video-binding`, saved atomically in
  `/data/video2rig/video_bindings.json`. This is a user declaration, not automatic matching.
  A declared mismatch requires explicit confirmation, enforced server-side as well as in the UI.
  Unbound videos require acknowledgement in the UI; no trained `best.pt` means animation is blocked.
- `/api/videos/{name}/file` supports playback/range requests, `/thumbnail.jpg` serves its first frame.
- Tests: `tools/test_run_binding.py` (repository root) or deployed `code/tools/test_run_binding.py`.
  Twelve dry API tests cover bindings, paths, prerequisites and reuse guards; browser tests used
  isolated mock submit/upload interfaces, so no extra GPU jobs were started.

Service management:

```bash
systemctl status video2rig-web        # status
systemctl restart video2rig-web       # restart after updating code
journalctl -u video2rig-web -f        # logs
```

Uploaded models go to `/data/video2rig/inputs/`, videos to `/data/video2rig/videos/`, job records and logs to `/data/video2rig/jobs/`.
There is no login and it is LAN-only. Do not expose it to the internet.

## Where things live

* Code: `video2rig/` here; it is mirrored on the training server at `/data/video2rig/code`.
* Server: `ssh animsrv` (alias in `~/.ssh/config`: 10.0.3.117:43999, root, key `~/.ssh/ai_anim_server`).
* Python env: `/data/video2rig/.venv` (torch 2.7 cu128, timm DINOv2, opencv).
* Blender: `/opt/unimate/blender-4.0.2-linux-x64/blender`. Override it with `V2R_BLENDER`.
* Projects: `/data/video2rig/projects/<name>/`.

## Soft-clean lighting profile (independent new-material workflow)

New Web projects can select **柔光无地面投影**. CLI remains backward-compatible (`legacy`
is still its default):

```bash
$PY -m v2r.cli prepare model.fbx ../projects/new_clean --visual-profile soft-clean
# Controlled A/B: keep the old rig, labels, cameras exactly the same
$PY -m v2r.cli prepare ../inputs/lizard.blend ../projects/lizard_clean \
    --visual-profile soft-clean --geometry-from ../projects/lizard
```

`soft-clean` replaces two hard suns with three broad area lights and moderate ambient
fill, omits the reference floor/shadow receiver, and retains the character's form shading,
colour and texture. Generated training renders use the same lights with modest variation;
training compositing uses neutral grey and does **not** add cast shadows or dust. Exam
video compositing also follows the profile. The Web sample browser uses the matching
augmentation. Legacy outputs and projects remain unchanged.

Download the new project's reference picture and generate a **new** Seedance clip from it.
Changing the reference alone does not improve an old trained reader. Likewise, testing the
new reader on old shadowed videos measures compatibility, not the benefit of new shadow-free
video generation. Removing shadows cannot resolve hidden joints or prove better accuracy.
`lizard_clean` is the independent lizard experiment; the original `lizard` remains available.

Measured: clean project3000 poses/20epochs trained in25.5min; exam passed at0.761% mean
joint error and5.13° mean bone-direction error, comparable to old0.717%/5.10°. Direct
same-video cross-reader tests were slightly worse for the clean reader, both on old
shadowed real clips and on the clean synthetic exam. Do **not** replace the old reader
for old videos. On 2026-11-03, new shadow-free `xiyi3` was supplied. User initially ran
it in old `lizard`; matched `lizard_clean/out/xiyi3_clean` was generated for comparison.
Matched clean reading slightly reduced overall image residual/jitter, but late left ankle
world-step p90 worsened (~0.075%→0.210% body size) despite median reprojection improving
2.12→1.99px. No real GT and different source motion mean no causal claim; clean is **not a
recommended accuracy upgrade**. Web default has been returned to legacy and clean is
explicitly labelled experimental. Retain both for paired tests, not silent replacement.

## Steps per monster (one time)

Shortcut on the server: `bash /data/video2rig/code/run_all.sh /data/video2rig/inputs/11101_01.fbx m11101 [prepare options]`
runs prepare → gen → train → exam (about 35 min on the 4090).

From Windows: `.\video2rig\sync.ps1 -Model .\model\11101_01.fbx` uploads the model and its textures to `/data/video2rig/inputs/`.

```bash
cd /data/video2rig/code
PY=../.venv/bin/python
$PY -m v2r.cli inspect /data/video2rig/inputs/11101_01.fbx    # armature? skinned? textures found?
# 1. skeleton + studio + reference picture
$PY -m v2r.cli prepare /data/video2rig/inputs/11101_01.fbx ../projects/m11101 --facing=-Y
#    --facing: (write it as --facing=-Y; "--facing -Y" is rejected by argparse) the direction the creature looks in the model (Blender axes, default -Y = Blender "front")
#    --procedural-material: use this if the textures are missing (avoids pink renders)
#    props (meshes on their own root bone, e.g. m11101's guns) are removed by default: not in the reference
#                  picture, no keypoints, not exported; bones unchanged.  --no-drop-props keeps them.
#                  Projects prepared before this default keep their props (no "drop_props" in prepare_opts.json).
#    --limits limits.json: per-bone limits {"regex": [[xmin,xmax],[ymin,ymax],[zmin,zmax]]} in degrees
#    --min-seg 0.06: bones whose whole subtree stays within 6% of the body size (fingers, ears)
#                    are folded into their parent ("folds the fingers into the hand")
#    --keep-bones REGEX: never fold these bones
# 2. 3000 random poses from both cameras
$PY -m v2r.cli gen ../projects/m11101 --n 3000 --workers 10
# 3. teach DINOv2 this one monster
$PY -m v2r.cli train ../projects/m11101 --epochs 16
# 4. the exam: a made-up motion with known answers
$PY -m v2r.cli exam ../projects/m11101
```

If the model has **no skeleton**, rig it first. UniRig is installed in `/root/rigbench/UniRig`:
`bash launch/inference/generate_skeleton.sh --input model.obj --output skel.fbx` and then
`generate_skin.sh --input skel.fbx --output rigged.fbx`. The video's "pick the best of 4 tries" step is not automated here.

## Every new animation

1. Send `reference_two_views.png` to Seedance/Kling together with a prompt like
   *"Split screen, two locked-off studio cameras of the same creature: left = front view, right = side view.
   The camera does not move. Grey studio background. The creature <does the move>."*

   Always close the prompt with a "clean picture" clause in your own words, for example:
   *"Keep the image clean and sharp in every frame: the floor stays empty, nothing is kicked up or
   thrown off, there are no added effects, glows, streaks or blur, and every limb, hand, foot and held
   object stays fully visible."*
   Reason: the reader only ever saw clean renders. Generated dust, sparks, speed streaks or heavy blur
   over a hand or foot do not lower its confidence; it still reports a joint position there, often a
   wrong one. Regenerating a clip is cheaper than fixing those frames.
2. Run:

```bash
$PY -m v2r.cli animate ../projects/m11101 /path/seedance_clip.mp4 --name roar
```

* The video must be front-left / side-right, like the reference picture. Use `--swap` if it is reversed.
  The split is detected automatically. You can also use `--split half` or `--split 642`.
* **Check after joint detection, before solving:** the detected points must agree under the reference cameras.
  A failure is now `NEEDS_REVIEW`, not proof that Seedance animated two different moves: occlusion,
  reader mistakes, identity swaps, re-framing and truly inconsistent generated motion can all cause it.
  Even rejected runs keep `diagnostic.mp4` (red rings = high residual, yellow = other-view epipolar line),
  `diagnostics.json` (time ranges, per-joint statistics), `reading.npz`, `check.json`, and `summary.json`.
  The web Results page shows these without requiring a forced export. `--force` exports an approximate
  **diagnostic preview**, visibly marked as unverified; it does not make the check pass.
  Older animation files in a reused rejected output directory are hidden from the current web listing.
* `reading.npz`: `X` and `res` are paired check-stage triangulation outputs. `X_fit` / `conf_fit`
  are separately stored solver inputs after down-weighting. `uv_reader` is the original reader output;
  `uv_raw` is after optional calibration but before mirror-swap correction.
* Seedance starts from the reference picture, so the first frames show the rest pose. If the generator re-framed
  or scaled the video, `--calib auto` (default) uses those frames to correct each view's scale and shift.
* Output in `projects/<m>/out/<name>/`:
  - `<name>.fbx`, `.glb`, `.bvh`, `.blend`: the animated model, ready for Blender/Unity.
  - `lab.mp4`: the video with the joints read (top) and our model with the solved motion (bottom).
  - `motion.npz`, `reading.npz`, `check.json`, `summary.json`.

## Remove temporal jitter from an existing animation

A passed two-view check is a spatial consistency check, not a temporal stability guarantee.
For slow actions such as the xiyi2 squat, use Results → **生成稳定副本** (generate stable copy).
Choose slow/strong 1.5Hz, ordinary 2.5Hz, or fast/light 4Hz; a lower frequency smooths more.
The original animation is preserved and a new FBX/GLB/BVH/.blend + lab video is exported.

```bash
$PY -m v2r.cli stabilize ../projects/lizard xiyi2 --name xiyi2_stable --cutoff 1.5
```

This applies symmetric offline second-difference regularisation to all local rotations and
root motion together, followed by joint-limit enforcement and FK recomputation. It is not
per-bone-position blur: bone lengths stay fixed. Root rotations use sign-continuous normalised
quaternions; local channels use the solver's limited XYZ convention. There is no causal filter
lag, but endpoints and amplitude can change. `stability.json` records pre/post angular and
keypoint accelerations, reprojection errors and root-height amplitude/timing changes relative
to the **original solve**, not motion ground truth. Do not use strong settings on fast impacts.
No foot-contact locking or semantic detector-error correction is claimed. Existing output
names cannot be overwritten by this command. Dense exported curves now use LINEAR interpolation
to avoid default Bezier sub-frame overshoot.

## Experimental reliability fusion + joint temporal solve

Web Run → 求解模式 → **实验：可信度融合＋联合时序**. Legacy remains the default.

```bash
$PY -m v2r.cli animate ../projects/lizard ../videos/xiyi2.mp4 --name xiyi2_temporal --solver temporal
```

The experimental reader records heatmap ambiguity/spread. Bounds, original peak, robust
physical-time track innovations, projected-edge anomalies and mild symmetric ray disagreement
form heuristic weights (not visibility/correctness probabilities). The new joint refinement
regularises root/local relative-rotation increments and world/centre acceleration with fps²
scaling. No hardcoded bone names, frame intervals, automatic contact or post-filter are used.
The legacy warm-start remains and is also time-scaled for this mode. Identity swaps reorder
heatmap/depth metadata consistently. `observation_weights.npz` and `solver.json` audit the run.
Experimental output names cannot overwrite an existing solved motion.

On the full synthetic exam, joint error improved 0.717%→0.596% size and bone angle 5.10°→4.13°.
On real xiyi2, local Euler acceleration RMS fell about80%, with root height amplitude100.69%
of legacy and unchanged low-hold centre time; image error median2.14→2.08px, p90 8.94→9.67px.
However, controlled24/30/60fps noisy-GT tests show slightly worse joint/angle errors and
cross-rate positional consistency, despite more consistent rotational acceleration. Uniform
vs fused ablation was similar in this lightweight corruption test. Therefore this is **not**
a proven general upgrade or frame-rate-invariant solver and is not promoted to default.
Original inconsistent xiyi remains rejected46/121 frames; forced temporal output is diagnostic only.

Regression: `tools/test_observations.py`, `tools/test_temporal_integration.py`,
`tools/benchmark_temporal.py --unit-only`; full tests use `--weights fused --out report.json`
and a separate `--weights uniform --mode noise` ablation. Saved reports include hashes, seed,
GT interpolation floor and unequal iteration-budget limitations.

## Existing joint-depth head ablation (no new default)

`solve_temporal.fit_temporal` supports experimental `depth_mode="off"|"numeric"|"order"`;
current numeric `.025` remains default. `order` uses reciprocal mirror-pair signs with a
small-gap/low-weight/temporal-agreement abstention gate. It is not a visibility estimate,
a guarantee of identity, or a generic surface-depth model. Five depth-order unit tests
cover gates, sign gradients, nonfinite values and empty pairs.

`tools/benchmark_depth.py --out /data/video2rig/experiments/depth_order_20261103`
uses the exact same depth-OFF initial solve and observations per case across all modes.
Result: full known-motion mean joint error off/numeric/order0.59556/0.59566/0.59585%size,
bone error4.11285/4.11275/4.12387°. Real xiyi2/3 late left-hind motion was essentially
unchanged. 10x/100x depth strengths also showed no substantial benefit. Therefore no new
Web depth switch is promoted as a repair. Inspect lizard results `xiyi2_depth_off`,
`xiyi2_depth_numeric`, `xiyi2_depth_order` for the controlled visual comparison.
The legacy swap path still lacks aligned depth metadata; temporal aligns it, but a UV
identity correction can itself conflict with the depth head. Neither is independent GT.

## Source-image stationary-segment experiment

`v2r.visual_refine.visual_refine(project, source, name)` optionally refines an existing
solution against independent source-image motion evidence. It requires the original source
video at the same fps/frame count. Local forward/backward LK texture tracks determine
per-view/per-keypoint stationary intervals; no fixed bone names or handpicked frame ranges.
For supported intervals the 3D skeleton's projection is encouraged not to drift. It does
not set a manual world anchor, infer a ground plane, or assert physical foot contact.

The example `lizard/out/xiyi2_visual` reduces late left-hind screen drift relative to
`xiyi2_temporal_web`, while preserving root squat amplitude within0.3%. This remains an
opt-in experiment, NOT a default solver replacement: background/occlusion can mislead local
flow, thresholds depend on image scale, some global acceleration metrics worsen slightly,
and static pixels cannot establish a hidden joint's true location. `visual_refinement.json`
and `visual_evidence.npz` audit all detected intervals and changes. No interval => exact no-op.
Tests: `tools/test_image_motion.py` (static/moving/textureless/cumulative-drift).

## Explicit planted-foot refinement (operator-reviewed interval)

For xiyi2's residual late left-hind drift, a separate result `xiyi2_contact` refines only
`knee.L` and `hip.L`. Root and every other local channel remain unchanged. It fades a
positional anchor in from 3.0–3.5s and then holds the ankle head and toe marker to the same
quiet pose selected after 4.0s. No source output is overwritten.

```bash
$PY -m v2r.cli contact ../projects/lizard xiyi2_stable --name xiyi2_contact \
    --foot ankle.L --start 3 --fade .5 --reference 4
```

Use only after visually confirming that this foot is planted for the specified interval.
This is not automatic contact detection: running/jumping feet must not be pinned. Joint
limits remain enforced, but the contact target may be unreachable on a different motion.
It is a 2-point foot constraint, not full sole orientation, collision checking or physics.
Anchors preserve an existing quiet pose rather than proving the foot physically meets the
true ground surface. Check the `contact.json` residuals, local angle changes and lab video.

## How it works (files)

| step | file | what |
|---|---|---|
| rig + studio + reference | `v2r/blender/prepare.py`, `common.py` | finds the deforming bones and makes them keypoints (head and tip of each), left/right/mirror pairs, default joint limits. Front and side cameras share one scale. |
| random poses | `v2r/blender/gen_poses.py` | random rotations inside the limits plus body turn and jump. About 50% of the poses are fast: 3 keys, then EEVEE motion blur. Labels are exact projections, camera depth and ray-cast visibility. |
| "like Seedance" | `v2r/data.py` | studio sweep background, soft shadow, dust, colour/hue drift of the creature, blur, noise, down/up-scaling, JPEG |
| reader | `v2r/model.py`, `train.py` | DINOv2 ViT-B/14 (multi-layer features + view embedding), heatmap per joint, relative depth per joint |
| check + 3D | `v2r/solve.py` | per limb and per view near/far label swap fix (Viterbi over ray miss + bone length + continuity), DLT triangulation, "same move in both pictures?" check |
| on the skeleton | `v2r/solve.py`, `geom.py` | differentiable FK that is exactly Blender's (verified to 1e-7). It fits the global motion and every joint rotation inside its limits (tanh), with reprojection + 3D + depth-order + smoothness terms. |
| export | `v2r/blender/export_anim.py` | keys the bones, then writes FBX/GLB/BVH/.blend and the preview renders |
| exam | `v2r/exam.py` | a smooth random motion inside the limits is rendered in the studio and compressed, then read like a Seedance clip. Reports joint error (% of body size) and bone-direction angle error per bone. |

## Known limits (same as in the video)

* If Seedance draws two different moves in the two pictures, the tool can only follow one of them. That is why it rejects such clips.
* A joint hidden behind the body in **both** views is a guess, held up by the smoothness and joint-limit terms.
* Joint limits are generic defaults (±75°/±30°/±50° for limbs, ±35°/±20°/±30° for the spine). Tighten them with `--limits` for better results.
