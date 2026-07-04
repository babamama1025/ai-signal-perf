"""資料讀取與結構定義模組"""
import pandas as pd
import openpyxl
from pathlib import Path

# 各路口欄位分組定義（0-based 索引，對應 df.columns）
INTERSECTION_GROUPS = [
    ('2 青昇',          (7,  12)),
    ('6 青心',          (12, 17)),
    ('11 高鐵北/青埔',  (17, 21)),
    ('12 高鐵南/青埔',  (21, 25)),
    ('16 公園',         (25, 28)),
    ('18 民權',         (28, 31)),
    ('19 高鐵南/領航南',(31, 33)),
    ('20 高鐵南/中豐北',(33, 38)),
    ('1 台31/中正東路', (38, 43)),
    ('2 台31/599巷',    (43, 46)),
    ('3 台31/五福路',   (46, 49)),
    ('4-5 台31/領航南北路', (49, 54)),
]

# 方向欄位的顯示名稱（pandas 去重後的名稱 → 人可讀標籤）
APPROACH_DISPLAY_NAMES = {
    'A': '2_A', 'B': '2_B', 'C': '2_C', 'D': '2_D',
    'A.1': '6_A', 'B.1': '6_B', 'C.1': '6_C', 'D.1': '6_D',
    'A.2': '11_A', 'C.2': '11_C', 'D.2': '11_D',
    'A.3': '12_A', 'B.2': '12_B', 'C.3': '12_C',
    'A.4': '16_A', 'C.4': '16_C',
    'A.5': '18_A', 'C.5': '18_C',
    'B.3': '19_B',
    'A.6': '20_A', 'B.4': '20_B', 'C.6': '20_C', 'D.3': '20_D',
}

TW_WEEKDAY = ['一', '二', '三', '四', '五', '六', '日']


def load_performance_csv(csv_path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, encoding='utf-8')
    df['日期'] = df['日期'].astype(str).apply(_normalize_date)
    df = df.drop_duplicates(subset=['日期', '時段', '指標'], keep='last')
    for col in df.columns[3:]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.reset_index(drop=True)


def _normalize_date(s: str) -> pd.Timestamp:
    return pd.to_datetime(s.strip().replace('/', '-'))


def load_test_day_classification(xlsx_path: str | Path) -> dict[str, str]:
    """讀取「測試日」工作表，回傳 {date_str: 'AI'|'FIX'}。"""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb['測試日']
    result = {}
    for row in ws.iter_rows(values_only=True, min_row=3):
        if not row or row[1] is None:
            continue
        date_val = row[1]
        if hasattr(date_val, 'strftime'):
            date_str = date_val.strftime('%Y-%m-%d')
        else:
            date_str = str(date_val)[:10]
        # cols 3-10: 前1, 前2, 前3, A1, A2, A3, A4, A5
        flags = [row[i] for i in range(3, 11) if i < len(row)]
        any_ai = any(str(f) == 'True' for f in flags if f is not None)
        result[date_str] = 'AI' if any_ai else 'FIX'
    wb.close()
    return result


def get_available_dates(df: pd.DataFrame) -> list[pd.Timestamp]:
    return sorted(df['日期'].unique())


def get_available_periods(df: pd.DataFrame) -> list[str]:
    periods = df['時段'].unique().tolist()
    return sorted(periods, key=lambda p: int(p.split('~')[0].replace(':', '')))


def get_system_columns(df: pd.DataFrame) -> list[str]:
    return df.columns[3:7].tolist()


def get_travel_time_columns(df: pd.DataFrame) -> list[str]:
    return df.columns[54:75].tolist()


def get_column_groups(df: pd.DataFrame) -> dict[str, list[str]]:
    """回傳依路口分組的欄名字典。"""
    cols = df.columns.tolist()
    groups = {'系統總量': cols[3:7]}
    for name, (start, end) in INTERSECTION_GROUPS:
        groups[name] = cols[start:end]
    groups['旅行時間廊道'] = cols[54:75]
    return groups


def get_display_name(col: str) -> str:
    """回傳欄位的人可讀顯示名稱。"""
    return APPROACH_DISPLAY_NAMES.get(col, col)


def format_date(ts: pd.Timestamp) -> str:
    return f"{ts.strftime('%Y/%m/%d')} (週{TW_WEEKDAY[ts.weekday()]})"
