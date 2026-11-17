# 复现 Advanced Feature 5

`Advanced_Feature_5.md` 是报告章节，`PROTOCOL.md` 记录了训练、评分和选型规则。从交付包根目录进入 `floorsense_c/` 后，使用原项目 `requirements.txt` 的 Python 环境，按顺序运行：

```bash
python temporal_robustness/temporal_eval.py replay
python temporal_robustness/paired_drift.py
python temporal_robustness/temporal_eval.py compare
python temporal_robustness/make_figure.py
```

冻结回放和配对分析只用 Python 标准库。重训使用 sklearn；出图使用 matplotlib。数据和冻结接入包在交付目录内，代码直接读取 ZIP。`compare` 验证之前保存的模型、复测归档和回放明细哈希，再用 D1 与复测 Train 数据训练。

`results/v1_temporal_scans.csv` 保留所有回调的预测和去重标记，`results/v1_paired_point_predictions.csv` 比较匹配 N 朝向的两期点位。`results/v1_v2_validation.csv` 和 `results/validation/` 保存模型对比。`results/v2_candidate/` 用于研究复现，`d2_validation/D_handoff/realtime/` 对应本轮保留的 V1 接入版。
