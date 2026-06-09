# 邊緣端姿態辨識跌倒監測警報系統

> Edge-AI Pose-based Fall Detection & Alert System
> 在 MCU 上以 YOLOv8-Pose 萃取人體關節點，於硬體層面截斷影像外流的隱私導向跌倒偵測

| 項目 | 內容 |
|------|------|
| 學生 | 談宇容　M11407W14 |
| 課程 | 邊緣人工智慧實務（Edge AI） |
| 硬體 | Seeed Grove Vision AI Module V2（Himax WiseEye2 HX6538）+ AMD Ryzen 5 7535HS 筆電 |
| Repo | <https://github.com/sylvia8813/fall_detection> |
| Demo | <https://youtu.be/Ep11dypr_o8> |

---

## 摘要

本專題實作一套**邊緣端（on-device）跌倒監測警報系統**。核心概念是：在 Seeed Grove Vision AI Module V2（搭載 Himax WiseEye2 MCU + Ethos-U55 NPU）上執行 **YOLOv8n-Pose** 推論，自 CSI 相機影像直接萃取 **17 個 COCO 人體關節點**，再透過 UART 以 JSON 格式輸出座標值，由 Host 端的規則式狀態機進行跌倒判定並即時警報。

最大特色在於**隱私設計**：在板端（stock）模式下，原始影像完全停留在板內、不外傳，從硬體層面截斷影像外流。系統在公開資料集 URFD 上達到 **F1 = 0.844（P 0.79 / R 0.90）**，自建側躺資料集 4 段全數偵測、零誤報、平均延遲 1.44 秒。針對「臉朝下／向前跌」此一純模型瓶頸，另建立 fine-tune pipeline，將臉朝下偵測率自 ~50% 提升至 ~100%（驗證集 Box mAP50 = 0.989）。

---

## 一、專題發想與目標

### 待解決問題
- **雲端推論**存在網路延遲與頻寬限制。
- 室內 **IP 攝影機**將原始影像傳輸到雲端／主機，有極高的**隱私外洩風險**。

### 本專題目標
1. 利用 Seeed Grove Vision AI Module V2，在 **MCU 上直接做 YOLOv8-Pose 推論**。
2. 自 CSI 相機影像萃取 **17 個人體關節點座標**。
3. 透過 **UART 輸出 JSON 格式座標值**，進行跌倒邏輯判定。
4. **從硬體層面截斷影像外流**，達成「架構上即不外洩」的隱私設計。

---

## 二、系統架構

```
┌──────────────────────────────────────────────────────────────┐
│        Grove Vision AI Module V2 (Himax WiseEye2 HX6538)       │
│                                                                │
│  OV5647 CSI ──▶ YOLOv8n-Pose ──▶ 17 COCO keypoints            │
│   相機影像        (TFLM / NPU)        (影像停留板內，不外傳)     │
│                                          │                     │
│                                          ▼                     │
│                            UART (JSON, 921600 bps, CH343)      │
└──────────────────────────────────────────┬────────────────────┘
                                            │  USB-C
                                            ▼
┌──────────────────────────────────────────────────────────────┐
│             Host PC（AMD Ryzen 5 7535HS / Win11）              │
│                                                                │
│  解析 JSON ──▶ 取 8 個語意關鍵點 ──▶ 規則式狀態機 (v1.5)       │
│                                         │                      │
│                                         ▼                      │
│                              跌倒判定 → 終端即時警報            │
└──────────────────────────────────────────────────────────────┘
```

板端僅輸出座標（隱私安全）；Host 端負責輕量的規則判定與警報。

---

## 三、板端部署遇到的問題

本專題採**原始碼編譯 + 自行燒錄**（非 no-code 流程），部署過程中遇到並解決下列問題：

- **Datapath 看門狗死鎖**：`WDT3 timeout → 相機被強制關閉 → 無限重啟`。
- **解法**：
  1. 不強制關閉相機；
  2. 自動 retrigger 重抓影像；
  3. 修正 JPEG / DMA 路徑參數。

**板端效能**：推論約 **121 ms／frame（約 8 FPS）**、功耗 **≈ 0.35 W（估計值，未實測）**。

---

## 四、YOLOv8 Pose 與跌倒判定邏輯

### 4.1 關鍵點選取（17 取 8）

YOLOv8n-Pose 輸出 17 個 COCO keypoints，本系統僅選取**語意顯著的 8 個**，以降低 Host 端運算負擔：

| COCO ID | 關節點 | 判定用途 |
|---------|--------|----------|
| #0 | 鼻子 | 頭部 y 座標 |
| #5 / #6 | 左 / 右肩 | 肩膀高度、肩髖比 |
| #11 / #12 | 左 / 右髖 | 重心 y 座標、速度 |
| #15 / #16 | 左 / 右踝 | 倒地確認 |

### 4.2 v1.2：髖部下降 + 肩髖比

初版規則依賴髖部下降與肩髖比，需同時滿足三個條件：
1. 髖部 y 座標超過畫面高度的 **80%**（代表重心顯著下降）；
2. 肩到髖的高度比 **< 0.5**（代表身體趨近水平）；
3. 上述兩條件須**持續 20 frames（約 2.5 秒）**才觸發警報。

時間窗口用於抑制誤報（避免蹲下撿東西被誤判）。此設計參考 Nguyen 等人（2025）論文。

### 4.3 v1.5：規則式狀態機（State Machine, rule-based）

改良為狀態機架構，採用**四種主要判斷方式**：

| 判斷依據 | 條件 | 說明 |
|----------|------|------|
| **Bbox 長寬比**（Aspect Ratio） | H/W < 1.0 | 越低代表人躺得越平 |
| **軀幹角度** | 相對垂直軸 > 55° | 0° = 站立，90° = 完全水平 |
| **臀部下降速度**（hip_vy） | > 200 像素/秒 | 偵測快速向下的運動 |
| **腿部角度** | — | 判斷「彎腰」還是「真的躺下」，避免彎腰誤判 |

並加入 **leg-aware veto** 修正側躺漏報。整體策略**以 Recall 優先**——「漏報一次跌倒」的代價遠大於「誤報一次」。

### 4.4 關鍵設計參數

| 設定項 | 值 | 目的 |
|--------|-----|------|
| `fusion_mode` | `"or"` | 比例或角度任一達標即判定為水平 |
| `ground_grace_second` | 0.6 秒 | 容許關鍵點抖動（跌倒過程中經常發生） |
| `ever_upright` | `True` | 必須曾經站立過，防止靜止物體誤判 |
| `torso_upright_veto` | 30° | 若軀幹清楚站立，不能單靠比例判定 |
| `cooldown` | 5.0 秒 | 觸發警報後的冷卻時間，避免同一次跌倒重複警報 |

> 補充：另有「地面持續累積器」（`sustained_ground_sec`）路徑，但最終版**關閉**（會增加誤報）；低幀率主要依賴下方 4.5 的 `skip_to_grounded`。

### 4.5 關鍵邏輯細節

- **為什麼需要 grace window（0.6 秒寬容）？**
  跌倒途中關鍵點經常抖動／消失，單一幀可能被錯判為站立；grace 防止因抖動而中斷跌倒檢測。
- **為什麼需要「最近站立過」檢查？**
  防止程式啟動時就把原本躺在地上的人誤判為跌倒。
- **低幀率問題（< 5 fps）？**
  低 fps 下速度判定失效，主要改靠 `skip_to_grounded`（已水平 + 近期站立過）+ grace 容忍抖動。

---

## 五、資料集

| 來源 | 內容 |
|------|------|
| **公開（baseline）** | URFD：30 段跌倒 + 40 段日常，共 70 段 |
| **自建** | Grove 實機錄製：側躺、向前跌、彎腰、坐椅… |

**關鍵方法**：URFD 採用**同源模型離線抽取座標**，避免 train–deploy 的分布落差（distribution shift）。

---

## 六、實驗結果

### 公開資料集 URFD（n = 70；30 跌倒 + 40 日常）
- **Precision 0.79 / Recall 0.90 / F1 0.844**（`eval_v15.txt`）
- 混淆矩陣：**TP / FP / FN = 27 / 7 / 3**
- 日常活動（URFD ADL，40 段）：**33/40 無誤報（TN）**；其餘 7 段有誤報（clip-level 誤報率約 17.5%）

### 自建側躺資料集
- **4 段全數偵測、零誤報**（`side_overall.txt`）
- **平均延遲 1.44 秒**

### 自建往前跌（live，PC 推論模式）
- **3 / 4 命中（約 75%）**——極端俯角下臉朝下仍可能漏（此為實機 live 結果，與下方第七節的「離線」偵測率區分）

> 在「pose 模型能偵測到」的跌倒類型上，規則式判定表現優異、誤報極低；真正的瓶頸出現在臉朝下／向前跌（見第七節）。

---

## 七、Fine-Tune（解決臉朝下偵測失效）

### 問題
**向前跌／臉朝下**時，板端會連續 **5–6 秒 detection = 0**。

### 原因分析
COCO 預訓練模型高度依賴臉部特徵，加上 **nano + INT8 量化 + 高俯角**，導致信心分數 < 偵測門檻 0.25——這屬於**模型層級的問題**，非規則可解。

### Fine-tuning 流程（自製 pipeline）
1. 抽 frame → 自動標註 → 人工修正了 **30 張臉朝下畫面**（每畫面 17 關節點，手肘／手腕可稍微忽略）。
2. **防遺忘**：混入側躺／站立的乾淨標註 + **凍結 backbone**。
3. **離線／PC 驗證**：臉朝下偵測率 **~50% → ~100%**（離線、訓練來源幀上的比較），側躺未退步。
4. 驗證集 **Box mAP50 = 0.989**。

> 註：上述 ~100% 為**離線**驗證；實機 **live 往前跌為 3/4（約 75%）**（見第六節），兩者請分清楚。

---

## 八、INT8 量化與 Ethos-U55 部署

### 工具鏈
pose 模型需以 **DeGirum 多輸出匯出**（原生匯出 vela 編譯不過）：

```
DeGirum 匯出 → onnx2tf INT8 → vela 3.9 (ethos-u55-64)
```

### Vela 成果
- **100% 算子跑在 NPU**
- 推論 **126.7 ms（約 7.9 fps）**

### 整合層問題：輸出張量順序不符
fine-tune 模型燒進板子後**關節點解析失敗**。診斷後發現：模型與 stock 的**輸出形狀完全相同**（7 個輸出：3 box + 3 conf + 1 keypoints `[1,1344,51]`），但**輸出順序不同**——

| keypoints `[1344,51]` 在輸出清單的位置 | |
|------|------|
| **stock（韌體預期）** | list position **3** |
| **fine-tune 原始匯出** | list position **6** |

韌體 `cvapp_yolov8_pose.cpp` 以**固定索引**讀取各輸出張量，順序一錯就讀到錯誤的張量 → 解析失敗。**模型本身正確，問題在輸出順序。**

### 修正：輸出重排對齊 stock（不需重新量化）
將 fine-tune 模型的 7 個輸出**重新排序**對齊 stock（逐一比對形狀）後重新 vela 編譯，驗證輸出順序逐項相符（keypoints 回到 position 3）：

```
STOCK     : [(256,64),(1024,64),(64,1),(1344,51),(1024,1),(64,64),(256,1)]  kpts@3
REORDERED : [(256,64),(1024,64),(64,1),(1344,51),(1024,1),(64,64),(256,1)]  kpts@3 ✓
```

產出 `yolov8n_pose_FINETUNED_reordered_0x3BB000.tflite`（100% NPU、7.9 fps）。
**現況**：輸出順序已驗證對齊，理論上韌體可正確解析；惟**實機 live 驗證尚待補完**（最後測試時板上跑的仍為 stock 模型）。

### 兩種運行模式對照

| 模式 | 推論處 | 隱私 | 臉朝下 |
|------|--------|------|--------|
| **板端模式（stock）** | MCU | 影像不外傳 | 看不到 |
| **PC 推論模式（fine-tune）** | 板 = 相機 + PC 推論 | 影像傳 PC | 抓得到 |

> 這呈現了一個**隱私 ↔ 偵測能力的權衡**：stock 模式隱私最佳但偵測不到臉朝下；PC 推論模式能偵測臉朝下，但影像需傳至 PC。輸出重排修正完成 live 驗證後，即可讓 fine-tune 模型回到板端、同時兼得隱私與臉朝下偵測。

---

## 九、Demo

| 情境 | 結果 |
|------|------|
| 往左跌 | 終端即時跳出警告 ✅ |
| 往右跌 | 偵測成功 ✅ |
| 往前跌（PC 推論模式） | 偵測成功 ✅ |
| 往前連續跌四次（PC 推論模式） | 3/4 命中（僅第二次未偵測到） |

完整影片：<https://youtu.be/Ep11dypr_o8>

另含工具展示：手動標註關節點座標（16 倍速）、手動標註跌倒開始／結束期間（跌倒期間約 2 秒，標記方式參考 URFD）。

---

## 十、結論與未來工作

### 結論
本專題完成一套**隱私導向的邊緣端跌倒監測系統**：在 MCU 上以 YOLOv8-Pose 萃取人體關節點、UART 僅輸出座標、Host 端以規則式狀態機判定跌倒。系統在 URFD 達 **F1 = 0.844（R 0.90）**、自建側躺零誤報，並透過 fine-tune 將臉朝下偵測率（離線）提升至接近 100%（mAP50 = 0.989），完整驗證了在 **~0.35 W（估計）、~8 FPS** 的低功耗平台上做即時跌倒偵測的可行性。最具價值的工程心得是：edge AI 真正的難點往往不在「模型準不準」，而在**底層整合**——從 datapath 看門狗、INT8 量化、到輸出張量順序對齊，每一關都要打通。

### 未來工作
1. **完成輸出重排模型的實機 live 驗證**：重排後的 fine-tune 模型輸出順序已對齊 stock，待燒上板實測確認臉朝下可正確解析，即可讓 fine-tune 模型回到板端、兼顧隱私與臉朝下偵測。
2. **擴充自建資料集**：每類跌倒姿態各 5–10 段，使 P/R/F1 統計更具可信度。
3. **時序模型**：導入多幀時序資訊，進一步區分「跌倒」與「躺下休息／坐地」等相似姿態。
4. **事件通報**：結合 Wi-Fi / BLE，於偵測到跌倒時主動推播警示。

---

## 十一、環境建置與重現步驟（Reproduction Guide）

> 本章說明如何在**另一台電腦**從零重現本專案。分為「板端韌體」與「PC 端」兩部分。
> ⚠️ **誠實聲明**：PC 端的跌倒判定、即時預覽、評估、標註與 fine-tune 相關程式（`fall_detector.py`、`live_preview*.py`、`evaluate_logs.py`、`label_tool.py`、`best.pt`、fine-tune pipeline）因當初被 `.gitignore` 排除或未提交，**並未納入本 repo**。本章除了給出可直接執行的板端流程外，也完整記錄 PC 端的**重建依據**（參數、recipe），以利日後重寫。

### 11.0 環境需求總覽

| 類別 | 需求 |
|------|------|
| OS | Windows 10/11（板端建置）；fine-tune 轉換建議 **WSL2 / Linux** |
| 編譯器 | Arm GNU Toolchain **13.2.rel1**（arm-none-eabi）、`make`（xpack windows-build-tools） |
| Python | 3.10+（xmodem 燒錄、PC 端腳本） |
| 硬體 | Seeed Grove Vision AI Module V2（Himax WiseEye2）、USB-C 線 |
| 瀏覽器 | Microsoft Edge（Web Serial API，看即時結果用） |

### 11.1 取得專案（含子模組）

```bash
git clone --recursive https://github.com/sylvia8813/fall_detection.git
cd fall_detection
```

> `--recursive` 是必要的：`EPII_CM55M_APP_S/library/cmsis_cv/CMSIS-CV` 是 git submodule。

### 11.2 板端韌體：建置環境（Windows）

1. 安裝 `make`（參考 [xpack windows-build-tools](https://github.com/xpack-dev-tools/windows-build-tools-xpack/releases)）。
2. 下載並解壓 [Arm GNU Toolchain 13.2.rel1（mingw-w64, arm-none-eabi）](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads)，將其 `bin/` 加入系統 PATH。
3. 確認 `EPII_CM55M_APP_S/makefile` 中：`APP_TYPE = tflm_yolov8_pose`。

### 11.3 編譯韌體

```bash
cd EPII_CM55M_APP_S
make clean
make
# 產出：obj_epii_evb_icv30_bdv10/gnu_epii_evb_WLCSP65/EPII_CM55M_gnu_epii_evb_WLCSP65_s.elf
```

### 11.4 產生韌體 image

```bash
cd ../we2_image_gen_local
cp ../EPII_CM55M_APP_S/obj_epii_evb_icv30_bdv10/gnu_epii_evb_WLCSP65/EPII_CM55M_gnu_epii_evb_WLCSP65_s.elf input_case1_secboot/
we2_local_image_gen project_case1_blp_wlcsp.json
# 產出：output_case1_sec_wlcsp/output.img
```

> 注意：repo 內附的 `we2_local_image_gen` 為 Linux/macOS 執行檔；Windows 需改用 [Himax 官方 repo](https://github.com/HimaxWiseEyePlus/Seeed_Grove_Vision_AI_Module_V2) 的 Windows 版本。

### 11.5 燒錄韌體 + 模型（XMODEM）

```bash
pip install -r xmodem/requirements.txt
# 先關閉 Tera Term / 任何序列埠終端機，釋放 COM port

python xmodem/xmodem_send.py --port=COM10 --baudrate=921600 --protocol=xmodem \
  --file=we2_image_gen_local/output_case1_sec_wlcsp/output.img \
  --model="model_zoo/tflm_yolov8_pose/yolov8n_pose_256_vela_3_9_0x3BB000.tflite 0x3BB000 0x00000"
# 依指示按板子 RST 鍵
```

- **模型 flash 位址**：`0x3BB000`（對應韌體 `common_config.h` 的 `YOLOV8_POSE_FLASH_ADDR`）。
- 只更新模型（韌體已燒過）可省略 `--file`。

### 11.6 觀看即時結果（板端 / 隱私模式）

- **方法 A — Himax AI Web Toolkit**：用 **Microsoft Edge** 開啟 toolkit → 右上選 `Grove Vision AI(V2)` → `Connect` → 選 COM port，即可看到骨架疊圖。
- **方法 B — 解析 log 畫骨架**：用 Tera Term 錄下 UART log，再跑本 repo 內的：
  ```bash
  python log_visualizer/visualize_keypoints.py <teraterm.log> --out output
  ```

### 11.7 PC 端跌倒判定（需自行重建，重建依據如下）

本 repo **不含** PC 端規則引擎，但完整參數已記錄於本報告，可據以重寫 `fall_detector.py`：

- **判定訊號與閾值** → 見 §4.3：`ratio < 1.0`、`torso > 55°`、`hip_vy > 200 px/s`、`hold ≥ 0.5s`。
- **狀態機** → `IDLE → DROPPING → GROUNDED → FALL → COOLDOWN`。
- **關鍵參數** → 見 §4.4：`fusion_mode="or"`、`ground_grace_second=0.6`、`ever_upright=True`、`torso_upright_veto=30°`、`cooldown=5.0s`、`sustained_ground_sec=0`（關閉）。
- **低幀率邏輯** → 見 §4.5：`skip_to_grounded`（已水平 + 近期站立過）+ grace。
- **評估方法**：判定視窗 `[fall_start − 1.0, fall_end + tolerance]`，URFD 評估須用 `--fps 10`（log 由 30fps 以 stride=3 抽出）。

### 11.8 Fine-tune 模型轉換與上板（vela，進階）

重現「臉朝下可偵測」的微調模型，流程記錄於 §七、§八：

1. **環境**：用 **WSL2 / Linux**（`onnx2tf` 在原生 Windows 會卡在 `onnxsim` 需 MSVC 編譯）。建兩個獨立 venv：一個裝 TensorFlow 做 INT8、一個裝 `ethos-u-vela==3.9.0` 做編譯（避開 `flatbuffers` 版本衝突）。
2. **匯出**：pose **必須**用 DeGirum fork 的 `dg_export_int8_output.py --img=256`（原生 ultralytics 匯出 vela 會 graph health AssertionError）。
3. **vela 編譯**：
   ```bash
   vela --accelerator-config ethos-u55-64 --config himax_vela.ini \
        --system-config My_Sys_Cfg --memory-mode My_Mem_Mode_Parent \
        <int8>.tflite
   ```
   （`himax_vela.ini` 取自 Himax `YOLOv8_on_WE2` repo。）
4. **輸出順序對齊**（關鍵，見 §8）：將 7 個輸出重排，使 `keypoints[1,1344,51]` 落在輸出清單 **position 3**（與 stock 一致），否則韌體解析失敗。
5. **燒錄**：以 §11.5 的 xmodem 指令燒到 `0x3BB000`。

---

## 十二、參考資料

1. Nguyen et al. (2025) — 跌倒判定邏輯（髖部下降 + 肩髖比 + 時間窗口）參考來源
2. URFD — UR Fall Detection Dataset（baseline 公開資料集）
3. Seeed Grove Vision AI Module V2 — <https://wiki.seeedstudio.com/grove_vision_ai_v2/>
4. Himax WiseEyePlus 官方 Repo — <https://github.com/HimaxWiseEyePlus/Seeed_Grove_Vision_AI_Module_V2>
5. Ultralytics YOLOv8 — <https://github.com/ultralytics/ultralytics>
6. DeGirum / onnx2tf / Vela（Arm Ethos-U Vela compiler）— INT8 量化與 NPU 部署工具鏈
