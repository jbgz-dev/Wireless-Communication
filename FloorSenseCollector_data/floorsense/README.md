# FloorSense：楼层识别 App（D，版本 0.1）

这是演示用的楼层识别 App，和采集 App 放在同一个 Gradle 工程里（模块 `:floorsense`）。两者的 Wi-Fi 扫描节奏和 fresh 判断相同，训练数据就是用这套规则采的。

## 安装

1. Android Studio 打开 `FloorSenseCollector` 文件夹，运行配置选 **floorsense**，连手机点 Run ▶。
2. 第一次打开时允许"精确位置"。
3. 手机设置与采集时一样：**关闭 WLAN 扫描节流**，打开定位和 Wi-Fi。App 顶部色条会提示这些设置是否正确。

要求 Android 9（API 28）及以上。

## 三个功能

### 实时识别
按"开始识别"。界面显示大字楼层、投票分数、区域（West/Middle/East）、已知 AP 数和上次新扫描是几秒前。

状态条的含义：

| 状态 | 什么时候出现 | 楼层怎么处理 |
|---|---|---|
| 正常 | 最近一次扫描有效 | 显示最近 5 个不同指纹的平均结果 |
| 信号不足 | 已知 AP 少于 5 个 | 保持上一次的楼层，不猜 |
| 扫描过期 | 超过 15 秒没有新扫描（例如 Wi-Fi 被关掉） | 保持上一次的楼层，恢复扫描后自动继续 |
| 正在跨层移动 | E 的跨层检测发出移动开始事件 | 冻结显示，到达后清空平滑，下一次扫描直接决定新楼层 |

和上一次指纹完全相同的扫描不重复计入平滑。系统返回的缓存结果直接忽略。

### 评估模式
在测试点上填测试点编号和真实楼层（Zone 可选），按"记录这个点"。App 记录接下来 5 次有效扫描，响一声表示完成，并以那时界面显示的楼层作为这个点的结果。下方实时显示累计准确率和混淆情况。

"导出评估数据"会打包一个 zip，里面有：

- `wifi.csv`：原始扫描，列和 Collector 的 `wifi.csv` 相同，C 以后换模型可以离线重新评估；
- `predictions.csv`：每次扫描的单次预测和界面显示结果；
- `points.csv`：每个测试点一行，用来出报告的准确率和混淆矩阵；
- `meta.json`：手机型号和模型版本。

### 回放模式
把 Collector 采的 session 文件夹推到手机上，App 用同一套逻辑重放，并计算准确率：

```bash
adb push <session文件夹> /sdcard/Android/data/edu.bnbu.floorsense.app/files/replay/
```

按"回放全部 session"会显示四列结果：纯 Wi-Fi 准确率；用 `events.csv` 的移动标记模拟跨层检测后的准确率；不平滑的单次扫描准确率；到达后多久显示正确楼层。移动途中（move_start 到 move_end）不计分。

电脑上也能跑同样的回放，不用手机：

```bash
FS_REPLAY_DIR=/path/to/dynamic_train ./gradlew :floorsense:testDebugUnitTest --tests '*ReplayReport*' -i
```

## 模型

`src/main/assets/knn_reference.json` 直接拷自 `floorsense_c/results/models/`（k=5，距离加权，欧氏距离，271 条训练指纹，1226 个 AP）。C 重新训练后，替换这个文件，再重新生成对照样例：

```bash
python floorsense/tools/make_fixtures.py --c-dir ../floorsense_c --static <static session 目录> \
    --out floorsense/src/test/resources/fixtures.json
./gradlew :floorsense:testDebugUnitTest
```

`KnnParityTest` 会逐条比较 Kotlin 和 scikit-learn 的指纹、已知 AP 数、预测类别和投票分数。不通过就说明特征处理或推理有差异，不能上线。

换成 ONNX 随机森林时，实现 `core/FloorModel.kt` 里的 `FloorModel` 接口即可，界面和平滑代码不用改。

## 代码结构

```
core/          不依赖 Android，可以在电脑上单元测试
  FeatureConfig  扫描 → 特征向量（与 floorsense_c/pipeline.py 规则一致）
  KnnModel       复现 scikit-learn KNN
  FloorTracker   平滑、信号不足、过期、跨层事件接口
  SessionReplay  读取 Collector 的 session 文件夹
  ReplayRunner   重放并统计准确率
WifiScanner    Wi-Fi 扫描（与 Collector 的规则相同）
EvalRecorder   评估模式的记录和导出
MainActivity   界面
```

## 给 E 的接口

`FloorTracker.onTransitionStart()` / `onTransitionEnd()`。E 的跨层检测发现开始移动时调用前者，判断到达后调用后者。目前只在回放模式里用 `events.csv` 的人工标记模拟，还没有接真实的传感器检测。

## 已知限制

- 投票分数没有校准，不是正确概率。
- 信号不足的阈值（已知 AP < 5）和平滑窗口（5 个指纹）是根据训练数据定的初值，需要在独立测试上再调。
- 真机测试、第二台手机测试都还没做。
