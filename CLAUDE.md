# AI 號誌績效比較分析工具 — 開發文件

## 專案用途

以 Streamlit 建立的互動式 Web App，比較 AI 號誌（事後）與定時時制（事前）之交通績效差異。
主要客戶場域：桃園三期（`perf_summary_3.csv`）、四期（`perf_summary_4.csv`）等。

---

## 模組架構

```
performance_comparison_app.py   主程式（Streamlit UI、狀態管理）
  ├── data_loader.py             CSV 讀取、欄位結構自動偵測（模組層快取）
  ├── comparison_logic.py        統計計算、改善率、特殊路口延滯
  ├── chart_builder.py           Plotly 圖表工廠（純函式，不持有狀態）
  └── export_builder.py          openpyxl Excel 匯出（含樣式）
```

### data_loader.py

- `load_performance_csv(path)` 載入後立即填入模組層快取 `_col_structure`。
  **所有其他存取函式（`get_system_columns`、`get_column_groups` 等）均依賴此快取，
  必須在呼叫前先執行 `load_performance_csv`。**
- 欄位自動偵測邏輯（`_detect_column_structure`）：
  - 旅行時間廊道：欄名含 `->` 或 `路徑`，或含 `-` 且非路口範圍格式
  - 系統總量欄：第一個路口聚合欄之前的所有欄
  - 路口聚合欄：以數字（可含 `-`）開頭後接空格，如 `2 青昇`、`4-5 台31/...`
  - 來向欄：單一大寫字母，可加 `.數字` 後綴，如 `A`、`B.1`

### comparison_logic.py

**核心常數：**
```python
LOWER_BETTER = {'總停等延滯', '平均停等延滯', '旅行時間'}  # 越小越好，其餘越大越好
DERIVED_METRICS = {'平均停等延滯': ('總停等延滯', '通過量')}
```

**計算原則：**
- 總停等延滯、通過量：取選定日期的欄位算術平均值
- 平均停等延滯：`mean(總停等延滯) / mean(通過量)`，不使用 CSV 的平均停等延滯列（避免平均的平均）
- 旅行時間：從有旅行時間資料的指標列取均值
- 改善率：
  - `LOWER_BETTER`：`(before - after) / before`
  - 其他：`(after - before) / before`
- `aggregate_periods`：全時段合計時，延滯與通過量採加總，旅行時間採各時段平均

**特殊路口延滯（`compute_special_raw` / `aggregate_special_raws` / `format_special_metrics`）：**
- 用途：桃園三期等場域需單獨列出特定路口子集的延滯績效
- `特殊平均停等延滯 = 特殊總停等延滯(加總) / 系統通過量`（分子為特選路口，分母為全系統）
- 全時段彙整：`aggregate_special_raws` 將各時段的 raw 數值直接相加，非平均

### export_builder.py

**工作表結構：** 分析說明 → 總表（`_build_summary_sheet`）→ 各時段（`_build_period_sheet`）→ 原始資料（選用）

**樣式：**
- `_apply_num_format`：目前所有數值欄一律使用 `'0'`（無逗號整數格式）
- `_write_pct`：改善率欄，有色底（綠/紅）+ 百分比格式
- 主要色彩：深藍標題（`4472C4`）、淺藍子標題（`9DC3E6`）、綠（改善）、紅（惡化）

---

## 資料格式

`performance_summary.csv`（UTF-8）必要欄位：

| 欄位 | 格式 | 說明 |
|------|------|------|
| 日期 | `YYYY/MM/DD` 或 `YYYY-MM-DD` | 自動正規化 |
| 時段 | `HH:MM~HH:MM` | 用於過濾分析區間 |
| 指標 | 文字 | `總停等延滯`、`通過量`、`平均停等延滯`（CSV 中存在但計算時不直接使用） |
| 後續欄 | 數值 | 由程式自動偵測（系統總量、路口聚合、來向、廊道） |

`ai_operation_log_N.csv`（UTF-8 BOM）：日期、時段、狀態、備註，用於在日曆中標記 AI 啟動狀況。

`date_selections_N.json`：儲存已確認的事前/事後日期分配，格式為 `{period: {before: [...], after: [...]}}`。

---

## Streamlit 狀態管理

分析結果以 `st.session_state['analysis_results']` 字典傳遞，結構：

```python
{
    'results':          dict[period, dict[metric, DataFrame]],
    'before_by_period': dict[period, list[Timestamp]],
    'after_by_period':  dict[period, list[Timestamp]],
    'periods':          list[str],
    'include_tt':       bool,
    'special_raws':     dict[period, dict],  # 空 dict 表示未啟用特殊路口功能
}
```

---

## 已知問題（Code Review 2026-07-23）

以下為程式碼審查發現的確認缺陷，修正前需注意：

### [HIGH] Excel 數值格式失去小數精度
**位置：** `export_builder.py:407`，`_apply_num_format`

`_apply_num_format` 一律輸出 `'0'`（整數無逗號），導致：
1. `平均停等延滯` 等小數指標（如 `78.9 秒/輛`）在 Excel 中顯示為整數（`79`），失去一位有效數字
2. 大型 `通過量` 數值（如 `3,203,607`）失去千分位逗號，顯示為 `3203607`

修正方向：恢復條件格式 — `LOWER_BETTER` 指標用 `'#,##0.0'`，其餘用 `'#,##0'`。

---

### [HIGH] `agg_special_raw` 真值判斷無法區分全 NaN sentinel dict
**位置：** `performance_comparison_app.py:1010`

```python
agg_special_raw = cl.aggregate_special_raws(special_raws, periods) if special_raws else None
agg_special     = cl.format_special_metrics(agg_special_raw) if agg_special_raw else None
```

`aggregate_special_raws` 在所有時段均無有效資料時回傳 `nan4`（4 個 key 全為 NaN 的 dict）。
`nan4` 是非空 dict，Python 真值為 `True`，因此 `if agg_special_raw` 無法攔截此情形，
`format_special_metrics` 仍被呼叫，最終在概覽表格中新增兩欄全為「—」的幻影欄位。

第 1021 行的逐時段判斷有相同問題：
```python
period_special = cl.format_special_metrics(special_raws[period]) if special_raws.get(period) else None
```

修正方向：
```python
def _has_valid_special(raw: dict) -> bool:
    return raw and not all(pd.isna(v) for v in raw.values())

agg_special = cl.format_special_metrics(agg_special_raw) if _has_valid_special(agg_special_raw) else None
period_special = cl.format_special_metrics(special_raws[period]) if _has_valid_special(special_raws.get(period)) else None
```

---

### [MEDIUM-HIGH] `sys_vol_col = ''` 導致特殊延滯功能靜默失敗
**位置：** `performance_comparison_app.py:910`，`comparison_logic.py:185`

當 `dl.get_system_columns()` 回傳空 list 時（資料尚未載入或場域欄位結構不符），
`sys_vol_col` 被設為 `''`。在 `compute_special_raw` 中，
`'' in DataFrame.columns` 恆為 `False`，故 `sys_vol_before` 與 `sys_vol_after` 均為 NaN。
接著 `aggregate_special_raws` 的 `any(pd.isna(...))` 篩掉全部時段，最終輸出全空。
整個流程沒有任何錯誤訊息或警告，使用者見到「分析完成！」但特殊欄位一片空白。

修正方向：在設定 `sys_vol_col` 後立即檢查，若為空則顯示 `st.warning`。

---

### [MEDIUM] Excel 總表頁腳在旅行時間未啟用時仍提及旅行時間
**位置：** `export_builder.py:220`，`_build_summary_sheet`

頁腳文字 `旅行時間為各時段平均` 為無條件寫入，不受 `include_travel_time` 旗標控制。
當 `include_travel_time=False` 時，工作表無旅行時間列，但頁腳仍聲稱有旅行時間平均值。

修正方向：依 `include_travel_time` 條件組合頁腳文字。

---

### [LOW-MEDIUM] `aggregate_special_raws` 遇到部分 NaN 就丟棄整個時段
**位置：** `comparison_logic.py:196`

若某時段的延滯資料有效但通過量資料缺失（感測器中斷），`any(pd.isna(...))` 會連延滯也一起丟棄，
導致全時段合計的特殊延滯被低估，且無任何提示。

修正方向：分開處理延滯與通過量的 NaN 判斷，或改為記錄警告後仍納入可用值。

---

## 已修正問題（2026-07-28）

### 日期分配表自動勾選行為全面重構
**位置：** `performance_comparison_app.py`，`_make_date_period_df` 及 `_apply_load_preset`

**原問題（已修正）：**
- 新增/移除分析時段時，觸發 `_make_date_period_df` 全部重建，覆蓋所有手動勾選（包含 preset 載入的狀態）
- 載入 preset 後補入的新日期，依 AI 操作紀錄自動勾選事前/事後，使用者無感知

**修正後行為：**
- `_make_date_period_df` 新增 `auto_classify=False` 參數（預設），事前/事後預設全空白
- **初次載入、切換場域、切換日期類型** → 全空白，不自動勾選
- **新增/移除分析時段** → 差異更新：只新增空白列或刪除對應列，既有勾選不受影響
- **切換「只顯示符合日期類型的日期」checkbox** → 補入或移除對應日期列，既有勾選不受影響
- **載入 preset 補入新日期** → 空白，並顯示提示訊息（含筆數），引導使用者主動用「⚡ 依操作紀錄自動填入」補全
- 唯一自動勾選入口：使用者主動點選「⚡ 依操作紀錄自動填入」按鈕

---

## 常見維護場景

### 新增場域
1. 在 `data/` 放入 `perf_summary_N.csv`、`ai_operation_log_N.csv`
2. 確認欄位命名符合自動偵測規則（系統欄在路口欄之前、廊道欄含 `->` 或 `路徑`）
3. 若有「特殊路口子集」需求，確認 `get_system_columns()` 能正確回傳系統總量欄

### 修改指標顯示格式
- UI 數值格式：`comparison_logic.py` 的 `_USE_INT_FORMAT` 與 `generate_analysis_text`
- Excel 數值格式：`export_builder.py` 的 `_apply_num_format`（目前固定為 `'0'`，見已知問題）
- 兩處格式定義應保持一致

### 新增衍生指標
在 `comparison_logic.py` 的 `DERIVED_METRICS` 字典中加入 `{指標名: (分子指標, 分母指標)}`，
`compute_comparison` 會自動計算。同時更新 `METRIC_UNITS` 與 `LOWER_BETTER`（如需要）。
