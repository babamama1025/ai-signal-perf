"""資料讀取與結構自動偵測模組

所有路口、來向、旅行時間廊道等結構資訊，
完全從 performance_summary.csv 的欄位標頭與資料樣態自動推導，
不含任何硬編碼的欄位名稱或位置。
"""
import re
import pandas as pd
from pathlib import Path

TW_WEEKDAY = ['一', '二', '三', '四', '五', '六', '日']

# 路口聚合欄的識別規則：以數字（可含 -）開頭後接空格，例如 "2 青昇"、"4-5 台31/領航南北路"
_INTERSECTION_RE = re.compile(r'^\d[\d\-]* ')
# 短碼來向欄的識別規則：單一大寫字母，可後綴 .數字，例如 A、B.1、D.3
_APPROACH_RE = re.compile(r'^[A-Z](\.\d+)?$')
# 旅行時間廊道欄的識別規則：名稱含「路徑」或「->」（箭頭路段格式）
_TT_RE = re.compile(r'路徑|->')

# 模組層級快取：load_performance_csv 載入後自動填入
_col_structure: dict | None = None


# ── 主要載入函式 ────────────────────────────────────────────────────────────
def load_performance_csv(csv_path: str | Path) -> pd.DataFrame:
    """載入 CSV，自動正規化日期格式並去除重複列。"""
    df = pd.read_csv(csv_path, encoding='utf-8')
    df['日期'] = df['日期'].astype(str).apply(_normalize_date)
    df = df.drop_duplicates(subset=['日期', '時段', '指標'], keep='last')
    for col in df.columns[3:]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.reset_index(drop=True)
    # 載入後立即建立欄位結構快取
    global _col_structure
    _col_structure = _detect_column_structure(df)
    return df


def _normalize_date(s: str) -> pd.Timestamp:
    return pd.to_datetime(s.strip().replace('/', '-'))


# ── 結構自動偵測（核心） ────────────────────────────────────────────────────
def _detect_column_structure(df: pd.DataFrame) -> dict:
    """
    從 CSV 欄位標頭與資料樣態，自動推導：
    - 旅行時間欄（欄名含「路徑」或「->」）
    - 系統總量欄（路口前段、不符合路口命名規則）
    - 路口群組（路口聚合欄 + 其後的來向欄）
    - 短碼來向欄的顯示名稱（2_A、6_A…）
    """
    data_cols = df.columns[3:].tolist()

    # 步驟 1：偵測旅行時間欄（欄名含「路徑」或「->」）
    tt_cols = [c for c in data_cols if _TT_RE.search(str(c))]
    non_tt_cols = [c for c in data_cols if c not in tt_cols]

    # 步驟 2：找系統總量欄（第一個路口聚合欄之前的所有欄）
    first_int_idx = next(
        (i for i, c in enumerate(non_tt_cols) if _INTERSECTION_RE.match(str(c))),
        len(non_tt_cols),
    )
    system_cols = non_tt_cols[:first_int_idx]

    # 步驟 3：建立路口群組
    # 每遇到一個路口聚合欄，就開啟新群組；其後的來向欄都歸屬此群組
    groups: dict[str, list[str]] = {}
    current_name: str | None = None
    for col in non_tt_cols[first_int_idx:]:
        if _INTERSECTION_RE.match(str(col)) and not _APPROACH_RE.match(str(col)):
            current_name = col
            groups[col] = [col]
        elif current_name is not None:
            groups[current_name].append(col)

    # 步驟 4：為短碼來向欄建立帶路口前綴的顯示名稱
    display_map: dict[str, str] = {}
    for int_col, cols in groups.items():
        m = re.match(r'^(\d[\d\-]*)', str(int_col))
        prefix = m.group(1) if m else str(int_col)
        for col in cols[1:]:          # 跳過路口聚合欄本身
            if _APPROACH_RE.match(str(col)):
                letter = str(col).split('.')[0]  # 去掉 .1 / .2 等後綴
                display_map[col] = f"{prefix}_{letter}"

    return {
        'system':      system_cols,
        'groups':      groups,          # {路口名稱: [路口欄, 來向欄…]}
        'travel_time': tt_cols,
        'display_map': display_map,
        'all_data':    non_tt_cols,     # 所有非旅行時間的量測欄
    }


# ── 公開存取函式 ────────────────────────────────────────────────────────────
def get_column_structure() -> dict:
    """回傳已快取的欄位結構（需先呼叫 load_performance_csv）。"""
    return _col_structure or {}


def get_system_columns() -> list[str]:
    return get_column_structure().get('system', [])


def get_travel_time_columns() -> list[str]:
    return get_column_structure().get('travel_time', [])


def get_column_groups() -> dict[str, list[str]]:
    """回傳依路口分組的欄名字典（含系統總量與旅行時間廊道）。"""
    s = get_column_structure()
    groups = {'系統總量': s.get('system', [])}
    for name, cols in s.get('groups', {}).items():
        groups[name] = cols
    tt = s.get('travel_time', [])
    if tt:
        groups['旅行時間廊道'] = tt
    return groups


def get_display_name(col: str) -> str:
    """回傳欄位的顯示名稱（例如 A.1 → 6_A）；無對應則回傳原名稱。"""
    return get_column_structure().get('display_map', {}).get(col, col)


def get_available_dates(df: pd.DataFrame) -> list[pd.Timestamp]:
    return sorted(df['日期'].unique())


def get_available_periods(df: pd.DataFrame) -> list[str]:
    periods = df['時段'].unique().tolist()
    return sorted(periods, key=lambda p: int(p.split('~')[0].replace(':', '')))


def get_metrics(df: pd.DataFrame) -> list[str]:
    """從資料取得指標名稱（依出現順序）。"""
    return df['指標'].unique().tolist()


def format_date(ts: pd.Timestamp) -> str:
    return f"{ts.strftime('%Y/%m/%d')} (週{TW_WEEKDAY[ts.weekday()]})"


# ── 選用：從 Excel 測試日工作表匯入日期分類 ──────────────────────────────────
def load_test_day_classification(xlsx_path: str | Path) -> dict[str, str]:
    """
    讀取「測試日」工作表，回傳 {date_str: 'AI'|'FIX'}。
    此為選用功能；若 xlsx 不存在，呼叫端應回退至全手動分類。
    """
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb['測試日']
    result = {}
    for row in ws.iter_rows(values_only=True, min_row=3):
        if not row or row[1] is None:
            continue
        date_val = row[1]
        date_str = (date_val.strftime('%Y-%m-%d')
                    if hasattr(date_val, 'strftime')
                    else str(date_val)[:10])
        # cols 3-10: 前1, 前2, 前3, A1, A2, A3, A4, A5
        flags = [row[i] for i in range(3, 11) if i < len(row)]
        result[date_str] = 'AI' if any(str(f) == 'True' for f in flags if f is not None) else 'FIX'
    wb.close()
    return result
