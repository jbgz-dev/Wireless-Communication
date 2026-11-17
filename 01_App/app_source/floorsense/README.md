# BNBU-FloorSense 演示 App（开发说明，v0.4）

用户怎么装、怎么用、屏幕上每一项是什么意思，见 [使用指南.md](使用指南.md)。这里写给要改代码的人。

这个 App 和采集 App 在同一个 Gradle 工程里（模块 `:floorsense`，包名 `edu.bnbu.floorsense.app`）。两者的 Wi-Fi 扫描节奏和 fresh 判断相同，训练数据就是用这套规则采的。

## 构建

```bash
# 在 01_App/app_source/ 下
./gradlew :floorsense:testDebugUnitTest :floorsense:assembleDebug
```

Android Studio：打开 `FloorSenseCollector_data` 文件夹，运行配置选 **floorsense**，连手机点 Run ▶。要求 Android 9（API 28）及以上。

## 结构

```
src/main/assets/wifi_model.json.gz   C 的冻结版 V1（SHA-256 e018a4f7…），打包时 aapt 会解压成 wifi_model.json
core/          不依赖 Android，可以在电脑上单元测试
  FeatureConfig  扫描 → 特征向量（BSSID 小写、RSSI 裁剪到 [-100,-20]、重复取中位数、缺失 -100）
  ForestModel    随机森林推理；读 V1（schema_version 1）和旧的 floorsense-rf-1
  FloorModel     模型接口和 RealtimePolicy（已知 AP ≥ 15、楼层分数 ≥ 0.50）
  FloorTracker   C 的实时规则：通过门限才更新，否则保持上一次楼层、Zone 置空
  MotionDetector 跨层检测（电梯：加速度计；楼梯：陀螺仪平台转身）
  FloorFusion    2–6F 上的贝叶斯滤波，跨层时从移动前的楼层平移
  FusionEngine   把上面三者接起来，界面只和它打交道
  SessionReplay / FusionReplay  读取采集 App 的 session 并重放
WifiScanner    Wi-Fi 扫描
EvalRecorder   评估模式的记录和导出
MainActivity   界面；注册加速度计、gravity、陀螺仪（SENSOR_DELAY_GAME）
```

算法和阈值与 Python 原型 `validation_d2/dynamic/`（`detect.py`、`fuse.py`）相同，阈值只用动态 Train 定。

## 和参考实现的一致性测试

| 测试 | 对照什么 | 结果 |
|---|---|---|
| `V1ParityTest` | C 的 `parity_vectors.json`（分类输出）和 `realtime_policy_vectors.json`（waiting / held / ok） | 全部一致 |
| `D3ReplayTest` | 用 App 代码重放 D3 的 70 个静态 Test 点，和 C 的 `scan_events.csv` 逐条比较 | 729 次扫描 0 处不一致；楼层 69/70 |
| `FusionReplayTest` | 动态 session 的检测和融合，结果和 `fuse.py` 对照 | 49 个 session 中 48 个检测相同（差的 1 个是录制在到达后太早停止） |
| `FusionTest` | 合成的电梯、楼梯转身、走廊直走 | 通过 |
| `ForestParityTest`、`KnnParityTest` | D2 时的旧 RF、KNN 和 scikit-learn | 只留作对照，不进 APK |

D3 和动态的重放需要原始数据，数据不在仓库里，所以用环境变量指定，没设置时自动跳过：

```bash
FS_D3_EVENTS=<d3_results/2026-11-16_70pt/scan_events.csv> FS_D3_DIRS=<session目录>:<session目录> \
  ./gradlew :floorsense:testDebugUnitTest --tests '*D3Replay*' -i
FS_DYNAMIC_DIRS=<目录>:<目录> ./gradlew :floorsense:testDebugUnitTest --tests '*FusionReplay*' -i
# FS_GYRO=0：只用加速度计（旧规则），用来做 Advanced 3 的对比
```

## 换模型

C 重新训练或校准后，把新的 `wifi_model.json.gz` 放进 `src/main/assets/`，同时更新 `src/test/resources/v1_*vectors.json`，再跑一次单元测试。模型自带 `realtime_policy`，门限会跟着模型走，App 代码不用改。

## 已知限制

- 楼层分数没有校准，不是正确概率。
- 连续走两层以上楼梯时，一整段行走只算一层。
- 电梯起动时手在晃会漏检（起动脉冲被当成手的动作过滤掉）。
- 楼梯上下方向依赖 T8 楼梯的转向（上楼逆时针）；换一栋楼要重新确认。
