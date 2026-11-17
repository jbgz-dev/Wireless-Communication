# BNBU-FloorSense (COMP4213)

Wi-Fi floor and zone recognition for T8 floors 2–6 on Android, with stairs/elevator detection (accelerometer + gyroscope).

Code and documents only. Raw Wi-Fi data and model files contain BSSIDs and are kept inside the team, not in this public repo. Use `01_App/app_source/tools/anonymize_dataset.py` before sharing any data.

| Folder | Content |
|---|---|
| `01_App/app_source/app` | Data collection app 1.1 (`edu.bnbu.floorsense.collector`) |
| `01_App/app_source/floorsense` | Demo app BNBU-FloorSense 0.4 (`edu.bnbu.floorsense.app`): frozen V1 model, cross-floor detection and fusion |
| `01_App/app_source/tools` | Data tools, including the anonymization script |
| `03_C_模型与测试` | Role C: V1 model pipeline, D3 70-point static test, Advanced 5 calibration (code and reports) |
| `04_验证与融合` | D2 validation, dynamic detection/fusion prototype, 11-17 dynamic blind test results |
| `05_文档` | Project status and data collection guides |
| `legacy/FloorSenseCollector_v1.0` | Old collector 1.0, backup only |

Not included (get from the team drive): `floorsense/src/main/assets/wifi_model.json.gz`, everything in `floorsense/src/test/resources`, C's `data/`, `results/models`, all session folders and APKs.
