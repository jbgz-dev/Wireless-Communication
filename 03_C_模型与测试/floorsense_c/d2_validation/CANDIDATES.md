# D2 候选模型

两种候选只用 D1 Train 的去重扫描拟合。先保留出现在至少两个 D1 训练点的 BSSID，降低模型对训练点特有 AP 的依赖。全部 D1 Train 有 50 个点、271 条去重扫描，筛选后保留 934 个 BSSID。RSSI 特征沿用 D1 的裁剪、缺失填充和 BSSID 排序规则。

楼层和 Zone 分别训练。每个目标中，扫描权重为所在点的扫描数和所属类别的点数乘积的倒数，再统一缩放至平均权重为 1。这样，同一类别的点有相同的总权重，每个类别的总权重也相同。D1 的 Zone 点数是 West 10、Middle 20、East 20，因此该权重提高了 West 点的相对贡献。

| 候选 | 固定设置 | 设计目的 |
|---|---|---|
| `stable_ap_balanced_rf` | 300 棵树、每叶至少 2 条扫描、每次分裂考虑约 √p 个特征、随机种子 42 | 允许多个 AP 的 RSSI 组合形成非线性规则，限制叶节点对单一扫描的依赖。 |
| `stable_ap_balanced_logistic` | L2 正则化，C = 0.01，LBFGS，最多 2,000 次迭代 | 使用加性 RSSI 特征分数。部署端可按固定 BSSID 顺序用系数、截距和类别列表计算预测。 |

`train_candidates(train_df)` 接收 `pipeline.load_data` 返回的 D1 去重表，返回候选名称到模型包的映射。每个包都可直接传入 `predict.predict(scan, bundle)`。两项目标的输入都只有 BSSID 和 RSSI。楼层预测也无需作为 Zone 的输入。若当前扫描没有词表内的 BSSID，现有预测接口返回 `insufficient_known_aps`。

## D1 五折参考

沿用 D1 的 seed 42 点位划分。每折的 AP 词表、样本权重和模型只由该折的训练点计算。下表为 271 条去重扫描的折外预测汇总。

| 模型 | 楼层准确率 | Zone 准确率 | Zone Macro F1 | 联合准确率 |
|---|---:|---:|---:|---:|
| D1 RandomForest | 98.52% | 76.75% | 75.90% | 75.65% |
| `stable_ap_balanced_rf` | 99.26% | 74.54% | 73.45% | 74.17% |
| `stable_ap_balanced_logistic` | 99.26% | 76.38% | 76.12% | 76.01% |
