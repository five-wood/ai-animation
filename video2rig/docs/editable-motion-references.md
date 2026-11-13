# 满帧动画 → 可编辑关键帧：商业案例与研究参考

调研日期：2026-11-07。目标：视频拟合满帧动画，输出适合美术二次创作的稀疏控制器关键帧；工作环境为 3ds Max，原生 Biped + 普通 Bone，脚部 IK 定帧。

本文是文献与产品机制调研，不是本项目或这些工具的实测报告。不将厂商减键比例、论文平均误差或 FBX 动画回放成功当作 Max 原生编辑保真保证。

对应实施设计：[3ds Max 可编辑动画交付方案](3ds-max-editable-motion-design.md)。

## 一、结论与优先级

最值得组合借鉴的是：

1. **3ds Max Biped 原生动捕过滤**：作为最低成本、最贴近最终编辑器的基线，不应一开始完全重写其已有能力。
2. **Cascadeur Animation Unbaking**：产品交互最贴近需求——先依照支撑点变化放置关键帧，再处理插值、运动学类型与部位差异。
3. **Maya HumanIK + Smart Bake**：借鉴先回到编辑 control rig，再按容差维护重建质量；不能误认为 Smart Bake 必然把满帧源变成少量姿势键。
4. **Roberts 2018/2019 + Cheynel 等 2024**：分别研究有编辑意义的姿势选择，以及控制器空间的关键帧/切线拟合。
5. **Footskate Cleanup 2002**：借鉴接触锚点、连续 IK 与时序过渡，不直接照搬其可变骨长机制到 Biped。
6. **UnderPressure 2022**：可借鉴接触识别思路；人体域和受限许可证使其不适合作为当前怪物项目的直接商用组件。

应区分三种任务：

- 曲线压缩：尽量少 keys 还原原曲线。
- 可编辑重建：keys 落在美术会操作的控制器和有意义的时间上。
- 接触修复：可以主动改变源动作来消除滑脚。

它们不是同一目标，应分别报告误差和编辑成本。

## 二、商业产品与原生工具

### 1. 3ds Max Biped Motion Capture：优先做基线

**已核实能力**

Autodesk 2024 官方文档明确：动捕和 marker 数据通常逐帧有 key；可通过过滤减少 keys。支持：

- Use Key Reduction：按部位设置位置/角度容差与 Minimum Key Spacing。
- 原始 Motion Capture Buffer：保留未过滤数据，Show Buffer 做参考，Convert From Buffer 反复尝试参数。
- Freeform / Footstep Extraction / Fit to Existing 三种路径。
- Limb Orientation：angle / point / auto；auto 使用手脚位置并重新安排膝/肘姿态。
- 普通 BIP 也可通过该过滤流程处理。

**对本项目的直接价值**

先把动作正确映射到测试 Biped，在不提取 footsteps 的 freeform 条件下尝试原生减键，并保留原始参考。比较它与自定义算法的实际 keys、足底漂移、膝盖误差和编辑体验，避免重造已有工具。

**限制**

- Footsteps 自动生成/动力学与美术的 freeform 脚 IK 定帧不是同一个流程，不应默认启用。
- 官方文档的 handspring 示例明确展示：footstep dynamics 可令身体高于原始动捕，需要 freeform 区段恢复匹配，证明自动支撑机制确实可能改变动作。
- auto limb orientation 不保证保留源膝盖方向。
- Minimum Key Spacing 会在容差步骤之后继续减键；最终须实测，不能只看输入容差。
- 文档中“滤除超过 80% keys”的描述不是我们模型的质量/减键保证。
- Biped 机制不自动覆盖普通 Bone、Twist 和自定义控制器。
- MAXScript 的 `mocap.*` 设置是全局参数；批处理工具应保存/恢复，避免影响用户其他角色。

来源：

- [Motion Capture Conversion Parameters Dialog，2024](https://help.autodesk.com/cloudhelp/2024/ENU/3DSMax-Character-Animation/files/GUID-3559D99C-B946-4C37-B845-D90087B963D3.htm)
- [Biped Motion Capture MAXScript，2024](https://help.autodesk.com/cloudhelp/2024/ENU/MAXScript-Help/files/3ds-Max-Objects-and-Interfaces/MAXScript-Extensions/MAXScript-Extensions-in/Biped-MAXScript-Extensions/GUID-389AABA5-35CC-46FD-9E4C-DD4A653F8A62.html)

### 2. Cascadeur Animation Unbaking：产品形态最接近

**已核实能力**

官方 Animation Unbaking 面向 fully keyframed 动画，将其转换为更少关键帧和多种插值：

1. Auto Interpolation Keys：支撑点（fulcrums）变化时放 key；跳跃轨迹顶点额外放 key。
2. Adjust Keys and Interpolations：补充 keys、插值区间及 kinematic types。
3. Auto Unlock：在 AutoPosing 条件下减少锁定控制器。
4. 自 2024.2 起支持单独 Animation Tracks，用于不同部位不同程度的 unbaking。
5. 手动绑定角色需为接地刚体设置 Fulcrum Group，官方甚至举马的四个蹄子为例。
6. 设置提供重建接近程度和姿态差异控制；不应擅自把其参数数值解释为毫米误差。

**借鉴建议**

支撑事件优先 → 动作阶段/顶点 → 部位过渡键 → 插值修整，而非每根骨骼每条轴独立减键。接触位置和部位分组需要角色准备数据，不能假设任意骨架自动知道脚底在哪。

**限制**

Cascadeur 的 control rig、AutoPosing 和插值语义不等于 Max Biped。外部工具中好编辑，不证明导回 Max 后仍是同样的可编辑结构。适合作为产品与质量对照，不默认作为必须购买/依赖的软件。

来源：[官方 Animation Unbaking](https://cascadeur.com/help/category/221)。

### 3. Maya HumanIK + Smart Bake：先回控制器，再保证重建

**已核实能力**

- HumanIK 支持 Bake To Control Rig；有 Custom rig mapping 时可 Bake To Custom Rig；Body Part 模式可仅处理选择的部位。
- Smart Bake 只在源烘焙曲线已有 key 的时刻放 key。
- Increase Fidelity 根据 Fidelity Keys Tolerance，在重建曲线偏差超出预算时增加 keys。
- Sparse Curve Bake 对直接连接的动画曲线保留足够描述曲线形状的 keys。

**借鉴建议**

把“骨骼动画转为编辑控制器动画”与“稀疏化”分开；每种实际控制器有对应适配器；超差按部位补 key。

**限制**

- 满帧源本来每帧有 key，Smart Bake 的时刻继承本身不保证稀疏。
- Fidelity Keys Tolerance 是曲线值的百分比，不是脚底世界空间误差或接触保证。
- HIK 与 Max Biped 不是同一套编辑控制器，不能直接等价互换。

来源：

- [HumanIK Bake options，2024](https://help.autodesk.com/cloudhelp/2024/ENU/Maya-CharacterAnimation/files/GUID-1704AD41-8435-4AD4-AF29-43EF13803EAC.htm)
- [Bake Simulation / Smart Bake，2026](https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Animation/files/GUID-A11424B4-8384-4832-B18D-01264E1A19D1.htm)

### 补充：iClone Motion Correction 的接触交互

官方 Motion Correction 会计算接触时段、建立 hand/foot prints，并生成 Reach / Release keys。可调检测阈值、过渡长度、接触起止偏移，以及是否同时约束位置与方向。官方案例是在路径行走中修正脚滑，并明确建议反复调参、先固定一只脚再处理另一只。

值得借鉴的是“接触区间 + 空间目标 + 进入/退出过渡”的可视化编辑，而不是只有一个锁脚按钮。它是接触修正，不是稀疏主要姿势生成器；其印记、Reach 与层也不能假定通过 FBX 成为 Max 原生控制器。

来源：[iClone 8 Motion Correction 官方案例](https://manual.reallusion.com/iclone-8/Content/ENU/8.0/51-Animation-Timeline-Editing/Motion-Correction-for-Sliding-Issues.htm)。

### 4. MotionBuilder 的重要反例：传动画不等于传编辑语义

Autodesk Dynamic baking 文档明确说明：发送到 3ds Max 时所有 MotionBuilder constraints 都会被烘焙，即使其 source object 也被发送。

因此，不能把“MotionBuilder 中搭好脚锁 → 发送到 Max”当成保留 Max 原生脚 IK 编辑能力的解决方案。该证据特指官方 MotionBuilder→Max 发送行为；不是声称任何软件的任何 FBX 都绝不能表示任何约束，但足以说明不能依赖约束自动互通。

来源：[Dynamic baking，2024](https://help.autodesk.com/cloudhelp/2024/ENU/MotionBuilder/files/GUID-A9AEC751-817D-42EA-B0EE-61CC9FA802C2.htm)。

## 三、直接相关研究

### 5. Optimal and interactive keyframe selection for motion capture

Richard Roberts、J. P. Lewis、Ken Anjyo、Jaewoo Seo、Yeongho Seol。先有 SIGGRAPH Asia 2018 Technical Briefs，2019 年扩展期刊版发表于 Computational Visual Media 5(2):171–191。

**核心**

将有编辑意义的关键姿势选择建模为最短路径，用动态规划找关键帧集，而不是只保留极值或均匀抽帧。再通过选出的 keys 重建可编辑动画。

**证据与案例**

论文展示修改跳跃高度/蹲伏深度、手臂位置与身体重心等二次编辑。摘要报告测试中约保留原帧数的 10%，并保留大部分动作细节。

**借鉴**

输出一组便于改节奏和动作幅度的主要姿势；比较不同 key budget 下的误差；用优化选择而不是机械抽帧。

**限制**

“optimal”针对论文所定义的近似目标，不是所有插值/所有 rig 的全局最优。没有自动给出 Biped 原生控制器、脚 IK 接触语义，也不保证 10% keys 可在本项目达到相同质量。

来源：[2019 扩展版原文与附带视频](https://www.sciopen.com/article/10.1007/s41095-019-0138-z)，DOI 10.1007/s41095-019-0138-z；[2018 DOI](https://doi.org/10.1145/3283254.3283256)。

### 6. Rethinking motion keyframe extraction: a greedy procedural approach using a neural control rig

Cheynel 等，SIGGRAPH Asia 2024 Posters；**两页 poster 研究，不当作已成熟生产组件**。DOI 10.1145/3681756.3697960。

**核心**

直接指出现有减键方法常忽略骨架层级、控制 rig 与美术编辑习惯。学习可微的 neural control rig 代理，在控制器空间拟合关键帧值及导数，使用 Hermite 插值，并根据重建收益逐步增加 key。

**最值得借鉴**

- 在可编辑控制器上拟合，不是在全部蒙皮骨骼上压缩。
- 评估世界关节位置/旋转等运动误差，不只看单条曲线。
- 不必只从源帧中照抄 pose，可以调整 key 值和切线以更好重建中间动作。

**限制与量化边界**

采用 SMPL、BEDLAM 和学习到的近似 rig，不是实际 Max Biped。论文表格在 10% keys 条件下报告 MPJPE 0.0527 m（约 5.27 cm）；这个量级不能用来保证支撑脚毫米级稳定。接触需单独约束，实际 Max 插值需回采评估。本次未核实到可直接复用的 Max 插件/完整生产代码。

建议先借鉴问题定义和控制器空间优化，使用 Max 实际评估闭环，不急于训练 neural proxy。

来源：[ACM 完整 HTML 原文](https://dl.acm.org/doi/fullHtml/10.1145/3681756.3697960)。

### 7. Footskate Cleanup for Motion Capture Editing

Lucas Kovar、John Schreiner、Michael Gleicher，SCA 2002。

**核心**

依据明确的 footplant / handplant 约束修复动作。官方代码说明强调：不仅逐帧 IK，还需要连续输出与接触进入/退出时的 blending，避免新产生不连续。提供 C++ 库，使用 BSD 风格许可证。

**借鉴**

显式接触段与锚点、连续腿型、接触切换过渡，以及在原动作附近做局部修正。直接对应“用了 IK 会不会偏离原拟合动画”的问题。

**限制**

它是接触修复器，不是少量关键帧生成器。原方法包含对腿长的小幅调整；固定骨长的 Biped 不能照搬此自由度来声称满足所有目标。需重新处理 root 调整、可达性和无解报告。2004 年公开的代码接口是逐帧 pose/constraint，并不是 Max 原生可编辑 IK 交付物。使用时需保留许可证与署名要求，并复核目标约束。

来源：[Wisconsin 官方算法与代码页](https://research.cs.wisc.edu/graphics/Gallery/FootskateSolver/)、[官方演示：接触切换、膝跳和腿长变化](https://research.cs.wisc.edu/graphics/Gallery/kovar.vol/Cleanup/)。

### 8. UnderPressure: Deep Learning for Foot Contact Detection, Ground Reaction Force Estimation and Footskate Cleanup

Lucas Mourot、Ludovic Hoyet、François Le Clerc、Pierre Hellier，2022，Computer Graphics Forum 41(8):195–206；DOI 10.1111/cgf.14635。

**核心**

用动捕 + 压力鞋垫数据训练模型，从运动估计脚的地面反作用力，推导接触标签，再用优化 IK 修复滑脚。官方仓库提供数据入口、预训练模型和实现。

**借鉴**

接触检测是独立任务；不能只凭脚速度低/高度低判断落地。自动接触候选与约束修复可分开进行，并与启发式或人工标注比较。

**限制**

- 人体数据和骨架域，不代表适用于怪物/四足/夸张比例；估计 GRF 不是真实测力结果。
- 不生成少量美术关键帧，也没有 Max 原生控制器适配。
- **仓库公开不等于可商用开源**。已核对的 LICENCE.txt 为 Limited Software Evaluation License，授权限 fundamental research，明确排除 commercial use，且限制修改等操作。不得未经授权将代码/模型作为我们的商业生产组件；商业采用需独立授权和法务复核。

来源：[官方仓库与论文摘要](https://github.com/InterDigitalInc/UnderPressure)、[实际许可证](https://github.com/InterDigitalInc/UnderPressure/blob/master/LICENCE.txt)。

## 四、对本项目路线的具体调整

### 先做原生基线，再判断自研的收益

Max 就绪后选三个代表片段：双脚下蹲、单脚移重/迈步、跳跃落地。使用同一源动画、同一角色比例、同一源/目标时间映射比较：

A. 不减键的目标 Biped 参考转换。
B. Max 原生 freeform key reduction（footstep extraction 关闭）。
C. 事件优先 + 显式接触 + 分部位控制器减键的自研方案。
D. 可选 Cascadeur unbaking 对照；仅把导入 Max 后实测结果纳入最终结论，不假设 rig 随 FBX 保留。

分别记录：

- 相对源动画的偏差（包括接触修复本身带来的主动变化）。
- 相对确认后的接触目标的偏差（稀疏化/转换误差）。
- 足底滑动/穿透、膝盖位置/方向、骨盆路径、关键事件时序、子帧过冲。
- major poses、实际各轨道 keys、总 keys、人工修复数量。
- 美术完成一次指定编辑的耗时，例如改下蹲深度、延长支撑或改变落脚点。

如 B 已满足要求，自研应集中补接触标注、普通 Bone 适配和验收，而不是完整重造减键器。如 B keys 少但编辑不便，再引入 Roberts 的 pose-level 选择及控制器级拟合。暂不把学习式 neural rig 或接触模型作为首版依赖。

### 最终推荐组合

```text
Cascadeur 式：支撑事件优先的动作结构
    +
Roberts 式：有编辑意义的关键姿势选择
    +
控制器空间拟合：Biped / Bone 分适配器
    +
Footskate 式：连续接触约束与有限范围修正
    +
Max 原生解算回采：误差驱动地补必要 keys
```

保留脚部 IK 定帧和原生 rig，不在最后重新全身满帧烘焙。学习产品/论文的机制，但所有源数据可信度、控制器行为和许可边界独立验收。
