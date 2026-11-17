# D2 阶段复现

先读 `D2验证报告.md`。`baseline/` 存冻结 D1 模型的验证，`comparison/` 为基线加新候选的同口径验证。两份 D2 ZIP 与 D1 训练归档独立存放。动态交接使用 `dynamic_validation_manifest.csv` 和 `dynamic_event_overrides.csv`。

完整 D2 交付包解压后已包含 `floorsense_c/`、D1 训练归档与两份 D2 原始归档。也可把本目录放在已完成第一阶段的 `floorsense_c/` 下，复用 `pipeline.py`、`point_labels.json`、`results/models/{KNN,RandomForest}.joblib`、`data/static/sessions`。如只有训练压缩包，先在 `floorsense_c/` 运行 `python prepare_data.py`。使用 D1 `requirements.txt` 的版本。从 `floorsense_c/` 执行以下命令；若使用单独下载的 D2 文件，将第 1 行换成实际路径。

```bash
d2_static_archive='d2_validation/data/static_validation.zip'
python -m unittest discover -s d2_validation -p 'test_*.py'
python d2_validation/validate.py "$d2_static_archive"
python d2_validation/validate.py "$d2_static_archive" --candidates --out d2_validation/comparison
python d2_validation/export_d.py "$d2_static_archive" --mode scan --out d2_validation/D_handoff/realtime
python d2_validation/export_d.py "$d2_static_archive" --mode point --out d2_validation/D_handoff/static_comparison
python d2_validation/make_d2_figures.py
python d2_validation/replay_realtime.py
```

`validate.py` 检查规划中 VA 点身份，与 Train ID 无交集。归档直接读取。候选重训只向 `train_candidates()` 传 D1 清洗结果。当前先复测 3F B-VA01，模型仍使用已存在的 50 个 D1 点。实时回放只需要 Python 标准库，从本阶段 `data/` 读取两个原始 Validation 归档。
