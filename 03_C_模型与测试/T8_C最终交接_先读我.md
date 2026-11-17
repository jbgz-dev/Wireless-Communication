# T8 FloorSense C 最终交接

Android 接入使用 `D_handoff/realtime/` 中的冻结版 **V1**。本包包含 2026 年 11 月 16 日新版规划的 **70 个静态 Test 点**及同日有限校准实验 **Vcal9**。每点独立冷启动，按前 30 秒内最后一次有效更新评分。V1 首次正确楼层更新的中位时间是 **3.95 秒**，70 个点均有有效更新。

## C 部分与课程要求

| 评分项 | 已完成与待验收 |
|---|---|
| Basic 1 · 三层自动楼层识别（12%） | Wi-Fi 模型已实现。2–4F 共 52 个测试点，楼层正确 **51/52（98.08%）**，达到 50 点和 90% 的量化要求。 |
| Basic 2 · 手机实时运行（4%） | 已交付自动更新、弱信号时保持上次楼层、信号恢复后继续更新的参考实现及测试向量。下一步在 Android 真机核对结果和 UI 状态。 |
| Basic 3 · 评估与可复现性（4%） | 已交付逐层准确率、逐点预测、混淆矩阵、原始数据及可复现评分。已核对 Train、Validation、Test 的点号、Session ID 和指纹。实际站位独立性由现场记录佐证。D1/D2 覆盖四朝向，本批 D3 为 N 朝向。 |
| Advanced 1 · 五个连续楼层（2%） | 已实现 T8 2–6F 识别，静态测试楼层正确 **69/70（98.57%）**。 |
| Advanced 7 · 区域识别（最高 3%） | West / Middle / East 已实现，Zone 正确 **52/70（74.29%）**，楼层与 Zone 均正确 **51/70（72.86%）**。Middle 正确 **14/28（50.00%）**，演示时可说明区域精度。 |
| Advanced 5 · 跨时间校准（3%） | 已跑 9 个同日短时锚点的配对实验。Vcal9 在同一批 70 点上，楼层仍为 **69/70（98.57%）**，Zone 从 **52/70（74.29%）** 提升到 **56/70（80.00%）**，联合正确从 **51/70** 提升到 **55/70（78.57%）**。这是初步结果，原 10 锚点方案还缺 5F-A，现有时长也短于原约定。 |

Advanced 2 的楼梯和电梯自动换层由对应组员接续。静态五层成绩用于 Basic 1 和 Advanced 1。两种机型均有现场采集记录，设备和楼层分组绑定，跨设备鲁棒性需要另做对照。各项的课程分值和现场演示要求见 [课程说明](Final%20Project%20Description.pdf)。

## Android 接入

| 文件 | 用途 |
|---|---|
| [V1 模型](floorsense_c/d2_validation/D_handoff/realtime/wifi_model.json.gz) | 随机森林模型，包含楼层、Zone、特征顺序和实时门限。 |
| [特征配置](floorsense_c/d2_validation/D_handoff/realtime/feature_config.json) | 用 BSSID 对齐 RSSI，按配置处理缺失、越界和过期信号。 |
| [接入说明](floorsense_c/d2_validation/D_handoff/README.md) 和 [Python 参考实现](floorsense_c/d2_validation/D_handoff/realtime/mobile_reference.py) | 核对模型输入、树推理、输出字段和状态转换。 |
| [分类对照向量](floorsense_c/d2_validation/D_handoff/realtime/parity_vectors.json) 和 [实时对照向量](floorsense_c/d2_validation/D_handoff/realtime/realtime_policy_vectors.json) | 对齐 Kotlin 和 Python 的分数、楼层、Zone 及冷启动、保持、恢复行为。 |

1. 在 Android 加载 `wifi_model.json.gz`。每次 Wi-Fi 扫描按特征配置预处理，依据模型内 `classes` 的顺序解释分类输出。
2. 每个定位会话从空楼层启动。已知 AP 至少 15 个且楼层投票分数至少 0.50 时输出 `ok`，更新楼层与 Zone。其余情况输出 `waiting` 或 `held`。`held` 保留上一次有效楼层，本次 Zone 为空。
3. 用两份对照向量核对 Kotlin，再用真机记录首次定位、弱信号保持和信号恢复。日志保留时间、扫描 ID、已知 AP 数、楼层分数、楼层、Zone 和状态。报告使用手机显示及真机日志作为实时功能证据。

## 正式结果和复跑

- [70 点静态测试报告](floorsense_c/d3_results/2026-11-16_70pt/T8_D3_C_70点静态测试报告.md)
- [逐点结果](floorsense_c/d3_results/2026-11-16_70pt/point_review.csv)、[逐扫描日志](floorsense_c/d3_results/2026-11-16_70pt/scan_events.csv)、[楼层混淆矩阵](floorsense_c/d3_results/2026-11-16_70pt/cm_V1_floor.csv)、[Zone 混淆矩阵](floorsense_c/d3_results/2026-11-16_70pt/cm_V1_zone.csv)
- [模型与数据哈希](floorsense_c/d3_results/2026-11-16_70pt/inputs.json)、[训练和验证重叠检查](floorsense_c/d3_results/2026-11-16_70pt/fingerprint_overlap_review.json)
- [同日校准报告](floorsense_c/d3_results/2026-11-16_calibration/T8_Advanced5_同日校准对照.md)、[逐点配对](floorsense_c/d3_results/2026-11-16_calibration/paired_points.csv)、[校准候选模型 Vcal9](floorsense_c/d3_results/2026-11-16_calibration/fit/wifi_model.json.gz)

在 `floorsense_c` 下用已安装 `openpyxl` 的 Python 复跑。测试 ZIP 保留提交时的内容，评分脚本按新版工作簿只读取静态 Test。

```bash
python d3_results/2026-11-16_70pt/evaluate_static_subset.py \
  --workbook 'd3_results/2026-11-16_70pt/data/D3_静动态Test规划(3).xlsx' \
  --test 'd3_results/2026-11-16_70pt/data/Static_Test_stair2-4(2)(2).zip' \
         'd3_results/2026-11-16_70pt/data/floorsense_V2068A_1794831778552(1)(3).zip' \
  --d1 data/static_train.zip \
  --validation d2_validation/data/static_validation.zip \
  --out d3_results/replay_2026-11-16_70pt
```

## 复跑 Advanced 5 同日校准

Vcal9 保留作实验对照。它比 V1 多了一次中途跳层，最终楼层预测逐点一致。复跑时在 `floorsense_c` 下用已安装 `requirements.txt` 依赖的 Python 执行下列命令。

```bash
python d3_results/2026-11-16_calibration/evaluate_short_anchors.py \
  --anchors 'd3_results/2026-11-16_calibration/data/floorsense_V2068A_1794837501492(1).zip' \
  --workbook 'd3_results/2026-11-16_70pt/data/D3_静动态Test规划(3).xlsx' \
  --test 'd3_results/2026-11-16_70pt/data/Static_Test_stair2-4(2)(2).zip' \
         'd3_results/2026-11-16_70pt/data/floorsense_V2068A_1794831778552(1)(3).zip' \
  --d1 data/static_train.zip \
  --out d3_results/replay_2026-11-16_calibration
```
