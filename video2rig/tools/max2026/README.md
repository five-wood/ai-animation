# Max 2026 本地验证原型

本目录脚本针对本次已检查的 m11101 原始场景和本地拟合样例，不是任意骨架通用导入器。

## 入口

在 Max 2026 内选择 **Scripting → Run Script**，运行 [V2R_Max2026.ms](V2R_Max2026.ms)。可打开稀疏副本、合成 IK 支撑测试及报告目录；当前场景有未保存修改时不会自动替换。

工作输出目录：`D:\Project\ai-animation\work\max_editable_20261109`。不要将诊断副本另存覆盖原始角色场景。

## 处理链

1. [inspect_scene.py](inspect_scene.py)：只读场景结构、模式、单位检查。
2. [inspect_tracks.py](inspect_tracks.py)、[inspect_bind.py](inspect_bind.py)：只读真实子轨、Skin bind 和已有曲线检查。
3. [make_working_copy.py](make_working_copy.py)：仅在原场景干净、目标路径不存在时保存独立工作副本，并核对原文件 hash。
4. [prepare_motion.py](prepare_motion.py)：系统 Python + NumPy，源 FK、rest-delta 目标转换；不与 Max 交互。
5. [import_motion.py](import_motion.py)：Max 内执行，在工作副本回写 Biped / 普通 Bone，并实际回采。未通过的源保真状态仍为 diagnostic，不因成功保存而变合格。
6. [reduce_native.py](reduce_native.py)：在原生 Max 满帧参考上做保留 keys 的稀疏化，以60Hz与源24Hz采样检查减键新增误差。
7. [test_ik_support.py](test_ik_support.py)：**合成测试**——原角色双脚 IK 支撑、COM 下降1cm后恢复；不是源视频动作或自动接触修复。
8. [validate_protected.py](validate_protected.py)：原文件 hash、非Biped且非导入driver的已存在曲线逐键检查。

## 2026-11-11 spline 可编辑版（新链路）

输出目录：`D:\Project\ai-animation\work\max_editable_20261111_spline`，说明见该目录的 `实机验证报告.md` 与 `美术测试说明.md`。不再先做满帧重定向再删键，而是直接在 Biped 合法自由度内拟合、分部位排键：

1. [build_spline_edit.py](build_spline_edit.py)：系统 Python + NumPy。Biped 合法化（COM 吸收骨盆、锁骨挂 Spine1 且无扭转、肘单轴铰链、最小二乘手臂拟合）、双脚锚点与整体下沉、分部位姿势键 + 每段 1–3 个过渡键（整数帧），输出 `edit_packet.json` / `legal_dense.npz`。排键函数来自 [plan_spline_keys.py](plan_spline_keys.py)。
2. [import_spline_edit.py](import_spline_edit.py)：用 `3dsmaxbatch.exe import_spline_edit.py -sceneFile ...\base_scene.max` 运行，按部位写入局部键值，双脚 sliding→planted，另存为 `spline_edit.max` 并回采；不覆盖已有输出。
3. [evaluate_spline_edit.py](evaluate_spline_edit.py)：离线比较 Max 回采结果、合法化目标与原拟合。
4. [post_checks_spline.py](post_checks_spline.py)：在 batch 中打开 `spline_edit.max` 运行，检查受保护曲线，并做 COM 单键编辑测试（另存为 `com_edit_test.max`）。

`3dsmaxbatch.exe` 无界面执行，不占用美术正在使用的 Max 窗口，优先于 send_editor。

## 2026-11-11 晚 关键帧优先（候选 + 过渡键阶梯）

方案见 `video2rig/docs/关键帧优先方案_20261111.md` 第九节，数据格式见 `关键帧数据与网页接口约定_20261111.md`。

1. [extract_keyframes.py](extract_keyframes.py) 0.2：候选结构键，包括接触、停顿、极值、发力点、技术键。极值用多尺度检测，同时检测位置和旋转。默认会顺带计算过渡键阶梯，写入 `keyframes_auto.json` 的 `breakdown_ladder` 字段。
2. [breakdown_ladder.py](breakdown_ladder.py)：结构键固定后，过渡键逐个追加，形成嵌套排名（键数滑杆）。误差用 Biped 合法化后的动作加 TCB 0/0/0 近似计算，帧号为视频帧。可以用 `--keys keyframes_artist.json` 对美术改过的结构键重新排名，`--png` 输出图。依赖 `build_spline_edit.build()` 和 Max bind profile。
4. [build_keyframe_edit.py](build_keyframe_edit.py)：关键帧优先版的数据包（11-12）。
   - 只在美术审核过的 `keyframes_artist.json` 关键帧上打全身键，可选再加阶梯前 N 个过渡键（`--breakdowns N`）。
   - 站姿约束：着地腿膝盖保持在 20–35°，身体高度可上可下，并在插值帧上防止膝盖伸直；落地前、离地后各加一个悬停键。
   - 输出格式和 spline 版相同，后续的写入、评估、检查、对照视频沿用 `import_spline_edit.py` 等脚本，用 `V2R_OUT_NAME=keyframe_edit.max` 指定输出文件名。
   - 结果和验证见 `work/max_keyframe_20261112_ladingwu/`。
3. [run_keyframes.py](run_keyframes.py)：网页“生成候选关键帧”任务的入口，一次处理一个结果目录：`python run_keyframes.py <项目目录> <结果名>`。
   - 依次执行着地判断、候选抽取、过渡键阶梯；
   - 输入先复制到临时目录，结果原子写入；旧候选保存到 `keyframes_auto_history/`；
   - 不是 Biped 角色，或缺少 `reading.npz` 时，输出 `KEYFRAMES_FAILED:` 和原因，并以退出码 2 结束；
   - 骨架和 bind profile 对不上时，只生成候选，不算阶梯。
   - 服务器上的 bind profile 用 [profiles/m11101_bind_inspection.json](profiles/m11101_bind_inspection.json)，是 `work/max_probe/bind_inspection.json` 的副本；也可以通过 `--profile` 或环境变量 `V2R_BIND_PROFILE` 指定。
   - 已部署到服务器的 `/data/video2rig/code/tools/max2026/`。

[send_editor.py](send_editor.py) 是本机已验证的 Win32 定向发送辅助工具，仅用于现有 Max 的空白/工具自有 scratch editor，不能当成 Autodesk 官方远程 API。普通用户直接从 Max 运行脚本即可。

## 重要边界

- 本次源样例5秒/24fps，目标30fps，按时间映射至0–150帧；原场景附件/面部的既有 keys 不做全局清除。
- 原生 Biped 继承关系与源 Blender/FBX FK 不完全相同；COM 吸收骨盆旋转可改善 spine attachment，但肘部 hinge 无法表达源任意前臂旋转。**减键成功不代表源动作全部保真。**
- 普通 Bone 的 Matrix3 rotationpart 和 node.rotation 约定不同，原型已用隔离 pose 对照确认并处理；不能将此规则未经测试推广到别的控制器。
- 所有 unsupported controller、负尺度/镜像 Nub、未知接触都必须显式保留/报告，不能强制替换控制器。
- native key inventory 按可访问 controller handle 去重，但 Biped 共享肢体 keys 有特殊存储语义；报告里的 controller-key 总数不能等价为美术独立关键姿势数。
- 合成 IK 测试仅验证原角色支持该操作；不证明视频拟合动作的支撑区间、脚底朝向或跨layer编辑正确。
- 原型会在工作目录内部加载快照做实验。不要在它运行时同时编辑 Max 场景；文件有未保存修改时需要人工保存/确认后再运行。

## 无 Max 的数学回归

系统 Python 需 NumPy：

```powershell
python video2rig/tools/max2026/test_prepare_motion.py
```

测试包含时间/ticks映射、零motion回rest、旋转正交与行列约定、quat符号等价、源FK误差与写入骨骼范围。Max回采结果另见输出目录报告，不能用这些单元测试代替实机验收。
