# 独立只读 depth-head 审计

## 范围和方法

- 服务器 `ssh -n animsrv`；仅执行 `/data/video2rig/.venv/bin/python` + NumPy CPU 数组运算。未加载模型、未训练、未运行GPU、未改核心或部署；远端新增文件仅 `/tmp/v2r_depth_*`。
- 统计两个项目所有19份 reading 快照；同源输出重复不是独立测试集，exam/exam_temporal也不是两个独立考试。
- GT严格取各项目 `exam/frames/motion_gt.json` 的各视图 uvz[:,2]，每帧每视图全28点均值归零后除 rig.size=2.6133360266685486。不拟合尺度、不翻转深度符号。正z是更远。
- 镜像字段互反且有效，8对。ankle.L/R索引22/26；不是骨骼索引18/21。
- 全阈值曲线预先固定为 0/.01/.02/.04/.08 normalized size，保留条件严格 |pred(L)-pred(R)|>阈值。coverage分母是该子集全部样本；error为保留样本误序率。相邻帧相关，不把样本数当独立证据。
- 真实片无GT；只报告深度时间变化、相邻排序翻转和同双视角 triang 排序冲突。triang由同一UV及相机推得，是相关一致性参照，绝不是GT。

## 保存对齐检查：temporal已正确重排，legacy未修

- 从 saved uv_raw 到 uv 的逐槽匹配恢复交换映射，所有快照未知映射数=0；不凭文件名推定对齐。
- lizard exam：frame30（0-based），side，后腿整链8槽/4mirror对交换。exam_temporal.depth严格等于permutation(exam.depth)，最大差0。xiyi_temporal相对xiyi也严格符合24槽交换（frame78/79 side，89 front）。
- xiyi2、xiyi3及clean项目所有目标无swap，legacy/temporal原始depth逐元素相同；这是排除重排混杂的控制。
- 当前源pipeline temporal分支115–118重排dep和quality；legacy120只交换uv/conf。legacy继续存在潜在错配，不应拿其交换样本直接比较。

### frame30 side：UV交换与深度身份并不总是同一问题

- 已对齐temporal在这8槽的GT深度MAE=.174650，legacy raw depth=.013836；4个深度pair排序由对变错。不能为了GT分数撤回正确的slot重排。
- 详细UV核对显示：hip.L/R误差由1.27/3.54px增至8.58/10.05px；knee.L/R 7.64/1.65→10.29/7.45px；ankle.L 3.49→2.76px，ankle.R 36.98→37.01px；tip则40.60/36.94→1.62/2.41px。整链交换改善了tips，却损伤proximal身份；深度head仍保留原本正确的左右深度次序。更准确结论是“整链UV identity决策与depth head身份耦合不一致”，不是所有UV交换都错。
- temporal该ankle两端conf约.840/.813，但conf_fit降至.237/.277：原始conf不能保证深度排序；融合可靠性可降低影响。

## GT点深度MAE

|run|all n=8400|可见 n=7399|单视图遮挡 n=1001|该点双视图vis=0 n=210视图槽|
|---|---:|---:|---:|---:|
|lizard/exam|0.024246|0.023079|0.032869|0.039190|
|lizard/exam_temporal|0.024399|0.023253|0.032869|0.039190|
|lizard_clean/exam|0.023153|0.021904|0.032392|0.042550|

双视图不可见点共有105个frame-keypoint（两视图槽计210）。不同模型对不可见点的MAE没有一致改善。

## GT镜像排序：accuracy / coverage / wrong-kept

### lizard/exam_temporal：全部8对，2400 pair-view-frame

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|91.92%|100.00%|194/2400|0.04000|
|0.01|93.38%|96.25%|153/2310|0.04000|
|0.02|94.54%|91.62%|120/2199|0.04006|
|0.04|95.74%|83.21%|85/1997|0.04043|
|0.08|97.39%|65.46%|41/1571|0.04142|

### lizard/exam_temporal：ankle.L/R，300 pair-view-frame

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|87.33%|100.00%|38/300|0.05374|
|0.01|88.62%|96.67%|33/290|0.05404|
|0.02|90.48%|91.00%|26/273|0.05382|
|0.04|91.53%|82.67%|21/248|0.05446|
|0.08|94.47%|66.33%|11/199|0.05634|

### lizard/exam_temporal：同一视图mirror两端都vis=0，39样本

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|69.23%|100.00%|12/39|0.04897|
|0.01|70.27%|94.87%|11/37|0.04839|
|0.02|78.57%|71.79%|6/28|0.05378|
|0.04|82.61%|58.97%|4/23|0.05380|
|0.08|100.00%|35.90%|0/14|0.03151|

### lizard/exam_temporal：上述真实双遮挡ankle，9样本

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|66.67%|100.00%|3/9|0.09246|
|0.01|62.50%|88.89%|3/8|0.09066|
|0.02|66.67%|66.67%|2/6|0.09875|
|0.04|50.00%|44.44%|2/4|0.10919|
|0.08|100.00%|11.11%|0/1|0.01164|

### lizard/exam_temporal：pair至少一端在两个视图都vis=0

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|90.20%|100.00%|20/204|0.04335|
|0.01|90.05%|98.53%|20/201|0.04256|
|0.02|91.05%|93.14%|17/190|0.04267|
|0.04|91.80%|89.71%|15/183|0.04222|
|0.08|95.95%|72.55%|6/148|0.03829|

### lizard/exam_temporal：ankle至少一端在两个视图都vis=0

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|73.08%|100.00%|7/26|0.06810|
|0.01|72.00%|96.15%|7/25|0.06655|
|0.02|72.73%|84.62%|6/22|0.06934|
|0.04|70.00%|76.92%|6/20|0.06849|
|0.08|80.00%|57.69%|3/15|0.05466|

### lizard_clean/exam：全部8对，2400 pair-view-frame

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|91.75%|100.00%|198/2400|0.03909|
|0.01|93.08%|95.71%|159/2297|0.03904|
|0.02|94.55%|91.67%|120/2200|0.03909|
|0.04|96.58%|82.88%|68/1989|0.03853|
|0.08|97.81%|66.67%|35/1600|0.03958|

### lizard_clean/exam：ankle.L/R，300 pair-view-frame

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|86.67%|100.00%|40/300|0.05277|
|0.01|88.77%|95.00%|32/285|0.05199|
|0.02|91.58%|91.00%|23/273|0.05124|
|0.04|95.45%|80.67%|11/242|0.04949|
|0.08|97.37%|63.33%|5/190|0.05175|

### lizard_clean/exam：同一视图mirror两端都vis=0，39样本

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|87.18%|100.00%|5/39|0.05481|
|0.01|85.29%|87.18%|5/34|0.05788|
|0.02|89.66%|74.36%|3/29|0.05728|
|0.04|100.00%|51.28%|0/20|0.04791|
|0.08|100.00%|30.77%|0/12|0.05923|

### lizard_clean/exam：上述真实双遮挡ankle，9样本

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|88.89%|100.00%|1/9|0.06762|
|0.01|87.50%|88.89%|1/8|0.06166|
|0.02|87.50%|88.89%|1/8|0.06166|
|0.04|100.00%|55.56%|0/5|0.03283|
|0.08|100.00%|22.22%|0/2|0.02217|

### lizard_clean/exam：pair至少一端在两个视图都vis=0

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|92.65%|100.00%|15/204|0.05309|
|0.01|93.00%|98.04%|14/200|0.05189|
|0.02|93.40%|96.57%|13/197|0.05109|
|0.04|95.11%|90.20%|9/184|0.04837|
|0.08|95.00%|78.43%|8/160|0.05050|

### lizard_clean/exam：ankle至少一端在两个视图都vis=0

|阈值|accuracy|coverage|wrong/kept|pair差MAE|
|---:|---:|---:|---:|---:|
|0.00|80.77%|100.00%|5/26|0.06788|
|0.01|83.33%|92.31%|4/24|0.06306|
|0.02|83.33%|92.31%|4/24|0.06306|
|0.04|85.00%|76.92%|3/20|0.05884|
|0.08|82.35%|65.38%|3/17|0.06217|

“两mirror端同一视图都不可见”和“一个keypoint两个视图都不可见”是不同子集，本报告两者均列。四个vis全0子集只有1个frame-pair/2个view样本，也保存在JSON，不用conf低替代实际遮挡。双遮挡n很小，1/1、2/2甚至14/14均不能证明安全。

### confidence并非depth排序置信度

- lizard exam_temporal原始pair min-conf>=.5保留2290/2400，th0 accuracy92.49%；conf_fit>=.5保留852/2400，th0 accuracy91.08%。两种confidence都不是已校准的排序概率。
- conf_fit>=.5且margin>.04为22/651错，coverage相对全体仅27.13%；>.08为6/442错，coverage18.42%。这些是描述性曲线，不以测试后效果选择预设。
- 不同solver的conf_fit尺度不同：clean exam是legacy，不能用同一数值门槛当跨solver校准。

## 各视图ankle：不应被合并均值掩盖

|run/view|th0 acc|th.04 acc / coverage|th.08 acc / coverage|
|---|---:|---:|---:|
|lizard/exam_temporal/front|78.67%|84.69% / 65.33%|90.74% / 36.00%|
|lizard/exam_temporal/side|96.00%|96.00% / 100.00%|95.86% / 96.67%|
|lizard_clean/exam/front|78.00%|93.75% / 64.00%|100.00% / 32.67%|
|lizard_clean/exam/side|95.33%|96.58% / 97.33%|96.45% / 94.00%|

## 真实片：只报告稳定性和triang冲突（NOT accuracy）

以下为threshold=.04（.08括号内）。冲突分母为保留样本；flip分母是相邻两帧都过阈值的相邻对。高稳定可以稳定地错；高triang冲突也不说明depth一定错。

|run/view/group|coverage .04 (.08)|triang冲突 .04 (.08)|相邻排序flip .04 (.08)|pair diff Δt绝对值均值 / p90|
|---|---:|---:|---:|---:|
|lizard/xiyi_temporal/front/all|42.36% (15.60%)|36.10% (21.85%)|7.04% (6.10%)|0.03225 / 0.08605|
|lizard/xiyi_temporal/front/ankle|47.11% (23.97%)|61.40% (44.83%)|21.05% (9.09%)|0.05717 / 0.14454|
|lizard/xiyi_temporal/side/all|100.00% (98.45%)|1.65% (1.57%)|0.83% (0.85%)|0.02146 / 0.05095|
|lizard/xiyi_temporal/side/ankle|100.00% (98.35%)|4.13% (3.36%)|1.67% (1.72%)|0.02623 / 0.05367|
|lizard/xiyi2_temporal_web/front/all|27.27% (0.72%)|43.56% (0.00%)|1.75% (0.00%)|0.00504 / 0.01134|
|lizard/xiyi2_temporal_web/front/ankle|39.67% (5.79%)|20.83% (0.00%)|8.89% (0.00%)|0.01207 / 0.01827|
|lizard/xiyi2_temporal_web/side/all|100.00% (100.00%)|0.00% (0.00%)|0.00% (0.00%)|0.00268 / 0.00664|
|lizard/xiyi2_temporal_web/side/ankle|100.00% (100.00%)|0.00% (0.00%)|0.00% (0.00%)|0.00260 / 0.00642|
|lizard/xiyi3_temporal/front/all|21.90% (0.10%)|28.77% (100.00%)|0.00% (n/a)|0.00478 / 0.00970|
|lizard/xiyi3_temporal/front/ankle|39.67% (0.83%)|97.92% (100.00%)|0.00% (n/a)|0.01164 / 0.02661|
|lizard/xiyi3_temporal/side/all|100.00% (100.00%)|0.00% (0.00%)|0.00% (0.00%)|0.00317 / 0.00739|
|lizard/xiyi3_temporal/side/ankle|100.00% (100.00%)|0.00% (0.00%)|0.00% (0.00%)|0.00293 / 0.00759|
|lizard_clean/xiyi3_clean_temporal/front/all|7.95% (0.00%)|77.92% (n/a)|0.00% (n/a)|0.00414 / 0.01048|
|lizard_clean/xiyi3_clean_temporal/front/ankle|16.53% (0.00%)|75.00% (n/a)|0.00% (n/a)|0.00522 / 0.01202|
|lizard_clean/xiyi3_clean_temporal/side/all|100.00% (100.00%)|0.00% (0.00%)|0.00% (0.00%)|0.00386 / 0.00957|
|lizard_clean/xiyi3_clean_temporal/side/ankle|100.00% (100.00%)|0.00% (0.00%)|0.00% (0.00%)|0.00397 / 0.00976|

完整0/.01/.02/.04/.08曲线、每个镜像pair、conf/conf_fit筛选、triang强间距对照及时间差分见JSON。lizard xiyi3 front ankle在.04为47/48 triang冲突、37个相邻保留对0次flip；clean为15/20冲突、17个相邻对0flip。但是这些保留样本的triang |diff|全部<=.02size，不能称97.92%深度错误；这是近等深几何下的稳定符号偏置/排序冲突。xiyi2 front ankle同样没有任何triang |diff|>.02的保留样本。真实片front经常接近左右等深；建议结合相机朝向/triang间距查看冲突，不能把“相同front/side符号”当跨视图一致性（两相机深度轴不同）。

## 建议：不把测试后最佳阈值当预设

1. **depth只做软排序证据，不自动驱动硬swap，更不用于双遮挡关节的硬重建。** MAE约.023–.024size，但ankle误序更高；真实片与同UV三角测量冲突明显且无GT。
2. 若必须设计未验证的工程初始门槛，可预先登记 **|Δpred|<=.04size abstain；>.04仅弱软约束；>.08只标记strong候选，仍需conf_fit/几何/连续性共同同意**。这里的.04/.08是可解释的4%/8%体尺margin级别，不声称由本考试优化而得，也不声称达到某个错误上限。后续应在新held-out动作/新遮挡采样验证，才能变为正式默认。
3. 尤其ankle.L/R真实双遮挡先保持abstain。当前lizard在.04仅2/4正确，.08仅1/1；clean .04为5/5、.08为2/2，证据远不足。更高阈值不能从此小样本证明解决遮挡。
4. 保留temporal的正确slot对齐；未来核心变更候选（本次未改）：legacy统一permutation、持久化raw/aligned depth及permutation，区分UV整链换标与depth身份，加入depth-vs-swap冲突标记。不要用被错误整链交换的高conf深度强迫solver。
5. 需要校准coverage/error时，以独立片段为统计单位；不能将150帧8对×2视图当2400个独立样本，不能对真实片用triang选择“最佳准确率阈值”。

## 产物

- 本地 `work/analyze/depth/audit_depth.py`、`supplement_depth.py`：可复现只读脚本。
- 本地 `work/analyze/depth/depth_audit.json`、`depth_supplement.json`：完整数值；远端同内容 `/tmp/v2r_depth_audit.json`、`/tmp/v2r_depth_supplement.json`。
- source_*.py 为本次远端源码只读快照（非部署文件）。
