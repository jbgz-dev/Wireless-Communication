# 静态 Wi-Fi 标签与分组评估协议

核对日期：2026-11-09。本文记录标签来源、当前静态数据覆盖和推荐评估设计；未训练模型。原始 XLSX、JSON 和 CSV 均未修改。

## 标签的唯一来源

`point_labels.json` 为以完整 Point ID 为键的对象，共 **139 个规划点**。每个对象包含 `floor`（整数 2–6）、`zone`（West/Middle/East）、`anchor_zone`（A–H）、`split`（Train/Validation/Test）、`planned_location`（规划地标原文）及逐行来源。`field_location` 和 `field_distance_from_west_m` 保留规划文件的空值，不能解释为已确认的现场坐标。

标签取自 `data/T8_FloorSense_Integrated_Sampling_v2.xlsx` 的 `Point Register!A2:O140`，并逐项与 `Zone Mapping!A2:C41` 交叉检查一致：

| 模型 Zone | 原始区域字母 | 每层训练点数 | 五层训练点数 |
|---|---|---:|---:|
| West | A、B | 2 | 10 |
| Middle | C、D、E | 4 | 20 |
| East | F、G、H | 4 | 20 |

**E 必须映射为 Middle**，即使其地理类型写着“走廊东段”。`meta.json` 的 `labels.zone` 是 A–H 原始区域字母，并非模型的三分类 Zone。标签连接应使用 `labels.point_id` 查表，并断言 `labels.floor`、`labels.zone` 和 `labels.split` 与规划一致；未知点或冲突不得默默推断。

Point ID 中的末尾编号是不同规划物理点。例如 C-TR01 与 C-TR02 是两处规划训练点，不是同一位置的重复扫描编号。规划仍要求现场确认它们确实不同；若后续确认两个 ID 实为同一物理位置，需合并为同一个 CV 分组。

## 规划覆盖与当前实际覆盖

每层预期训练点固定为 `A-TR01`、`B-TR01`、`C-TR01`、`C-TR02`、`D-TR01`、`E-TR01`、`F-TR01`、`F-TR02`、`G-TR01`、`H-TR01`，前缀为 `T8-F{floor}-`。

| 楼层 | 规划 Train | 规划 Validation | 规划 Test | 当前静态 Train 点 | 当前 Sessions |
|---|---:|---:|---:|---:|---:|
| 2F | 10 | 3 | 20 | 10 | 40 |
| 3F | 10 | 3 | 16 | 10 | 40 |
| 4F | 10 | 3 | 18 | 10 | 40 |
| 5F | 10 | 3 | 10 | 10 | 40 |
| 6F | 10 | 3 | 10 | 10 | 40 |
| 合计 | 50 | 15 | 74 | 50 | 200 |

当前 `data/static/sessions` 完整覆盖全部 50 个 TR 点，没有 VA/TS 点。每点恰有 N/E/S/W 四个朝向的四次 Session。200 次 Session 均 `complete`，均为采集人 P2、vivo V2068A。2F–5F 于 2026-11-08 采集，6F 于 2026-11-09 采集，因此日期与 6F 标签混杂。

规划依据：`Summary!A3:E9`、`Zone Summary!A2:F16`。独立验证/盲测要求见 `Evaluation Protocol!A2:F13`。动态文件 `T8_Dynamic_Train_Val_Test_Plan.xlsx` 的 `Read Me!A13:C16` 要求整条 Run 分割，动态数据不能替代独立静态测试位置；不应将动态 Run ID 当成这里的静态 Point ID。

## CSV 的观测单位与质量检查

`wifi.csv` 是长表，一行代表一次回调中的一个 AP 结果，不能直接把 AP 行作为分类样本。跨 Session 的唯一扫描键是 `(session_id, scan_id)`；`scan_id` 会在各 Session 中重新从零开始。

当前数据实测：

| 项目 | 数量或结果 |
|---|---:|
| AP 行 | 54,028 |
| 扫描回调 | 1,016 |
| `fresh=1` 且 `results_updated=1` 的扫描 | 1,003 |
| `fresh=0` 且 `results_updated=0` 的扫描 | 13 |
| 完全不同的 fresh 原始指纹 | 271 |
| Session 内去重后的 fresh 原始指纹 | 272 |
| 扫描内额外的重复 BSSID 行 | 16 |

这里“完全相同指纹”定义为扫描内排序后的全部 `(bssid, rssi, freq_mhz)` 三元组相同，包含重复行。1,003 次 fresh 回调中 732 次重复已有原始指纹；没有相同指纹跨不同 Point ID，仅一种指纹跨同一点的不同 Session。该现象说明 fresh 标记不等于新的独立无线信息，但单靠相同 RSSI 不能断言 Android 缓存的具体原因。

全部扫描内部 `fresh`、`results_updated`、时间戳和 `n_ap` 一致；`n_ap` 等于原始行数，包含重复 BSSID。16 条额外 BSSID 行来自 6F A-TR01 的一个 Session 中 4 个扫描，每次四个 BSSID 同频重复但 RSSI 不同。建特征前应固定同一 BSSID 的聚合规则，例如取最大 RSSI，并记录规则。

字段使用规则：

| 字段 | 建议用途 |
|---|---|
| `bssid`、`rssi` | 以 BSSID 为特征列、RSSI（dBm）为值；BSSID 规范为统一大小写 |
| `freq_mhz` | 质量核对或预先固定的频段特征；若使用 BSSID+频率作为键，训练和推理必须一致 |
| `ssid` | 多 AP 可共享名称，不适合作唯一 AP 标识；基线不依赖名称文本 |
| `fresh`、`results_updated` | 基线仅保留二者都为 1 的扫描 |
| `age_ms` | AP 结果年龄，只作质量控制；fresh 行最大 2,205 ms，仅 2 行超过 2,000 ms |
| `scan_id`、`t_wall_ms`、`t_elapsed_ms` | 分组、排序和检查扫描时序，不作为定位特征 |
| `n_ap` | 原始 AP 行数；若使用 AP 数特征，应在聚合/筛选后重新计算唯一 BSSID 数 |
| `channel_width`、`wifi_standard` | 整数类别代码，不能直接解释为带宽 MHz 或连续数值等级 |
| meta 中的 Point/楼层/区域/split/Session/日期 | 标签、分组和审计元数据，禁止作为模型输入 |

使用固定规则聚合重复 BSSID 后，按 `(point_id, 指纹内容)` 去重，以免重复回调隐性增加某些训练点的权重；保留来源 Session/scan 的追踪关系。也可先按 Session 去重，再报告同一点跨 Session 重复情况。上述 271/272 是聚合前的原始三元组统计；最终训练样本数应由实际采用的特征键和聚合规则重新计算，不能直接假定仍为该数。

不应凭检查到的准确率改变 fresh、age 阈值或去重规则。连续样本可以有相同的信号值，去重是当前基线的样本权重选择；保留完整原始文件以便复核。

## 推荐的无泄漏评估

1. **只用 50 个 TR 点开发。** 首先为唯一 Point ID 生成固定的外层 5 折，再将该点的所有 Session、朝向、扫描和复访数据展开到同一折。推荐每个外层验证折含每层 2 点，共 10 点；训练折 40 点。输出折清单和 Point ID 交集为零的检查结果。KNN 与 RF 共用同一折清单。
2. **尽量均衡 Zone，但不要伪造分层保证。** 每层 West 仅 2 点，所以 5 折不可能让每折验证集都包含每个“楼层×Zone”组合。可以按唯一点的楼层分层，再检查总 Zone 组成和训练集标签覆盖。一个可行的确定性设计是各层先配成 `(A,C01)`、`(B,F01)`、`(C02,G)`、`(D,H)`、`(E,F02)` 五对，再按楼层将五对循环轮换分配给五折；每折恰好 2 West、4 Middle、4 East，并有每层 2 点。配对规则仅由规划标签确定，不看信号和分数。
3. **调参只能用内层分组 CV。** 若搜索 k、距离/权重、RF 深度/叶节点、AP 数量、填充值或平滑规则，应在外层训练点内部再按 Point ID 分组；外层保留点只评分。若不做嵌套 CV，需提前固定超参数，或明确将结果标记为调参用 CV，不能作为无偏最终测试成绩。
4. **所有从数据学出的处理只 fit 当前训练折。** 包括 AP 字典、出现频次筛选、Top-K AP、均值/方差、缺失值统计、标准化、特征选择和降维。验证中未知 AP 按固定规则忽略。固定缺失 RSSI 常量（如 −100 dBm）是预先约定的编码；若比较不同常量，也只在内层选择。最终推理必须复用训练时的同一 AP 顺序和处理规则。分组 CV 与训练折独立 fit 的理由参见 [scikit-learn 分组验证](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data) 和 [数据泄漏说明](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)。
5. **评分单位和任务分开报告。** 保存每条留出预测，报告 floor accuracy、floor macro-F1、5×5 混淆矩阵；zone accuracy、zone macro-F1、3×3 混淆矩阵；并报告 `(floor正确且zone正确)` 的联合准确率。若区域模型依赖楼层，评估时必须传入预测楼层，不能传入真实楼层。另给每个 Point 等权的准确率，以及预先固定投票/概率平均规则下的 Point 级预测结果；扫描数不是独立位置数。
6. **最终盲测仍需独立 VA/TS。** 当前只有训练点，所以所得结果应称“按 Point ID 留出的训练内交叉验证”。未来用 15 个 VA 点调优、模型冻结后在 74 个 TS 物理点评分；同一 TS 点的所有日期/设备/朝向始终保持测试身份。多次扫描不增加独立测试位置数。若使用 VA 后重训，报告最终训练范围并保持 TS 完全隔离。

## 结果边界

- 目前可评估同一设备、同一采集人、已采集条件下的新 Point 泛化；不能据此宣称跨设备、跨用户或跨日期表现。
- 6F 与第二天绑定，按日期留出会同时留出整个 6F 类别，不能作为合理的现有五分类跨日期测试。需要在相同楼层/点位追加独立日期数据。
- A–H 是候选区域锚点，规划表未填写实际坐标或明确区域边界。Point ID 分组保证标识不重叠；真实物理位置不同仍需现场记录支持。
- C01/C02、F01/F02 属于相同锚点区域的不同规划点。Point 留出比随机扫描拆分更严格，但未证明跨整片锚点区域或米级位置泛化；如需评估该问题，应单独增加按 `(floor, anchor_zone)` 整组留出的敏感性分析。
