# xiyi2_temporal_web 左后腿独立只读审计

## 边界、可复现性与结论级别

- 远程只读 `/data/video2rig/projects/lizard/out/{xiyi2,xiyi2_temporal_web}` 与项目 rig/studio；新增脚本和输出仅 `/tmp/left_hind_*`，本地新增仅本目录。未改核心、未求解重跑、未调用GPU或触碰现存任务。
- 远程计算：`CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 nice -n 15 /data/video2rig/.venv/bin/python /tmp/left_hind_audit.py`；Torch强制单线程CPU。只做投影、FK、损失/梯度评估。
- 121帧，24fps；帧号从0开始，后段 t>=3s 是72..120，共49帧。
- 实际服务器与本地五份实现文件SHA256完全一致，见[工件清单](<artifact_inventory.txt#L129-L133>)；工件SHA256亦保存在该清单。远程原始文件未写回。
- 本文的 reproj / ray / anchor 距离都是**内部一致性量**，不是GT误差。没有人工标注/独立GT，不能判定哪视图、哪个2D位置或哪条3D轨迹是真值。下文“偏差”仅表示观测与拟合/跨视图不一致。

## 1. 索引先由rig确定

| 名称 | keypoint index | bone index | eul active index | 说明 |
|---|---:|---:|---:|---|
| hip.L | 20 | 16 | 13 | parent=hips |
| knee.L | 21 | 17 | 14 | parent=hip.L |
| ankle.L | 22 | 18 | 无 | ankle骨 inactive，局部旋转固定 |
| ankle.L.tip | 23 | 18 tail | 无 | 脚尖与踝刚性关联，可向髋膝传递数据项梯度 |

来源：[工件清单](<artifact_inventory.txt#L21-L28>)、[骨索引及限制](<artifact_inventory.txt#L45-L50>)、[FK实现](<../../../video2rig/v2r/geom.py#L205-L234>)。髋膝限值均XYZ ±75/±30/±50°。

## 2. 原2D观测与处理链

baseline与temporal的 `uv_reader / uv_raw / uv / conf / depth / X / res` **逐元素完全相同**；变化只在拟合权重、X_fit与最终运动，不是读点器结果发生了变化。[量化JSON](<left_hind_audit_summary.json#L28-L35>)。

- `uv_reader` 是原读点；`uv_raw` 是首帧相似校准后、swap前；`uv` 是求解观测。不能把 `uv_raw` 误叫原reader输出。
- 左后腿四点所有帧/双视图 `uv == uv_raw`，没有label swap；front有全局首帧校准，side未变。全片reader→raw位移中位：hip 1.673px、knee 2.534px、ankle 2.846px、tip 3.196px；不可能单独解释后段27–29px残差。见[映射核对](<mapping_check.json>)。
- 示例 frame96 / 4.0s ankle.L：front reader=(373.013,418.970)，校准后uv=(374.910,420.774)；side reader=uv=(328.483,438.326)。temporal投影 front=(366.489,446.774)、side=(330.227,435.964)。这只是不同估计间的差异，不是GT偏移。
- front ankle在frame92→93（3.833→3.875s）由v=436.624跳至419.242，约17.44px；frame104–105短暂回到436.59，随后再次419.29。因此“整个后段观测平滑”也不准确：**存在跳变，且跳到另一平台后时间因子很快恢复1**。

原始逐帧/逐点/逐视图坐标、置信、所有因子、投影与残差见[观测CSV](<left_hind_audit_observations.csv>)；脚本见[只读审计脚本](<audit_left_hind.py>)。

## 3. 后段权重与reprojection（49帧中位）

| 点/视图 | 原peak | final weight | baseline weight | temporal reproj px | baseline reproj px | triang ray residual px |
|---|---:|---:|---:|---:|---:|---:|
| hip.L front | .676 | .643 | .676 | 2.75 | 3.15 | 1.74 |
| hip.L side | .820 | .439 | .820 | .75 | .86 | 1.11 |
| knee.L front | .679 | .480 | .679 | 1.68 | 2.38 | 2.03 |
| knee.L side | .755 | .375 | .755 | 3.80 | 3.37 | 1.51 |
| ankle.L front | .554 | .245 | .111 | 27.29 | 28.25 | 20.11 |
| ankle.L side | .822 | .294 | .822 | 2.99 | 2.47 | 8.18 |
| ankle.L.tip front | .554 | .345 | .554 | 20.81 | 21.98 | 15.78 |
| ankle.L.tip side | .761 | .262 | .761 | 3.23 | 2.30 | 7.69 |

注意baseline不是GT。temporal将权重从side移开，改善部分front重投影、恶化部分side重投影，不能据此断言质量改善或恶化。

### 权重各因子中位数

顺序为 bounds / peak / temporal / bone / ray / ambiguity / spread；因子相乘后clip，不是校准概率。

| 点/视图 | bounds | peak | temporal | bone | ray | ambiguity | spread |
|---|---:|---:|---:|---:|---:|---:|---:|
| hip front | 1 | .692 | 1 | 1 | .994 | .953 | .981 |
| hip side | 1 | .829 | 1 | 1 | .994 | 1 | .533 |
| knee front | 1 | .695 | 1 | 1 | .992 | .715 | .982 |
| knee side | 1 | .767 | 1 | 1 | .992 | 1 | .493 |
| ankle front | 1 | .577 | 1 | 1 | .837 | .515 | .980 |
| ankle side | 1 | .831 | 1 | 1 | .837 | .851 | .497 |

- hip/knee双视图、ankle side的 temporal_factor 在49帧中**恒为1**；ankle front最小.286，主要惩罚跳变，不惩罚稳定平台。ankle front时间distance中位.847px，side .034px。
- bone_factor中位全1，但并非所有帧恒1：knee front最小.9916，ankle front最小.9638；抑制极弱。该项是每视图全片边长相对中位/MAD的宽容异常检测，不是3D骨长正确性验证。
- side腿部spread约16px，而front约5px，导致side spread_factor约.49–.53、front约.98；这是显著且系统性的视图权重差异。无法仅凭无GT工件认定宽峰来自遮挡或认定side正确。
- ray_factor下界设计为.8、双视图共享，跨视图明显不一致最多只能削弱约20%，不会选出“错视图”。

### “平滑但偏移获得高权重”是否被证实？

**不支持笼统强结论；支持有限失效模式。**

- ankle front在49帧中26帧reproj>20px；这26帧weight=.2051–.2553，中位.2388，**没有一个>=.5**。
- ankle front的14帧weight>=.5，reproj中位1.05px、最大2.14px。高权重与大残差不同时出现；高权重且低残差可能是正确观测，也可能是错误观测被紧贴，工件本身无法区别。
- ankle tip front 31帧reproj>20px，weight=.3286–.3631，中位.3445；仍是中低而非高权重。
- 可证的是：稳定不一致仍有非零中低权重，temporal与bone因子几乎不处理持续平台偏差；不是“已证明平滑错点获得高权重”。

计数及因子最小值：[补充统计](<extra_metrics.json>)。

## 4. 髋膝旋转及3D链

使用Blender局部XYZ与SO(3)相邻帧测地转角；Euler X不是解剖学膝屈伸角。几何膝内角另由hip-knee-ankle三点计算。

| 量（t>=3s） | baseline | temporal |
|---|---:|---:|
| hip相邻帧转角中位 / 最大 | .590° / 2.765° | .256° / 1.782° |
| knee相邻帧转角中位 / 最大 | .943° / 6.598° | .411° / 3.312° |
| 几何膝内角范围 / 中位 | 86.54–114.19° / 112.47° | 77.64–113.99° / 112.72° |

- temporal与baseline髋局部旋转差SO(3)中位.966°、最大3.751°；膝中位2.676°、最大12.501°。
- frame72 / 3.0s差异突出：temporal knee Euler=(24.38,4.88,8.61)°，baseline=(12.51,8.97,8.04)°；几何膝内角77.64° vs88.58°。frame96 /4s则113.34° vs112.38°，并非整个尾段膝角剧烈偏离baseline。
- temporal hip XYZ范围[-1.90,6.51]/[-2.02,2.05]/[-12.59,5.87]°；knee范围[-13.12,24.38]/[-1.62,9.86]/[-3.44,8.61]°，远离限值，**没有限位饱和证据**。
- 由FK保证上/下腿长度约.2490/.2561 world unit。X_fit锚点上腿中位.2657（比rig约+6.7%），下腿.2480，锚点几何膝内角中位100.25°，与temporal112.72°不同。此差异表明锚点不是刚性骨架一致真值。
- ankle没有主动局部自由度；踝位置由root+髋膝和rig偏移决定，tip与ankle刚性耦合。不要以“调ankle eul”作为该rig的修复路径；是否需要自由踝自由度需泛化模型审计，非本报告可证明。

逐帧旋转与膝角：[姿态CSV](<left_hind_audit_poses.csv>)。

## 5. 3D triang anchor与2D重复拉偏？

### 已证明：同源，不是独立证据

[管线](<../../../video2rig/v2r/pipeline.py#L147-L153>)用同一uv和final weights重新triangulate为X_fit；[求解器](<../../../video2rig/v2r/solve_temporal.py#L69-L94>)同时优化2D鲁棒image项和3D项，其中w3=min(view weights)。所以X_fit约束与2D观测统计相关，不能将“二者一致”当两个独立证据。

### 未证明：主导或始终同向拉偏

精确复算保存最终姿态的目标与梯度（没有优化/消融）：

- image=.0837311819，tri3d=.00002480812，复现保存solver.json；3D/2D总标量项比约.0296%。不能只用标量项判定作用大小，因此额外评估梯度。
- 后段hip局部eul的 ||∇tri3d||/||∇image|| 中位1.60%、p90 8.42%、最大19.25%；cos中位.454，32/49帧同向。
- 后段knee局部eul梯度比中位3.54%、p90 24.56%、最大35.59%；cos中位.207，30/49帧同向。image各观测梯度经骨架抵消后很小，比例会上升，不能据此说3D成为独立主驱动力。
- 对ankle**世界点坐标**，梯度比中位.156%、最大.533%；cos中位-.324，只有19/49帧同向。tip中位比.306%、cos中位-.912。也就是锚点与2D总梯度常常反向，不是统一重复拉向某一位置。
- frame96，ankle front X_fit投影=(372.075,437.966)，观测=(374.910,420.774)，最终=(366.489,446.774)；side X_fit投影=(325.822,427.295)，观测=(328.483,438.326)，最终=(330.227,435.964)。锚点是冲突视图的折中，不能把它解释为GT。
- 最终姿态处梯度仅是局部敏感性，不足以测量优化路径/盆地或去掉3D项后的因果变化。**本报告未做3D-off消融，所以不声称移除3D可修好。**

实现细节：鲁棒2D半径=.03*337.474≈10.12px；大残差image已饱和减梯度。3D项有最大.04夹紧，其梯度行为不同；未来对极大离群点仍值得检查同源3D是否绕过2D鲁棒性，但本片ankle局部梯度并未支持其主导。

## 6. 通用处理优先项（不接触地面、不硬编码骨名/时间段）

1. **优先补“持续跨视图冲突/歧义”诊断与可见证据，而非加大时间平滑。** 用held-out-view/leave-one-observation-out重投影、heatmap多峰候选与跨视图几何联合检查；保留“不确定哪视图错”的状态，避免用当前fit残差自证真值。先获取少量独立2D人工审阅点/标注作为验证集，尤其跳变前后及稳定平台，才能评价是否偏移。
2. **对系统性视图权重校准做泛化验证。** 当前side宽峰spread项系统乘约.5，front窄峰近1；评估图像/heatmap分辨率与峰尺度归一、每view验证集可靠性曲线。不能直接“提升side”或把视图权重归一到相同强度，也不要将高peak当可见概率。
3. **将同源tri3d定位为初始化/辅助，不当独立观测。** 后续在独立输出做3D-off/2D-only以及统一鲁棒门控消融；或只在深度弱可辨识方向用条件数/协方差加权anchor，避免相关证据重复。收益必须用标注、holdout与稳定性共同检验。本例anchor弱且常反向，优先级低于观测冲突/权重问题。
4. **数据冲突无法辨别时输出低置信/多假设，不用更强平滑掩盖。** 本例髋膝已更平滑但front ankle大残差平台仍在；更强平滑不能修复恒定偏差。时间尺度应按秒，幅度用图像/rig尺度，泛化至所有关节。
5. **通用自由度可辨识性检查。** 标识末端inactive+tip刚性耦合会怎样将误差分摊到上游；只有独立标注证据支持时才改rig自由度/先验。不要按ankle.L写特殊逻辑，不加contact、不按3–5s硬编码。

![CPU审计证据图](<left_hind_evidence.png>)

## 本地复核入口

- [审计脚本](<audit_left_hind.py>)：远程CPU只读数组/FK/梯度检查。
- [完整JSON](<left_hind_audit_summary.json>)：所有分位数、selected frames、数据项梯度。
- [观测CSV](<left_hind_audit_observations.csv>) / [姿态CSV](<left_hind_audit_poses.csv>)：逐帧复核。
- [工件与源哈希清单](<artifact_inventory.txt>)、[映射核对](<mapping_check.json>)、[补充计数](<extra_metrics.json>)。
