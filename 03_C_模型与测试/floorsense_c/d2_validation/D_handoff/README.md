# 给 D 的 Wi-Fi 模型接入包

实时接口优先使用 `realtime/`（优化随机森林）。`static_comparison/` 是逻辑回归对照，四朝向合并在 D2 得分较好，单次扫描联合正确率较低。两套模型只用 D1 Train 拟合，由 D2 选择，D3 等待真机盲测。

## 文件和接口

- `wifi_model.json.gz` 是便于其他语言读取的模型。顶层字段 `schema_version`、`config`、`models`。两分类目标分别是 `floor` 和 `zone`，类顺序由各自 `classes` 给出。
- `feature_config.json` 定义 934 个 BSSID 的顺序、缺失值 −100 dBm、RSSI 裁剪范围 [−100, −20]、最大 AP age 10,000 ms。先对原始行验证 freshness / results_updated 和 age，把 BSSID 转小写，将合法 RSSI 裁剪后按重复 BSSID 取中位数。忽略词表外 AP，按固定顺序填充缺失值。
- `mobile_reference.py` 只有 Python 标准库依赖。可接收一次扫描的原始 AP 行数组（字段 `bssid,rssi,age_ms,fresh,results_updated`，fresh/updated 使用 0 或 1）或已处理的 BSSID→RSSI JSON。过期、未更新或无有效 AP 的回调不能触发新楼层。
- `parity_vectors.json` 用于核对原始分类器输出。`realtime_policy_vectors.json` 用于核对冷启动、弱信号保持与恢复。`selected.joblib` 可在同版本 sklearn 环境后端调用，只有分类器；后端接入同样要应用下面的状态规则。JSON 与 Python 参考已经离线核对；仍需 D 在 Kotlin 和真机上验证。

从此目录运行：

```bash
python3 realtime/mobile_reference.py realtime/wifi_model.json.gz realtime/example_scan.json --previous-floor 5
```

树模型从结点 0 开始，按 `x[feature] <= threshold` 走左子树，否则走右。叶结点按类顺序归一化，最后对全部树的投票分数求均值。逻辑回归对各类做点积加截距，再做稳定 softmax。最大分数并列取序列首个类别。逻辑回归参考用双精度累加。

## 实时更新规则

使用 `realtime_step(fingerprint, payload, previous_floor)`。`previous_floor` 是本会话上一次**通过门限**的楼层，首次传 `None`，每个会话单独维护。读取 `wifi_model.json.gz` 顶层 `realtime_policy`。已知 AP 是本扫描的有效唯一 BSSID 与模型词表的交集。

| 条件 | `status` | 界面行为 |
| --- | --- | --- |
| 已知 AP ≥15 且楼层投票分数 ≥0.50 | `ok` | `is_new_estimate=true`。更新楼层和 Zone，将楼层存为下一次的 `previous_floor`。 |
| 任一条件未满足，已有楼层 | `held` | 显示上次楼层，标记“信号弱，待更新”，`is_new_estimate=false`，本次 Zone 为空。 |
| 任一条件未满足，尚无楼层 | `waiting` | 显示“等待定位”，`floor=null`，`is_new_estimate=false`。 |

楼层投票分数是模型的内部得分，未经概率校准。CLI 可加 `--raw` 复现原始分类器输出，该模式只用于诊断。现场记录每次接受、保持或等待、已知 AP 数和首次正确更新的延迟。上下楼后仍需要新的合格扫描，或由 E 提供经验证的到达事件。D3 采集前固定评分窗口，把超时无新估计保留在全点位分母中。
