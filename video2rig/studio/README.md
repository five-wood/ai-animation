# 动作工坊（video2rig studio）

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

## 已知限制

- 用户上传角色的朝向默认 `-Y`（Blender 正面），朝向不对时需要管理员在内部看板用正确参数重新制作。
- 只有 FBX/GLB 模型可在网页里 3D 预览；`.blend` 角色显示示例动画（若有 `out/exam/exam.glb`），否则只显示参考图。
- GPU 队列一次只跑一个任务；排队时显示“前面还有 N 个任务”，不估算时间。
- 同一项目（角色）同时只能有一个任务，多用户使用同一个角色时会依次排队（服务自动重试提交）。
