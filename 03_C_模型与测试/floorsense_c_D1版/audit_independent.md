# FloorSense 静态 Wi-Fi 独立数据审计

审计日期：2026-11-09。审计只读取 `data/static/sessions` 和采样规划，不修改训练实现或原始数据。可复现脚本为同目录 `audit_independent.py`；脚本只向 stdout 输出 JSON。

## 结论

本批数据可用于按 Point ID 隔离的五楼层、West/Middle/East 静态基线开发。50 个预定训练点全部覆盖，每点 N/E/S/W 各一个 session，共 200 个 session。当前数据全部属于训练集，不能把训练点内交叉验证结果称为独立盲测成绩。

最需要处理的风险是：

1. 1,016 次扫描只有 271 种完整原始指纹，152/200 个 session 内只有一种指纹。扫描数量明显大于信息变化数量。随机按行或按扫描切分会产生严重乐观偏差；按 session 切分也不能解决同一物理点跨朝向泄漏。
2. 2–5F 全在 11 月 8 日晚采集，6F 全在 11 月 9 日上午采集。楼层与日期、采集时段混杂，Point ID 分组不能消除该问题。
3. 只有 P2 和 vivo V2068A。当前无法验证跨人、跨设备、跨日期泛化。
4. 一个 session 中 4 个 scan 含重复 BSSID 且 RSSI 不同。向量构建必须显式聚合，不能依赖 CSV 行顺序覆盖。
5. 每层 West 只有 2 个训练 Point ID。若要求每个验证折覆盖所有 15 个 floor×zone 类别，最多只能做 2 折。

## 数据结构与完整性

| 项目 | 实际结果 |
|---|---:|
| session | 200 |
| Point ID | 50 |
| 楼层 | 2、3、4、5、6，各 10 点 / 40 session |
| 朝向 | N/E/S/W 各 50 session；每个点各 1 个 |
| Wi-Fi scan | 1,016 |
| Wi-Fi AP 记录行 | 54,028 |
| 不同 BSSID | 1,226 |
| `fresh=1` 且 `results_updated=1` | 1,003 scan，98.72% |
| `fresh=0` 且 `results_updated=0` | 13 scan，1.28% |
| 完整原始指纹种数 | 271 |
| session 状态 | 200 个均为 `complete` |

200 个目录均有 `meta.json`、`wifi.csv`、`events.csv`、`sensors.csv`、`sensors.json`、`ble.csv`、`cell.csv`。本次详细数值审计针对 meta 与 Wi-Fi；文件存在不代表其余模态已经完成数据质量审计。

所有 session 的 meta 和 Wi-Fi 字段集合一致：

- meta 顶层：`schema_version, session_id, status, start_wall_ms, start_local, timezone, end_wall_ms, labels, device, env_at_start, stats`。
- labels：`collector, building, floor, zone, point_id, split, mode, orientation, posture, target_scans, notes`。
- device：`manufacturer, model, device, android_release, sdk_int`。
- stats：`duration_s, wifi_scans, wifi_scans_fresh, wifi_scan_rejected, wifi_scan_requests, wifi_results_not_updated, wifi_mean_fresh_interval_s, ble_packets, ble_unique_devices, cell_samples, sensor_events, mean_azimuth_deg`。
- Wi-Fi：`scan_id,t_wall_ms,t_elapsed_ms,fresh,n_ap,bssid,ssid,rssi,freq_mhz,channel_width,wifi_standard,age_ms,results_updated`。

所有标签均为 `building=T8, collector=P2, split=train, mode=static, posture=hand_flat, target_scans=5`。设备均为 `vivo / V2068A / PD2068 / Android 11 / SDK 30`。meta 的环境标记均为 location/Wi-Fi/Bluetooth 开启、`wifi_throttled=false`、`missing_permissions=""`；这些是采集程序记录的状态，并非外部验证。

核对结果：session ID 与目录一致；Point ID 的 building/floor/区域/split 与规划一致；scan ID 连续；session 内时间单调；扫描时间在 session 起止范围内；每 scan 的公共字段一致；`n_ap` 与记录行数一致；meta 的总 scan、fresh scan、未更新 scan 数与 CSV 一致。唯一结构异常是下述重复 BSSID。

每点四朝向记录完整。`mean_azimuth_deg` 与名义朝向的圆周角最大差值分别为 N 1.976°、E 2.095°、S 2.994°、W 2.190°。这只表明记录值一致，不能证明传感器已校准或实际持机方向绝对准确。

## 规划与实际样本

来源：`data/T8_FloorSense_Integrated_Sampling_v2.xlsx` 的 `Point Register!A1:R140`、`Zone Mapping!A1:K41`。工作簿只读，未修改。

规划共 139 点：50 Train、15 Validation、74 Test。当前目录中的 50 个点恰好覆盖全部 Train 点，没有未规划点，也没有 Validation/Test 点。规划表中这 50 个 Train 点的“现场具体位置描述”和“距西端约多少米”均为空。因此 Point ID 可作为目前的分组键，但记录本身还不能证明 C-TR01/C-TR02 等是不同物理位置。应补充现场位置、距离或照片以使空间隔离可审计。

`meta.labels.zone` 是 A–H 采样区域代码，不能直接当作三类粗区域标签。五层映射一致：

| 原始区域 | 模型 zone | 点数 | session | scan |
|---|---|---:|---:|---:|
| A、B | West | 10 | 40 | 210 |
| C、D、E | Middle | 20 | 80 | 401 |
| F、G、H | East | 20 | 80 | 405 |

每一层均为 West 2 点、Middle 4 点、East 4 点，zone 不是平衡数据。应同时报告 zone macro-F1、各 zone 召回与准确率，避免只报 overall accuracy 掩盖 West 表现。

| 层 | 点 | session | scan | fresh scan | 完整指纹种数 | 采集开始 / 结束（北京时间） |
|---|---:|---:|---:|---:|---:|---|
| 2F | 10 | 40 | 202 | 200 | 52 | 11-08 19:27:49 / 19:51:08 |
| 3F | 10 | 40 | 201 | 200 | 53 | 11-08 19:53:28 / 20:07:34 |
| 4F | 10 | 40 | 205 | 201 | 61 | 11-08 20:09:38 / 20:22:06 |
| 5F | 10 | 40 | 208 | 202 | 53 | 11-08 20:24:20 / 20:36:26 |
| 6F | 10 | 40 | 200 | 200 | 52 | 11-09 11:01:40 / 11:12:48 |

## Fresh、缓存证据与重复指纹

本审计的“完整原始指纹”定义为：单个 scan 的全部 `(小写 BSSID, 整数 RSSI, 整数 freq_mhz)` 记录排序后的元组。保留重复 BSSID 记录；不把时间戳、age、SSID 或标签放入指纹。该定义衡量无线输入的精确重复，不等于统计独立样本数，也不能单独证明底层扫描是否重新执行。

| 指标 | 结果 |
|---|---:|
| 总指纹种数 | 271 / 1,016 scan |
| fresh 且 updated 的指纹种数 | 271 / 1,003 scan |
| 相对全局唯一指纹的重复 scan 数 | 745，73.33% |
| 各 session 内去重后合计 | 276 |
| 各 session 内重复 scan 数 | 740，72.83% |
| session 内只有 1 / 2 / 3 / 4 种指纹 | 152 / 22 / 24 / 2 个 session |
| 至少重复一次的指纹簇 | 224 簇 |
| 落在重复指纹簇的 scan | 969，95.37% |
| 每个 Point ID 的指纹种数 | 最小 4，中位数 6，最大 9 |
| 相邻扫描指纹完全相同 | 737 对 |
| 跨 session 重复指纹 | 5 簇，共涉及 28 scan |
| 跨 Point ID / floor / coarse zone 完全重复 | 0 / 0 / 0 簇 |

13 个未更新 scan 没有增加新的完整指纹种类。全部保留数据的 AP age 为 16–8,075 ms，中位数 126 ms，95 分位数 232 ms。全部 fresh 数据的 age 为 16–2,205 ms，中位数 125 ms，95 分位数 220 ms；只有 12 行 age >1,000 ms，其中 2 行 >2,000 ms，没有 >5,000 ms 的 fresh AP 行。

| age 阈值 | 全部 AP 行超过阈值 | 至少一个 AP 超阈值的 scan | 全部 AP 均超阈值的 scan |
|---|---:|---:|---:|
| >1,000 ms | 654 | 19 | 11 |
| >2,000 ms | 355 | 8 | 7 |
| >5,000 ms | 158 | 4 | 4 |
| >10,000 ms | 0 | 0 | 0 |

进一步以 `t_elapsed_ms - age_ms` 估算 AP 观测时刻。在 737 对相邻相同指纹中，只有 9 对的所有 AP 观测时刻都在 5 ms 内保持不变；后一个 scan 全部为 `fresh=0`（5 对 fresh→非 fresh，4 对非 fresh→非 fresh）。其余重复指纹通常 age 重置而估算观测时刻向前。因此当前证据支持“有 13 次明确未更新；大量 fresh 扫描携带相同无线特征”，不支持把 745 次精确重复全部判为已证实缓存。仍应核实采集端 fresh/age 的实现，避免将系统回调成功直接解释为信息独立。

跨 session 重复全部在同一 Point ID 内：

| Point ID | 重复指纹簇数 | 涉及 scan 数 | 两个 session 的起始时间 |
|---|---:|---:|---|
| T8-F2-F-TR02 | 1 | 7 | 11-08 19:46:27 / 19:46:40 |
| T8-F5-A-TR01 | 2 | 13 | 11-08 20:24:20 / 20:24:29；20:24:43 / 20:24:55 |
| T8-F5-B-TR01 | 1 | 6 | 11-08 20:25:48 / 20:25:59 |
| T8-F5-C-TR01 | 1 | 2 | 11-08 20:27:11 / 20:27:23 |

Point ID 分组可隔离这些精确重复。没有跨 Point ID 完全相同指纹，并不证明邻近点之间没有高度相似特征。

## 局部异常与实现要求

**重复 BSSID。** `20261109-110220_T8_F6_static_T8-F6-A-TR01_P2` 的 scan 1、2、3、4 中，4 个 BSSID 各出现两次：`xx:xx:xx:xx:xx:xx`、`:91`、`:92`、`:93`。频率均为 5745 MHz；有 SSID 的记录 RSSI 分别为 -79、-80、-80、-81 dBm，空 SSID 的第二条均为 -85 dBm。因此共有 16 个“scan×BSSID”重复键、16 条额外记录。建议在每个 scan 内按 BSSID 采用固定的 max-RSSI 或 median 聚合规则，并在训练和手机推理共用。此处两种方案会得到不同值，不能靠最后一行覆盖。

**少 AP。** 8 个 fresh scan 仅 1–5 个 AP，全部属于 `T8-F2-F-TR02`：

- `20261108-194652...`，S 朝向，scan 2/3/4 各 1 AP。
- `20261108-194706...`，W 朝向，scan 0 为 4 AP，scan 1/2/3/4 各 5 AP。

其余扫描至少 10 AP。总体每 scan AP 数为最小 1、中位数 50、95 分位数 95、最大 157。不要在看过验证错误后单独移除上述点；可以预先规定低 AP 时的拒识或质量标记，并同时报告覆盖率和有效输出准确率。若进行清洗敏感性实验，应完整披露删除规则和样本数。

**采样时长与间隔。** session 时长中位数 6.725 秒，范围 3.714–9.328 秒。fresh scan 相邻间隔中位数 1.508 秒，最小 0.014 秒，最大 3.402 秒；并不能把每个回调当作独立随机样本。197 个 session 有 5 次 fresh，3 个有 6 次；记录的 Wi-Fi 请求数为 3–6 次，fresh 数偶尔超过请求数。事件可能有回调或时间边界差异，宜检查采集端；该现象本身不能证明伪造或缓存。

## 对 KNN / RF 验证的建议

1. **固定按完整 Point ID 分组。** 同一 Point ID 的四朝向、全部扫描、后续日期/设备回访必须进入同一折。不能把 `session_id` 当作替代分组键，也不能按行、扫描或朝向随机拆分。
2. **先确定验证问题。** 若要求每折都有五层×三类 zone，使用按 `(floor, coarse_zone)` 分层的 2 折 Point ID 验证，每层每折 West 1 点、Middle 2 点、East 2 点，共每折 25 点。可用多个固定随机种子检查划分敏感性。若坚持 5 折，则以 floor 保证覆盖，显式展示每折 floor×zone 点数；每层 West 只有两点，不能声称每折都覆盖 15 类。
3. **所有拟合操作限制在训练折。** BSSID 字典、AP 选择、特征筛选、缺失填充值的估计、标准化及调参只能使用训练侧。若填充值固定为事先规定的常数，也应在训练与推理一致。时间戳、point/session ID、采集顺序、真实 floor/zone、collector 等不是 Wi-Fi 预测输入。
4. **同时计算扫描与点位指标。** 主报告给出每点投票或平均概率后的 50 点结果、五层混淆矩阵、zone 混淆矩阵、15 个 floor×zone 的 joint accuracy，以及 macro-F1。扫描级结果可以附列，但它会放大重复指纹和某些 session 的权重。置信区间或重采样应按点位聚类，不能按 1,003 个 fresh scan 独立重采样。
5. **控制重复指纹权重。** 建议主基线先排除明确的 13 个未更新 scan，并明确采用 session 内精确去重、每 session 权重归一化、或 session 代表指纹中的一种。与“保留全部 fresh scan”做同一 Point ID 划分的敏感性对照。KNN 中重复样本可重复投票，RF 中重复样本可改变采样权重；高重复不能作为额外独立证据。271 种指纹也仍不等于 271 个独立物理样本。
6. **区分楼层与 zone 任务。** 报告 floor accuracy、全局三类 zone accuracy、floor+zone 同时正确率。若 zone 是楼层条件模型，主 joint 结果必须用预测楼层路由；用真实楼层路由的 zone 分数只能单列为诊断上限。
7. **避免挑选最优折充当测试。** 当前全为 Train 点，分组 CV 是开发验证。冻结选择后的模型，再使用规划中的 15 个 VA 点或独立开发验证批次；74 个 TS 点只用于最后盲测。若反复使用同一 CV 选 KNN/RF/超参，应披露选择偏差，或用嵌套分组验证。

当前数据不足以做有意义的 leave-one-date-out 五楼层测试：留出 11 月 9 日会使训练集中完全没有 6F，留出 11 月 8 日则训练集中只剩 6F。需要在至少另一采集时段复测所有五层、尽量交错楼层/区域顺序，随后用独立日期的 VA/TS 点评估。跨设备与跨用户也需要第二手机、第二用户的匹配采集才能下结论。

## 复现

在 `floorsense_c` 的父目录运行：

```bash
/Users/giotto/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 floorsense_c/audit_independent.py
```

依赖仅标准库和只读 `openpyxl`。脚本输出原始统计、schema、异常定位、跨 session 重复清单和各点指纹数。本报告的总 session、scan、fresh scan 已与分层求和独立核对，四朝向完整性检查通过。审计未训练模型，因此没有宣称 KNN/RF 精度或实际手机表现。
