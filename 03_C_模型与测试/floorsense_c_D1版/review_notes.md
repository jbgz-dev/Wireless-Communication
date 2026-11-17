# C 岗位基线实现复核

复核日期：2026-11-09。范围：`pipeline.py`、`predict.py`、`test_pipeline.py`、`EXPERIMENT_PLAN.md` 和已生成 `results/`。仅写本复核记录，没有修改训练或预测代码。

## 结论

当前实际数据及主结果未发现阻断性的标签、数据泄漏、计分或模型导出问题。主结果是按 Point ID 留出的训练集内五折交叉验证；不能称为独立测试准确率。以下验证已经实际运行通过，不仅是静态阅读。

## 已实际核验

- 原始可用 fresh scan 为 1,003 条，Point ID 内去重后 271 条；50 个 Point ID，原始可用数据每点 N/E/S/W 四朝向齐全。全部 sample_id 唯一。
- 五个种子 42–46 各有五折。每折训练 40 点、验证 10 点，验证每层恰有 2 点。同一点全部方向始终归同一折。保存的 `point_folds.csv` 与确定性重建一致。
- 所有 25 个划分的训练/验证 Point ID、完整特征指纹均不相交。另检查按当前训练词表投影后的实际向量，训练/验证向量也无完全相同项。
- AP 词表由每个训练折的 BSSID 并集生成；重算维度与 `fold_diagnostics.csv` 一致。验证独有 AP 被忽略。所有实际验证扫描至少包含一个已知 AP。
- 每个 dataset/seed/model 下，每个 sample_id 恰有一条预测，样本集合与对应原始/去重数据完全相同。预测表中的 floor、zone、point_id 与数据加载结果一致。
- 独立重算了所有种子的总指标和各折指标：scan accuracy、当前定义的 macro-F1、Point ID 等权 accuracy、floor 与 zone 同时正确率及样本数，均与 CSV 相符。seed=42 的六张混淆矩阵也一致。
- KNN 导出模型为 k=5、distance 权重、euclidean 距离；RF 为 300 棵树。两类模型的两个目标均有正确类别集：floor 2–6，zone East/Middle/West。
- 两个 joblib 成功重载，特征数与保存词表相同，并能调用 `predict.predict`。全未知 AP 输入返回 `insufficient_known_aps`。示例预测只是接口验证，不是准确率测试。
- `knn_reference.json` 的矩阵、标签序列、AP 顺序与全部去重训练数据重新编码一致。
- 执行 `.venv/bin/python -m unittest -v test_pipeline.py`，两项测试通过：验证独有 AP 不进入词表；跨点相同指纹连接分组不拆开。

## 非阻断性说明

1. **各折 macro-F1 的标签集合：已由主代理修复。** 主代理反馈已新增 `test_fold_macro_f1_keeps_all_target_classes`，先复现失败再修复通过，当前四项测试通过，并正在全量重跑刷新折级指标；本复核代理没有重跑核验该修复。以下保留发现原因：复核时 `pipeline.py` 中 `metric_rows` 使用 `f1_score(..., average='macro')`，没有固定 `labels`。seed43/fold0、seed45/fold0、seed46/fold2 和 fold4 的验证点没有 West。此时 zone macro-F1 的平均分母可能是两类或三类，取决于预测是否输出 West；不宜把各折值直接当作固定三分类 macro-F1 比较或平均。如果展示折级 macro-F1，建议显式使用完整目标标签。主表 pooled OOF 每个种子包含全部三类，此问题不影响主结果。主代理已明确完整目标类别集，修复后此项不再是待处理问题。点级 macro-F1 同样不宜作为跨点比较重点，点级准确率更直接。
2. **“每样本一次”的断言可以更精确。** 复核时 `evaluate` 只断言每模型 sample_id 的 nunique 等于数据行数；这能发现漏样本，却不能独立排除重复预测。实际导出已另外按 `(dataset, seed, model, sample_id)` 检查，计数全部为 1。可把该精确检查保留在实现中，防止未来重构破坏约定。
3. **手机端需要补齐原始扫描到字典的契约。** `predict.py` 输入是已经聚合的 BSSID→RSSI 字典。调用方应先执行 fresh/updated 筛选、age≤10,000ms、同 BSSID 中位数聚合和大小写统一；该简化输入本身不携带 age/fresh 或重复行，无法代替采集端预处理。当前示例和训练向量的推理契约一致。主代理反馈已在报告和 README 补充该预处理契约及未校准 score 的说明。
4. **点位等权准确率不是“50 点多数投票分类准确率”。** 当前计算先求每点扫描正确比例，再对 50 点等权平均。报告应保留这个定义，不能把它写成按点投票后的正确点数。bootstrap 也是对这 50 个固定 OOF 点位准确率重采样的描述区间，未重新训练模型，不能当成独立测试或全部训练不确定性的置信区间。主代理反馈已在报告和 README 明确 Point ID 等权准确率的定义。

## 解释边界

Point ID 分组符合当前冻结方案，但规划中实际物理坐标未填写；C01/C02 和 F01/F02 的现场空间独立性仍需采集记录支持。楼层与采集日/时段混杂，全部来自同一手机与采集人；本实现正确避免了标识层面泄漏，并未证明跨日期、跨设备或跨用户泛化。只有 50 个位置，271 种特征指纹不等于 271 个独立位置。
