# T8 FloorSense：C 岗位交付

先打开 **实验报告.html**（离线可看）或 **实验报告.md**。

Python 3.12；完整结果已运行保存，不需要重新训练即可查看。

## 新电脑复现

在解压后的项目目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python prepare_data.py
python -m unittest discover -p 'test_*.py'
python pipeline.py
python make_report.py
python predict.py results/example_scan.json
```

Windows 可用 `.venv\Scripts\activate` 激活环境。

输入数据：`data/static_train.zip`；原始规划：`data/T8_FloorSense_Integrated_Sampling_v2.xlsx`；`point_labels.json` 是从规划提取的映射。prepare_data.py 安全解压到 `data/static/sessions/`；若文件已有改动不会覆盖。

## 评估口径

主结果：过滤非 fresh 后，Point 内相同特征指纹去重，271 条；5 折按 Point ID 分组并按楼层分层，seed=42。五种划分（42–46）全部在 metrics.csv。所有朝向保持同折，AP 词表仅由训练折建立。不含独立测试结果。

楼层与 Zone 是两个独立分类目标，`joint_accuracy` 指两个预测同时正确。`point_balanced_accuracy` 先计算每点扫描准确率、再等权平均，不是点位多数投票。

`results/models/*.joblib` 是全训练数据重拟合的模型，需要上述相同软件版本加载，只加载你信任的模型文件。模型得分是未校准投票比例；没有已知 AP 时不输出预测，少量 AP 的输出仍可能不可靠。

## Android 输入输出约定

输入为一次已检查 freshness/age 的扫描。BSSID 转小写；重复 BSSID 的 RSSI 取中位数；只接受 [-127,0) 内有限 RSSI；裁剪 [-100,-20]；未观测特征填 -100；严格按 `feature_config.json` 的 BSSID 顺序排列；忽略未知 BSSID。

Python `predict.py` 接收已聚合的 `{ "bssid": -60, ... }` JSON。输出 `floor`（2–6）、`zone`（West/Middle/East）和未校准 `*_vote_score`。

KNN JSON 含完整特征矩阵和标签，可由 D 实现欧氏距离、k=5、距离加权参考推理。零距离时只计零距离邻居；类别同票依照排序类别取先者。邻居距离并列处应与 scikit-learn 样例对照验证。JSON 不是已完成的 Android 模型；随机森林 joblib 也不能直接在原生 Android 中调用。两者均尚未经过手机端验证。

原始数据及预测明细含 BSSID，只在项目团队授权范围内分享。
