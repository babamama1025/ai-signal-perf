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
# 旅行時間廊道欄的識別規則：
#   四期格式：欄名含「路徑」或「->」
#   三期格式：欄名含破折號「-」，但不是路口編號範圍（如「4-5 台31/...」開頭的格式）
_TT_RE = re.compile(r'路徑|->|旅行時間')

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

    # 步驟 1：偵測旅行時間廊道欄
    # 四期格式：含「路徑」或「->」
    # 三期格式：含「-」（破折號），但排除以數字範圍開頭的路口欄，如「4-5 台31/...」
    def _is_tt(col: str) -> bool:
        s = str(col)
        return bool(_TT_RE.search(s)) or ('-' in s and not _INTERSECTION_RE.match(s))

    tt_cols = [c for c in data_cols if _is_tt(c)]
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


def get_approach_columns() -> list[str]:
    """回傳所有來向（全方向）欄名列表（路口聚合欄後的 A/B/… 欄）。"""
    approach_cols = []
    for cols in get_column_structure().get('groups', {}).values():
        approach_cols.extend(cols[1:])  # 跳過路口聚合欄本身
    return approach_cols


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


# ── 資料品質註記（計算專案 aisig publish 產生 data_quality_N.csv）───────────────
QUALITY_COLS = ['日期', '時段', '等級', '規則', '說明']
QUALITY_ERROR, QUALITY_DROP, QUALITY_WARN = '錯誤', '排除', '警告'   # 排除 = 外部壅塞等不代表號誌績效的時段
QUALITY_ICON = {QUALITY_ERROR: '⛔', QUALITY_DROP: '🚧', QUALITY_WARN: '⚠️'}


def load_quality(path: str | Path) -> pd.DataFrame:
    """讀取資料品質註記；檔案不存在時回傳空表（未發佈過註記的場域）。"""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=QUALITY_COLS)
    q = pd.read_csv(path, encoding='utf-8-sig')
    q['日期'] = pd.to_datetime(q['日期'])
    return q


def quality_notes(q: pd.DataFrame) -> dict[tuple[pd.Timestamp, str], dict]:
    """{(日期, 時段): {'error': 是否有錯誤或排除, 'level': 最嚴重等級, 'short': '⛔ 無VD資料', 'text': '錯誤：無VD資料（…）'}}。"""
    out = {}
    for (d, p), g in q.groupby(['日期', '時段']):
        levels = set(g['等級'])
        out[(pd.Timestamp(d), p)] = {
            'error': bool(levels & {QUALITY_ERROR, QUALITY_DROP}),
            'level': next((lv for lv in (QUALITY_ERROR, QUALITY_DROP) if lv in levels), QUALITY_WARN),
            'short': '、'.join(f"{QUALITY_ICON.get(lv, '')} {rule}" for lv, rule in zip(g['等級'], g['規則'])),
            'text':  '；'.join(f'{lv}：{rule}（{desc}）' for lv, rule, desc in zip(g['等級'], g['規則'], g['說明'])),
        }
    return out


QUALITY_EXCLUDE = ['不排除', '排除「錯誤」與「排除」', '全部排除（含「警告」）']


def is_excluded(note: dict | None, mode: str) -> bool:
    """mode 為 QUALITY_EXCLUDE 之一。"""
    if note is None or mode == QUALITY_EXCLUDE[0]:
        return False
    return note['error'] or mode == QUALITY_EXCLUDE[2]


def flagged_rows(notes: dict, dates_by_period: dict[str, tuple[list, list]]) -> list[dict]:
    """所選日期中有註記者：[{時段, 組別, 日期, 等級, 註記}]；dates_by_period：{時段: (事前日期, 事後日期)}。"""
    rows = []
    for p, (before, after) in dates_by_period.items():
        for label, dates in (('事前', before), ('事後', after)):
            for d in sorted(dates):
                n = notes.get((pd.Timestamp(d), p))
                if n:
                    rows.append({'時段': p, '組別': label, '日期': format_date(pd.Timestamp(d)),
                                 '等級': n['level'], '註記': n['text']})
    return rows


def flag_counts(rows: list[dict]) -> str:
    """flagged_rows 的各等級格數，如「⛔ 錯誤 1、🚧 排除 2、⚠️ 警告 3」（0 格的等級省略，警告除外）。"""
    n = {lv: sum(r['等級'] == lv for r in rows) for lv in QUALITY_ICON}
    return '、'.join(f'{QUALITY_ICON[lv]} {lv} {n[lv]}' for lv in QUALITY_ICON if n[lv] or lv == QUALITY_WARN)


# ── 降階統計（計算專案 aisig publish 產生 degradation_daily_N.csv）───────────────
DEGRADATION_COLS = ['日期', '星期', '降階次數', '小時制降階次數', '降階小時區間', '降階分鐘', '主要原因']


def load_degradation(path: str | Path) -> pd.DataFrame:
    """讀取每日降階統計；檔案不存在時回傳空表。"""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=DEGRADATION_COLS)
    d = pd.read_csv(path, encoding='utf-8-sig', keep_default_na=False)
    d['日期'] = pd.to_datetime(d['日期'])
    return d


def degradation_monthly(d: pd.DataFrame) -> pd.DataFrame:
    """依月份彙總：天數、有降階天數、兩種次數、降階時數與日平均。"""
    g = d.groupby(d['日期'].dt.strftime('%Y/%m'))
    out = pd.DataFrame({
        '天數': g.size(),
        '有降階天數': g['降階次數'].apply(lambda s: int((s > 0).sum())),
        '降階次數': g['降階次數'].sum(),
        '小時制降階次數': g['小時制降階次數'].sum(),
        '降階時數': (g['降階分鐘'].sum() / 60).round(1),
    })
    out['日均降階次數'] = (out['降階次數'] / out['天數']).round(2)
    out['日均小時制次數'] = (out['小時制降階次數'] / out['天數']).round(2)
    out['日均降階分鐘'] = (g['降階分鐘'].mean()).round(1)
    return out.rename_axis('月份').reset_index()
