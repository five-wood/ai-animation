# 女娲-动作工坊（video2rig studio）

面向普通用户的网页平台：**http://10.0.3.117:8700**（systemd 服务 `video2rig-studio`）。
内部看板 http://10.0.3.117:8600 保持不变，两者共用同一个 GPU 任务队列。

## 用户能做什么

| 功能 | 位置 |
|---|---|
| 账号：注册 / 登录 / 修改密码；第一个注册的账号自动成为管理员 | 登录页、右上角头像菜单 |
| 浏览角色库，3D 预览 FBX（拖动旋转、缩放） | 角色库 → 点角色卡片 |
| 上传自己的 FBX（可连同贴图），提交制作（需管理员审核） | 角色库 → 上传角色 |
| 下载参考图、复制提示词（可填写动作描述） | 制作台左栏第 1 步 |
| 上传外部生成的视频 → 生成动画（提交任务、显示进度） | 制作台左栏第 2、3 步 |
| 预览：并排对比（原视频 + 3D）、3D 模型、拟合效果（识别关节 + 角色动作） | 制作台中间 |
| 关键帧：生成后自动标记；手动添加 / 删除 / 拖动 / 改类型 / 备注；补充过渡帧；自动保存 | 时间轴 + 右栏“关键帧” |
| 关键帧快捷键：K/Enter 添加、双击时间轴添加、Delete/右键删除、`,` `.` 微移、1–6 改类型、Ctrl+Z/Ctrl+Shift+Z 撤销重做、Ctrl+←→ 跳 10 帧、Home/End、`?` 快捷键表 | 时间轴 + 右栏“关键帧” |
| 导出关键帧列表（CSV / JSON：帧号、时间、类型、备注） | 右栏“关键帧”底部 |
| 导出 FBX / GLB / BVH：完整动画，或只保留关键帧（FBX/GLB，平滑 / 直线过渡）。BVH 格式每帧都是完整姿势，没有“只保留关键帧” | 右栏“导出” |
| 我的作品列表 | 顶栏“我的作品” |
| 管理：角色审核/公开/改名、用户停用/重置密码/设管理员、任务日志 | 顶栏“管理”（仅管理员） |

技术细节（求解器、两视角检查数值、项目名、日志）不展示给普通用户；失败时只给出能操作的建议
（换视频 / 仍然生成预览 / 重试）。管理员在“管理”页能看到内部项目名、结果名与日志。

## 架构

```
浏览器 ── :8700 studio/app.py (FastAPI, SQLite /data/video2rig/studio/studio.db)
             │  POST /api/jobs (animate / keyframes / pipeline)   ← 共用一个 GPU 队列
             └─▶ :8600 web/server.py ──▶ v2r.cli / run_all.sh / run_keyframes.py
             读取 /data/video2rig/jobs/*.json、projects/<p>/out/<结果>/
             关键帧导出：本服务直接调用 Blender（studio/blender_export_keys.py，CPU）
```

- 角色 = 已训练项目（`rig.json`、`studio.json`、`reference_two_views.png`、`model/best.pt` 齐全）。
  服务每分钟扫描 `projects/`，新项目以**不公开**状态登记；`STUDIO_PUBLISH` 里列出的项目自动公开，
  其余在“管理”页勾选公开。用户上传的角色使用 `st<id>_<随机>` 项目名，走 `pipeline`（prepare → gen → train → exam）。
- 作品输出到 `projects/<项目>/out/st_w<作品id>_<随机>/`，不会覆盖内部看板的结果；内部看板也能看到这些结果。
- 关键帧数据沿用 `docs/关键帧数据与网页接口约定_20261111.md`（`keyframes_auto.json` / `keyframes_artist.json`，带版本与 409）。
  Biped 角色用 `tools/max2026/run_keyframes.py`；其他骨架或其失败时，用通用的 `studio/auto_keys.py`
  （基于关键点速度：停顿、极值、急停 + 插值偏差排序的过渡帧阶梯）。
- “只保留关键帧”导出（`studio/blender_export_keys.py`）：视频第 f 帧 = 时间 f/fps（第一个键在 0 秒）。
  Blender 自带的 FBX 导出总是逐帧烘焙（它的 simplify 只去掉数值几乎不变的帧，3ds Max 里实测仍是每帧一个键），
  所以脚本临时替换导出器的取样掩码，只写关键帧那几帧，并把 FBX 的插值标志写成 cubic+auto 切线（或 linear）。
  2026-11-13 用 3ds Max 2026.2（`3dsmaxbatch`）实测：25 个关键帧的结果导入后每根骨骼正好 25 个 Bezier 键，
  切线 auto，键落在整数帧 0、11、14…192（场景设为 24fps 时）；中间帧是平滑插值。
  导入前请把 Max 场景帧率设成视频帧率（一般 24），否则键会落在小数帧上。
- 安全：密码 PBKDF2-SHA256（20 万次）；会话 Cookie HttpOnly + SameSite=Lax，14 天；修改类接口必须带
  `X-Studio: 1` 头（防跨站请求）；登录失败 10 分钟内 8 次限流；所有文件按用户隔离，路径限制在目录内。
  仅限内网使用，如需外网访问请放在 HTTPS 反向代理后并设 `STUDIO_COOKIE_SECURE=1`。

## 配置（`/data/video2rig/studio/studio.env`，权限 600）

| 变量 | 说明 |
|---|---|
| `STUDIO_ADMIN_USER` / `STUDIO_ADMIN_PASSWORD` | 启动时确保此管理员存在（首次部署自动生成随机密码） |
| `STUDIO_PUBLISH` | `项目:显示名,项目2:显示名`，首次发现时自动公开 |
| `STUDIO_OPEN_SIGNUP` | `1`（默认）开放注册；`0` 只能由管理员建号 |
| `STUDIO_INVITE_CODE` | 设置后注册需要邀请码 |
| `STUDIO_PREPARE_APPROVAL` | `1`（默认）用户上传角色需管理员审核才开始训练 |
| `STUDIO_MAX_ACTIVE_WORKS` / `STUDIO_MAX_PREPARING` | 每人同时生成中的作品数（默认 3）/ 制作中的角色数（默认 1） |
| `STUDIO_MAX_VIDEO_MB` / `STUDIO_MAX_MODEL_MB` | 上传大小上限（默认 300 / 400 MB） |
| `STUDIO_SOLVER` | `legacy`（代码默认）或 `temporal`。**线上 2026-11-13 起设为 `temporal`**（可信度融合＋联合时序平滑）；之前生成的作品不变 |

改完执行 `systemctl restart video2rig-studio`。

## 部署与运维

```powershell
.\video2rig\tools\deploy_studio.ps1      # 只上传 studio/，保留上一版为 code/studio.prev，重启服务并做健康检查
```

```bash
systemctl status video2rig-studio
journalctl -u video2rig-studio -f
# 回滚：cd /data/video2rig/code && rm -rf studio && mv studio.prev studio && systemctl restart video2rig-studio
```

数据：`/data/video2rig/studio/`（`studio.db`、`videos/`、`models/`、`exports/`）。

## 本地开发与测试（无需 GPU）

```powershell
.\work\studio-dev\run_local.ps1                         # 假引擎 + 夹具数据，http://127.0.0.1:8711 （admin/admin123）
python video2rig\tools\test_studio.py http://127.0.0.1:8711 ladingwu.mp4   # 41 项接口检查
node work\studio-dev\browser_check.cjs                  # 无头 Chrome 走完整流程并截图到 work/studio-dev/shots/
```

假引擎约定：原始文件名含 `bad` 的视频会模拟“两视角检查不通过”，文件名含 `noarm` 的模型会模拟“没有骨骼”。
夹具来自服务器 `m11101_tex/out/ladingwu_temporal`（`work/studio-dev/fixture/`，不进 git）。

## 上传角色的朝向（转正）

有些 FBX 导入后是躺着的（prepare 日志里的 `model is flat in Z`）或背对相机。用户在网页上转正后再提交制作：

1. 上传后，服务器用 Blender 按 `prepare` 完全相同的方式读模型（`studio/blender_inspect.py` 调用 `load_model(up="+Z")`，
   同样的骨架选择、道具删除、蒙皮修复），导出预览 `inspect.glb` 和报告（骨骼数、尺寸、是否躺着）。没有骨骼 / 没有蒙皮 /
   文件损坏会在这一步直接告诉用户，不会等到训练才失败。
2. 角色弹窗里的“调整朝向”：前后翻转、左右侧倒、水平转身、上下颠倒（每次 90°），地面黄色箭头 = 正面相机方向，
   可显示骨骼、切到正面视角；高度明显小于宽度时提示“看起来是躺着的”，提交时再确认一次。
3. 保存的是 3×3 整数旋转矩阵（three.js 坐标）。`studio/orient.py` 把它精确换算成 prepare 的 `--up`（哪根轴朝上）和
   `--facing`（立起来后朝哪边），24 种朝向一一对应（`tools/test_orient.py` 全部往返验证）。提交制作后锁定，不能再改。

2026-11-13 实测：把 `11101_nogun_exam_textured.fbx` 转成仰面躺着、头朝侧面的副本上传 → 网页上“水平转身 ×3、前后翻转 ×3”
→ 保存为 `--up=-X --facing=+X` → 真实 `prepare` 生成的参考图站立、正面朝前，和原模型的参考图一致。

## 重新制作与内部看板的 409

内部看板（8600）`POST /api/jobs` 会因为三种不同原因返回 409，工坊分开处理（`studio/engine.py` 的 `classify_409`）：

| 8600 的 409 原因 | 以前 | 现在 |
|---|---|---|
| 同一项目已有排队/运行中的任务 | 当作“稍后重试” | 不变：下一轮自动重新提交 |
| `pipeline` 投到已有 `rig.json` / `best.pt` 的项目（重新制作、或第一次制作在训练中途失败后重试） | 当作失败，角色直接变“制作失败” | **重新制作永远用新项目名**（`st<id>_<随机>`）；旧项目原样保留在磁盘上，旧作品照常可用 |
| `keyframes` 对已有候选的结果没带 `replace` | 退回到通用关键帧 | 自动带 `replace: true` 再提交一次（看板会把旧候选存为历史） |

重新制作（管理员“管理 → 角色 → 重新制作”，仅限用户上传、模型文件还在的角色）：在后台训练新项目，**训练期间角色照常可用**；
成功后自动切换到新项目，失败则保留原版本并在管理页标“上次重新制作失败”。训练服务器上的系统项目不在工坊里重训
（请在内部看板操作），以免覆盖现有识别器。第一次制作失败后的重试同样会自动换新项目，避免撞上残留的 `rig.json`。

## 3D 预览降级

three.js 由 `static/viewer-safe.js` 按需动态加载（`viewer.js` 才引用 `/vendor/*`），所以：

| 情况 | 页面表现 |
|---|---|
| 3D 库（`/vendor/*`）加载失败 | 只有 3D 区域显示“3D 预览暂时无法加载”+ 重试，底下淡显参考图；其余功能全部正常 |
| 模型文件（FBX/GLB）下载或解析失败 | 3D 区域显示“模型加载失败”+ 重试；若当时在“3D 模型”视图会自动切到“拟合效果”视频 |
| 浏览器没有 WebGL | 3D 区域显示“当前浏览器无法显示 3D”+ 换浏览器建议 |
| 运行中 WebGL 上下文丢失 | 提示刷新页面 |
| 页面主脚本 12 秒内没有启动 | 显示“页面没有正常加载”+ 刷新按钮（不会白屏） |

回归：`node work/studio-dev/no3d_probe.cjs`（本地假引擎，覆盖前四种情况）。

## 已知限制

- 用户上传角色的朝向默认 `-Y`（Blender 正面），朝向不对时需要管理员在内部看板用正确参数重新制作。
- 只有 FBX/GLB 模型可在网页里 3D 预览；`.blend` 角色显示示例动画（若有 `out/exam/exam.glb`），否则只显示参考图。
- GPU 队列一次只跑一个任务；排队时显示“前面还有 N 个任务”，不估算时间。
- 同一项目（角色）同时只能有一个任务，多用户使用同一个角色时会依次排队（服务自动重试提交）。
