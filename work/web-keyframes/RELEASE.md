# video2rig Web：关键帧页上线记录（2026-11-12）

## 第二次上线（11:49–11:54）：网页上的“生成候选关键帧”按钮

**功能**

- 关键帧页右上角新增“生成候选关键帧”按钮，已有候选时显示为“重新生成候选”。点击后，网页任务队列新增一个 `keyframes` 任务，依次执行三步：判断着地 → 抽取候选 → 计算过渡键阶梯。只用 CPU，每个结果约 1 秒。
- 页面会显示排队和运行的阶段（1/3、2/3、3/3），完成后自动载入新候选。任务中心里显示为“生成候选关键帧”，点“查看关键帧”可以跳回关键帧页。
- 以下情况按钮不可用，并直接写明原因：
  - 角色不是 Biped（dog、lizard 等）；
  - 结果缺少 `motion.npz` 或 `reading.npz`（例如 `ladingwu_dense3`）。
- 过渡键阶梯只对 m11101 这副骨架计算（使用部署过去的 bind profile）。骨架对不上时，只生成候选结构键，并说明原因。
- 重新生成需要确认，后端也会检查：没有 `replace=true` 就返回 409。
  - 旧的候选文件存到 `keyframes_auto_history/`；
  - `contacts_auto.json` 和 `contacts_plan.json` 被覆盖，它们是判断依据，标记为未审核；
  - `keyframes_artist.json` 不会被改动。
- 美术已有的标注如果基于旧候选，打开时会自动转换：
  - 所有标注帧都保留；
  - 新候选里同 id、同原始帧的条目，仍记为候选来源；
  - 其他条目改记为手动关键帧；
  - 页面提示转换结果，并标记为未保存，由美术确认后保存。
  - 如果新旧候选完全一致，则不做转换。

**服务器改动**

- 新目录 `code/tools/max2026/`，之前不存在：包括 `run_keyframes.py`、`extract_keyframes.py`、`detect_contacts.py`、`breakdown_ladder.py`、`build_spline_edit.py`、`plan_spline_keys.py` 和 `profiles/m11101_bind_inspection.json`。压缩包哈希 `7ff16e5b…`，各文件哈希与本地一致。安装记录在 `web-release-backups/tools-max2026/record.json`。
- 后端：`server.py`（`ce815804…`）新增 `keyframes` 任务和 `generation` 字段；`task_monitor.py`（`b4e50378…`）新增阶段标记。通过 `deploy_web_batch.py` 部署：空闲检查做了两次，Web 服务重启一次，11:49:53 启动，PID `1869223`。备份在 `web-release-backups/ui-v1-375e02edc095/`。
- 前端：先发布 `ui-v1-375e02edc095`。之后在线上发现，生成完成后结果下拉框仍显示“无候选”，修正后发布 `ui-v1-290ca5592cf5`。这次只用 `activate_web_ui.py` 切换静态入口，没有重启服务，PID 仍是 `1869223`。旧的两个静态版本都已移到对应备份的 `previous-static/` 下，公共路径返回 404。
- 数据：在线上实际点按钮生成了两个结果的候选。结果目录里只新增了 `keyframes_auto.json`、`contacts_auto.json`、`contacts_plan.json` 三个文件，原有文件逐个核对哈希，都没有变化：
  - `m11101_nogun/out/112_temporal`：24 个候选，18 个过渡键；
  - `m11101/out/333`：38 个候选，7 个过渡键。

**验证**

- 本地：
  - Node 测试 13 项和后端测试 23 项都通过，原有测试全部通过；
  - 本地预览里用真实脚本跑通了完整流程：生成 → 保存 → 重新生成（确认）→ 自动转换标注 → 非 Biped 角色显示禁用和原因。
- 服务器隔离环境：
  - 4 组测试通过；
  - 用生产结果的副本运行脚本：ladingwu_temporal 和已部署的候选完全一致（帧号和阶梯都相同）；112_temporal 和 333 能正常生成；dense3（缺少 reading.npz）和 lizard（非 Biped）都给出了明确的失败原因。
- 线上：
  - 接口返回的可生成状态，与上面每个结果的实际情况一致；
  - 两个拦截都生效：已有候选但没有 `replace` 返回 409，非 Biped 角色返回 400，都没有创建任务；
  - 浏览器里实际点击生成，约 5 秒载入，下拉框变为“有候选”，任务中心显示正常；
  - 原有页面复查通过，没有 JS 错误。

**已知情况**

- 333 这段视频有 110 帧被判定为快速转身，过渡键阶梯只排出 7 个，p95 仍有 4.9cm。112_temporal 转圈段的最大偏差是 55.8cm。转圈段需要美术处理，这在页面上已经标明。
- 着地判断没有人工确认，接触类候选仅供参考。

**回退**（先确认没有任务在跑）

1. 用 `web-release-backups/ui-v1-375e02edc095/` 里的 `server.py`、`task_monitor.py` 覆盖线上文件；
2. 把同一备份 `previous-static/` 下的 `ui-v1-37a15bf91586` 复制回 `static/releases/`，用备份里的 `index.html` 覆盖入口；
3. 重启一次服务。

`tools/max2026/` 留着也不会被旧代码引用，可以保留；已经生成的候选文件是数据，回退时不要删除。

---

## 范围

- 新增“关键帧”页：用来预览候选关键帧，美术在视频上自定义标注并保存。接口约定见 `video2rig/docs/关键帧数据与网页接口约定_20261111.md`，第七节是实现状态。
- 后端新增 `web/keyframes_store.py`。`web/server.py` 增加两个接口，并在 outputs 每一项里增加 `has_keyframes`。
- 前端新增 `keyframes.js` 和 `keyframes-core.js`；改动了 `app.js`、`results.js`、`index.html`、`app.css`。
  - 其中 `app.js` 统一错误处理有一处改动：后端返回的错误详情是对象时，提示里显示其中的说明文字，而不是原始 JSON；同时把完整的 `detail` 保留在错误对象上。
- 不涉及算法代码、模型、训练数据或任务队列。`v2r/`、`tools/` 没有改动。

## 上线结果

- 生效静态版本：`ui-v1-37a15bf91586`，主地址 `http://10.0.3.117:8600/`。
- 部署方式：沿用 `video2rig/tools/deploy_web_batch.py`。白名单里加入了 `keyframes_store.py` 和两个新的 js 文件，上线后多检查一项：关键帧接口已经注册。
  - 部署前核对线上文件哈希：`server.py`（`5ae3f350…`）和入口 `index.html`（`cedfcef1…`）与预期一致，说明线上就是 `HEAD` 版本。
  - 空闲检查做了两次，Web 队列和命令行任务都是空的。
  - 只重启了一次 Web 服务。上线时间 10:20:59，新的主进程 PID 为 `1486383`。
- 上线后线上文件哈希：`server.py` 为 `fe1c6a20…`，`keyframes_store.py` 为 `b8de3bfd…`，`index.html` 为 `5b4bdd70…`；`project_store.py`、`task_monitor.py` 没有变化。
- 数据：只在 `projects/m11101_tex/out/ladingwu_temporal/` 下新增了 `keyframes_auto.json`（`bbd1f063…`）。
  - 这个文件是本地用 `extract_keyframes.py` 0.2 生成的。生成用的输入 `motion_temporal.npz`、`reading.npz`、`summary.json`，和服务器上的 `motion.npz`、`reading.npz`、`summary.json` 哈希逐一相同。
  - 该目录下原有文件都没有改动。服务器上没有 `tools/max2026`，所以没有在服务器上运行抽取。
- 操作失误一次：安装候选文件时，命令里的引号写错，往 `/keyframes_auto.json` 多写了一份副本。改用脚本后正确安装，并在核对哈希后删除了这份副本。

## 验证

- 服务器隔离环境（`/tmp`，使用正式的 Python 环境）：
  - `test_web_keyframes` 17 项，通过（其中 1 项读本地样例，在服务器上跳过）；
  - `test_web_tasks` 6 项、`test_project_store`、`test_task_monitor` 均通过；
  - 用真实样例经 `keyframes_store.view` 读出：fps 24，193 帧，34 个候选，建议过渡键 16 个。
- 线上 HTTP：
  - 入口指向新版本；新版本的 js 和 css 都返回 200；旧版本的公共路径返回 404。
  - 关键帧接口返回 34 个候选，过渡键阶梯数据正常，视频地址正确；视频按范围请求返回 206；路径穿越被拒绝（404）。
  - 11 个项目都在；`/api/tasks` 的 monitor 状态可用，没有活动任务；`/api/capabilities` 与上线前一致。
- 线上浏览器检查（`work/web-keyframes/prod_check.cjs`，无头 Chrome）：
  - 1920 和 1366 宽度下，34 张卡片、滑杆 16、缩略图和视频都正常，没有横向溢出；Shift+→ 能跳到下一个关键帧；
  - 项目概览、动画结果、生成动画三个页面正常，动画结果页上出现了“关键帧候选与标注 →”按钮；
  - 没有 JS 错误，整个过程没有发出任何写请求。
  - 截图：`work/web-keyframes/prod/`。
- **线上没有做保存测试**，没有生成 `keyframes_artist.json`，留给美术来做第一次保存。保存流程和 409 冲突处理已在本地预览里用真实的 `keyframes_store.py` 验证过（见 `browser_check.cjs`）。

## 备份与回退

非公开的运维备份：`/data/video2rig/web-release-backups/ui-v1-37a15bf91586/`，内容如下：

- 部署前的 `server.py`、`project_store.py`、`task_monitor.py` 和 `index.html`；
- `record.json`：记录这三个模块部署前是否存在（`keyframes_store.py` 部署前不存在）；
- `previous-static/ui-v1-056895d632eb/`：上一个静态版本。

回退步骤（先确认没有任务在跑）：

1. 把 `previous-static/ui-v1-056895d632eb` 复制回 `code/web/static/releases/`；
2. 用备份里的 `server.py` 和 `index.html` 覆盖线上文件；
3. 重启一次 `video2rig-web`。

回退之后，`keyframes_store.py` 和新版本目录留着也不会被引用。`keyframes_auto.json` 和美术的标注文件是数据，回退时不要删除。

## 后续

- 其他结果目录还没有 `keyframes_auto.json`，页面会提示“尚未生成候选”，美术可以从零开始标。要生成候选，需要先把 `rig.json`、`motion*.npz`、`reading.npz` 拉到本地，用 `extract_keyframes.py` 和 `breakdown_ladder.py` 生成后再放回结果目录。
  - `breakdown_ladder.py` 依赖 Max 的 bind profile，只适用于 m11101 这副骨架。
- 本次改动还没有提交 git。
