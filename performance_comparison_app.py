"""AI 號誌事前後分析系統 (Streamlit 應用程式)"""
import io
import json
import base64
import hashlib
import requests
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime

import data_loader as dl
import comparison_logic as cl
import chart_builder as cb
import export_builder as eb
import benefit_logic as bl

# ── 場域選項 ────────────────────────────────────────────────────────────────
SITE_OPTIONS = {
    '桃園四期(大湳)': 'perf_summary_4.csv',
    '桃園三期(高鐵)': 'perf_summary_3.csv',
}

# 桃園三期額外需要納入概覽與總表的系統層級欄位
SITE3_EXTRA_ENTITIES = ['高鐵周邊', 'A19周邊', '前期範圍']

# ── 場域預設時段（平日 / 假日）─────────────────────────────────────────────
# 修改此區塊可調整各場域的預設勾選時段
SITE_DEFAULTS = {
    '桃園四期(大湳)': {
        'periods_weekday': ['07:00~09:00', '12:00~14:00', '17:00~19:00'],
        'periods_weekend': ['10:00~12:00', '11:00~12:00', '17:30~18:30', '17:00~19:00'],
    },
    '桃園三期(高鐵)': {
        'periods_weekday': ['07:00~09:00', '14:00~16:00', '16:00~19:00'],
        'periods_weekend': ['10:00~12:00', '16:00~19:00'],
    },
}

def _esc_md(text: str) -> str:
    """跳脫字串中的 '~'，避免多個時段字串（如 07:00~09:00）併入同一行文字時，
    奇數個 '~' 被 Markdown 誤判成刪除線的起訖點。"""
    return text.replace('~', '\\~')


# ── 路徑設定 ────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / 'data'

# 各場域獨立的已儲存日期分配檔
SELECTION_PATHS = {
    '桃園四期(大湳)': 'date_selections_4.json',
    '桃園三期(高鐵)': 'date_selections_3.json',
}

# 各場域的資料品質註記（計算專案 aisig publish 產生；錯誤 = 無 VD 資料或完整度過低，排除 = 外部壅塞，警告 = 完整度偏低或通過量異常）
QUALITY_PATHS = {
    '桃園四期(大湳)': 'data_quality_4.csv',
    '桃園三期(高鐵)': 'data_quality_3.csv',
}

# 各場域的每日降階統計（計算專案 aisig publish 產生）
DEGRADATION_PATHS = {
    '桃園四期(大湳)': 'degradation_daily_4.csv',
    '桃園三期(高鐵)': 'degradation_daily_3.csv',
}

# ── 旅行時間指標說明（popover 用）────────────────────────────────────────────
_TT_METRIC_INFO_MD = """
#### 一、平均旅行時間

四條路徑在分析時段的平均旅行時間做算術平均：

$$\\text{平均旅行時間} = \\frac{1}{4}\\sum_{p=1}^{4} \\overline{\\text{TT}}_p$$

> 四項指標只在四條路徑皆有值時產出；任一路徑缺值，四項指標皆為缺值。
> 網站對每欄各自取日期平均（略過缺值），故表上路徑欄可能比指標欄多納入「只有部分路徑有值」的日期，4 條路徑的平均不一定等於平均旅行時間。

---

#### 二、加權平均旅行時間

以各路徑代表路段的**實際交通量**（VD）作為路徑重要性的衡量依據：

| 路徑 | 代表路段 | 方向 | 車道 | 計算內容 |
|------|----------|------|------|----------|
| 路徑1 | S406400 | 東向（dir 6） | 全部 | 時段總大車＋小車 |
| 路徑2 | S406400 | 南向（dir 2） | 3、4（直行） | 時段總大車＋小車 |
| 路徑3 | S406400 | 西向（dir 4） | 全部 | 時段總大車＋小車 |
| 路徑4 | S406400 | 南向（dir 2） | 1、2（左轉） | 時段總大車＋小車 |

以 $\\overline{\\text{TT}}_p$ 為路徑 $p$ 的平均旅行時間，$V_p$ 為代表路段時段總通過量（大車＋小車，不含機車）：

$$\\text{加權平均旅行時間} = \\frac{\\displaystyle\\sum_{p} \\overline{\\text{TT}}_p \\times V_p}{\\displaystyle\\sum_{p} V_p}$$

> 若代表路段通過量為零，該路徑不納入計算。

---

#### 三、加權平均旅行時間\_含邊界\_先加

在「加權平均旅行時間」的基礎上，將路徑1~3因路徑定義未涵蓋的邊界路口停等延滯先計入各路徑旅行時間，再加權平均。

**邊界平均延滯定義：**

- $D_{7,0,4,6}$：路口7（S430200）dir 0、4、6 三來向的總停等延滯（車·秒）÷ 三來向總車輛數
- $D_{1,0}$：路口1（S407100）dir 0 的總停等延滯 ÷ 該來向總車輛數
- $D_{14}$：路口14（S406200）dir 6 的總停等延滯 ÷ 該來向總車輛數

調整各路徑旅行時間後再加權：

$$T_1' = T_1 + D_{7,0,4,6} \\qquad T_2' = T_2 + D_{1,0} \\qquad T_3' = T_3 + D_{14} \\qquad T_4' = T_4$$

$$\\text{加權平均旅行時間\\_含邊界\\_先加} = \\frac{\\displaystyle\\sum_{p} T_p' \\times V_p}{\\displaystyle\\sum_{p} V_p}$$

> 若 $V_p$ 為零，該路徑不納入計算。

---

#### 四、加權平均旅行時間\_含邊界\_後加

先計算「加權平均旅行時間」（$\\overline{\\text{TT}}$），再一次性加總三個邊界平均延滯：

$$\\text{加權平均旅行時間\\_含邊界\\_後加} = \\overline{\\text{TT}} + D_{14} + D_{1,0} + D_{7,0,4,6}$$

> 若 $\\overline{\\text{TT}}$ 為缺值，該指標亦為缺值。

---

| 指標 | 時間粒度 | 空間範圍 | 權重來源 |
|------|----------|----------|----------|
| 平均旅行時間 | 時段平均 × 全路徑 | 各路徑代表路段 | 無（算術平均） |
| 加權平均旅行時間 | 時段平均 × 全路徑 | 各路徑代表路段 | VD 時段累計車流量（大＋小車） |
| 加權平均旅行時間\_含邊界\_先加 | 時段平均 × 全路徑 | 各路徑代表路段＋邊界路口 | VD 時段累計車流量，邊界延滯先併入各路徑 |
| 加權平均旅行時間\_含邊界\_後加 | 時段平均 × 全路徑 | 各路徑代表路段＋邊界路口 | 同「加權平均旅行時間」，邊界延滯於加權後加總 |
"""


@st.dialog('旅行時間加權計算方式', width='large')
def _show_tt_info_dialog():
    st.markdown(_TT_METRIC_INFO_MD)


# ── 頁面設定 ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title='AI 號誌事前後分析系統',
    page_icon='🚦',
    layout='wide',
)
# ── 表格格式化輔助函式 ────────────────────────────────────────────────────────
def _format_comp_df(comp_df: pd.DataFrame, metric: str):
    """回傳（格式化後 DataFrame, 原始 改善% Series）供樣式使用。"""
    is_vol  = (metric == '通過量')
    raw_pct = comp_df['改善%'].copy()
    d       = comp_df.copy()

    def fmt_num(v):
        return '—' if pd.isna(v) else (f"{v:,.0f}" if is_vol else f"{v:,.1f}")

    def fmt_pct(v):
        return '—' if pd.isna(v) else f"{v * 100:+.1f}%"

    for c in ['事前平均', '事後平均', '差異']:
        d[c] = d[c].apply(fmt_num)
    d['改善%'] = d['改善%'].apply(fmt_pct)
    return d, raw_pct


def _highlight_pct_col(col_series, raw_pct: pd.Series):
    """只改「改善%」欄的文字顏色（綠/紅），其他欄不動。"""
    styles = []
    for i in range(len(col_series)):
        if col_series.name == '改善%' and i < len(raw_pct):
            v = raw_pct.iloc[i]
            if pd.isna(v):
                styles.append('')
            elif v > 0:
                styles.append('color: #1a7a2e; font-weight: bold')
            else:
                styles.append('color: #b00020; font-weight: bold')
        else:
            styles.append('')
    return styles


def _display_overview_table(
    results: dict,
    inc_tt: bool,
    extra_entities: list[str] | None = None,
    special_metrics: dict | None = None,
):
    """系統層級改善率概覽表格（替代原概覽長條圖）。
    extra_entities：桃園三期等場域需額外顯示的系統層級欄位（如高鐵周邊、A19周邊、前期範圍）。
    special_metrics：{指標名: {before, after, diff, pct}}，若有則附加到表格末尾。
    """
    base_metrics = ['總停等延滯', '通過量', '平均停等延滯']
    row_labels   = ['事前平均', '事後平均', '差異', '改善(%)']

    all_entities = ['系統'] + (list(extra_entities) if extra_entities else [])
    multi_entity = len(all_entities) > 1

    col_raw: dict[str, dict] = {}

    for entity in all_entities:
        for metric in base_metrics:
            mdf = results.get(metric, pd.DataFrame())
            if not mdf.empty:
                e_row = mdf[mdf['欄位'] == entity]
                if not e_row.empty:
                    col_key = f"{entity}_{metric}" if multi_entity else metric
                    col_raw[col_key] = {
                        '事前平均': e_row['事前平均'].values[0],
                        '事後平均': e_row['事後平均'].values[0],
                        '差異':     e_row['差異'].values[0],
                        '改善(%)':  e_row['改善%'].values[0],
                    }

    if special_metrics:
        for metric_name, vals in special_metrics.items():
            col_raw[metric_name] = {
                '事前平均': vals['before'],
                '事後平均': vals['after'],
                '差異':     vals['diff'],
                '改善(%)':  vals['pct'],
            }

    if inc_tt:
        tt_df = results.get('旅行時間', pd.DataFrame())
        if not tt_df.empty:
            for _, row in tt_df.iterrows():
                col_raw[row['欄位']] = {
                    '事前平均': row['事前平均'],
                    '事後平均': row['事後平均'],
                    '差異':     row['差異'],
                    '改善(%)':  row['改善%'],
                }

    if not col_raw:
        st.warning('無概覽資料')
        return

    display_data: dict[str, list] = {}
    raw_imp: dict[str, float]     = {}

    for col_name, vals in col_raw.items():
        is_vol = col_name == '通過量' or (multi_entity and col_name.endswith('_通過量'))
        def _fmt(v, vol=is_vol):
            return '—' if pd.isna(v) else (f"{v:,.0f}" if vol else f"{v:,.1f}")
        display_data[col_name] = [
            _fmt(vals['事前平均']),
            _fmt(vals['事後平均']),
            _fmt(vals['差異']),
            '—' if pd.isna(vals['改善(%)']) else f"{vals['改善(%)'] * 100:+.1f}%",
        ]
        raw_imp[col_name] = vals['改善(%)']

    disp_df = pd.DataFrame(display_data, index=row_labels)
    disp_df.index.name = None

    def _style(df):
        s = pd.DataFrame('', index=df.index, columns=df.columns)
        for col in df.columns:
            v = raw_imp.get(col, float('nan'))
            if not pd.isna(v):
                c = '#1a7a2e' if v > 0 else '#b00020'
                s.loc['改善(%)', col] = f'color: {c}; font-weight: bold'
        return s

    st.dataframe(disp_df.style.apply(_style, axis=None), use_container_width=True)


# ── 日期分配 I/O ─────────────────────────────────────────────────────────────
def _load_selections(path: Path) -> list[dict]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8-sig'))
        except Exception:
            pass
    return []


def _github_push_file(rel_path: str, content_bytes: bytes, commit_msg: str) -> bool:
    """透過 GitHub Contents API 更新檔案；未設定 secrets 或失敗時靜默回傳 False。
    失敗時會將原因存入 st.session_state['_github_push_error']。"""
    st.session_state.pop('_github_push_error', None)
    try:
        cfg    = st.secrets.get('github', {})
        token  = cfg.get('token', '')
        repo   = cfg.get('repo', '')
        branch = cfg.get('branch', 'main')
        if not token or not repo:
            st.session_state['_github_push_error'] = 'not_configured'
            return False
        headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json',
        }
        api_url = f'https://api.github.com/repos/{repo}/contents/{rel_path}'
        r = requests.get(api_url, headers=headers, params={'ref': branch}, timeout=10)
        sha = r.json().get('sha') if r.ok else None
        payload: dict = {
            'message': commit_msg,
            'content': base64.b64encode(content_bytes).decode(),
            'branch':  branch,
        }
        if sha:
            payload['sha'] = sha
        r2 = requests.put(api_url, json=payload, headers=headers, timeout=15)
        if not r2.ok:
            st.session_state['_github_push_error'] = f'HTTP {r2.status_code}'
        return r2.ok
    except Exception as e:
        st.session_state['_github_push_error'] = type(e).__name__
        return False


def _github_sync_suffix() -> str:
    """根據 _github_push_error 產生對應的失敗說明文字。"""
    err = st.session_state.pop('_github_push_error', None)
    if err is None:
        return ''
    if err == 'not_configured':
        return '（GitHub 未設定，重啟後將消失）'
    if err.startswith('HTTP'):
        return f'（GitHub 同步失敗：{err}，請檢查 Token 是否有效）'
    return f'（GitHub 連線失敗：{err}，重啟後將消失）'


def _save_selections(path: Path, selections: list[dict]) -> bool:
    """儲存至本機並同步 GitHub，回傳 GitHub 同步是否成功。"""
    content_bytes = json.dumps(selections, ensure_ascii=False, indent=2).encode('utf-8-sig')
    path.write_bytes(content_bytes)
    return _github_push_file(
        path.relative_to(BASE_DIR).as_posix(),
        content_bytes,
        f'auto: update {path.name}',
    )


@st.dialog('刪除日期分配')
def _delete_dialog(cur_name: str, selections: list):
    st.write(f'確認要刪除「{cur_name}」？刪除後將無法恢復。')
    col_ok, col_cancel = st.columns(2)
    if col_ok.button('確認', use_container_width=True, type='primary'):
        remaining = [s for s in selections if s['name'] != cur_name]
        _save_selections(SELECTION_PATH, remaining)
        st.session_state['_edit_msg'] = f'✅ 已刪除「{cur_name}」'
        st.rerun()
    if col_cancel.button('取消', use_container_width=True):
        st.rerun()


@st.dialog('重新命名日期分配')
def _rename_dialog(cur_name: str, selections: list):
    st.write(f'將「{cur_name}」修改為：')
    new_name = st.text_input('新名稱', placeholder='輸入新名稱', label_visibility='collapsed')
    col_ok, col_cancel = st.columns(2)
    if col_ok.button('確認', use_container_width=True, type='primary'):
        new_name = new_name.strip()
        if not new_name:
            st.warning('請輸入新名稱')
        elif new_name == cur_name:
            st.warning('新名稱與現有名稱相同')
        elif any(s['name'] == new_name for s in selections):
            st.warning('此名稱已存在，請使用其他名稱')
        else:
            updated = [
                {**s, 'name': new_name} if s['name'] == cur_name else s
                for s in selections
            ]
            _save_selections(SELECTION_PATH, updated)
            st.session_state['_edit_msg'] = f'✅ 已將「{cur_name}」重新命名為「{new_name}」'
            st.rerun()
    if col_cancel.button('取消', use_container_width=True):
        st.rerun()


# ── 資料載入（快取，依路徑分別快取）────────────────────────────────────────
@st.cache_data(show_spinner='載入績效資料中…')
def _load_df(csv_path: str, _mtime: float):
    df = dl.load_performance_csv(Path(csv_path))
    return df, dict(dl.get_column_structure())


@st.cache_data(show_spinner='載入績效資料中…')
def _load_df_bytes(content: bytes, _hash: str):
    df = dl.load_performance_csv(io.BytesIO(content))
    return df, dict(dl.get_column_structure())


@st.cache_data(ttl=3600, show_spinner=False)
def _get_file_last_commit_date(rel_path: str) -> str | None:
    """透過 GitHub API 查詢檔案最後一次 commit 的日期（YYYY/MM/DD）。"""
    try:
        cfg    = st.secrets.get('github', {})
        token  = cfg.get('token', '')
        repo   = cfg.get('repo', '')
        branch = cfg.get('branch', 'main')
        if not token or not repo:
            return None
        r = requests.get(
            f'https://api.github.com/repos/{repo}/commits',
            headers={'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json'},
            params={'path': rel_path, 'sha': branch, 'per_page': 1},
            timeout=10,
        )
        if not r.ok or not r.json():
            return None
        iso = r.json()[0]['commit']['committer']['date']   # e.g. "2026-07-21T12:34:56Z"
        return iso[:10].replace('-', '/')
    except Exception:
        return None


# ── 場域選擇（側邊欄最頂部，資料載入前先取得選擇）──────────────────────────
st.sidebar.title('🚦 分析設定')
st.sidebar.subheader('🗺️ 場域選擇')
selected_site = st.sidebar.selectbox('選擇分析場域', list(SITE_OPTIONS.keys()))

# 上傳自訂檔案（優先度最高；上傳時下方 selectbox 自動 disable）
uploaded_csv = st.sidebar.file_uploader(
    '📂 上傳自訂資料檔（選用）',
    type='csv',
    key=f'custom_csv_{selected_site}',
    help='上傳後優先使用此檔案，下方目錄選擇無效；日期分配仍依場域設定',
)

# 從 data/ 目錄選擇其他績效檔案（切換場域時自動重設）
# 排除其他場域的預設檔，避免誤選
_other_defaults = {v for k, v in SITE_OPTIONS.items() if k != selected_site}
_avail_csvs     = sorted(
    p.name for p in DATA_DIR.glob('perf_summary*.csv')
    if p.name not in _other_defaults
)
_default_csv   = SITE_OPTIONS[selected_site]
_selected_data_file = st.sidebar.selectbox(
    '📊 或選擇 data/ 目錄下的資料檔',
    options=_avail_csvs if _avail_csvs else [_default_csv],
    index=(_avail_csvs.index(_default_csv) if _default_csv in _avail_csvs else 0),
    key=f'data_file_{selected_site}',
    disabled=uploaded_csv is not None,
    help='上傳自訂檔案時此選擇無效',
)
_active_file = uploaded_csv.name if uploaded_csv is not None else _selected_data_file
st.sidebar.caption(f'使用資料檔：{_active_file}')
st.sidebar.divider()

# 偵測場域切換，重置日期與分析結果
if st.session_state.get('_current_site') != selected_site:
    st.session_state['_current_site'] = selected_site
    # 強制下方時段選擇重新套用預設值；否則切回先前的場域時 multiselect 會顯示為空白
    st.session_state.pop(f'_day_type_{selected_site}', None)
    st.session_state.pop('date_df', None)
    st.session_state.pop('analysis_results', None)
    st.session_state['editor_ver'] = st.session_state.get('editor_ver', 0) + 1

# 偵測 data/ 資料檔切換，重置日期與分析結果
if st.session_state.get('_current_data_file') != _selected_data_file:
    st.session_state['_current_data_file'] = _selected_data_file
    st.session_state.pop('date_df', None)
    st.session_state.pop('_periods_key', None)   # 強制觸發 date_df 重建
    st.session_state.pop('analysis_results', None)
    st.session_state['editor_ver'] = st.session_state.get('editor_ver', 0) + 1

# 偵測上傳檔案變更（新增或移除），重置日期與分析結果
_upload_content = uploaded_csv.getvalue() if uploaded_csv is not None else None
_upload_hash    = hashlib.md5(_upload_content).hexdigest() if _upload_content else None
if st.session_state.get('_uploaded_csv_hash') != _upload_hash:
    st.session_state['_uploaded_csv_hash'] = _upload_hash
    st.session_state.pop('date_df', None)
    st.session_state.pop('_periods_key', None)   # 強制觸發 date_df 重建
    st.session_state.pop('analysis_results', None)
    st.session_state['editor_ver'] = st.session_state.get('editor_ver', 0) + 1

# 載入當前場域資料
CSV_PATH       = DATA_DIR / _selected_data_file
SELECTION_PATH = DATA_DIR / SELECTION_PATHS[selected_site]
QUALITY_PATH   = DATA_DIR / QUALITY_PATHS[selected_site]
DEGRADATION_PATH = DATA_DIR / DEGRADATION_PATHS[selected_site]

if _upload_content is not None:
    try:
        df, col_struct = _load_df_bytes(_upload_content, _upload_hash)
    except Exception as _e:
        st.error(f'上傳的 CSV 格式有誤，無法讀取：{_e}')
        st.stop()
else:
    if not CSV_PATH.exists():
        st.error(f'找不到資料檔案：{CSV_PATH.name}，請確認已上傳至正確位置。')
        st.stop()
    df, col_struct = _load_df(str(CSV_PATH), CSV_PATH.stat().st_mtime)
dl._col_structure = col_struct          # 還原模組層級快取（繞過 st.cache_data）

all_dates      = dl.get_available_dates(df)
all_periods    = dl.get_available_periods(df)
system_cols    = dl.get_system_columns()
extra_entities = SITE3_EXTRA_ENTITIES if selected_site == '桃園三期(高鐵)' else []
quality_notes  = dl.quality_notes(dl.load_quality(QUALITY_PATH))   # {(日期, 時段): 註記}


# ── 日期表格建立輔助函式 ─────────────────────────────────────────────────────
def _make_date_period_df(periods: list[str],
                          is_weekend: bool = False, filter_by_type: bool = True) -> pd.DataFrame:
    """
    建立「日期 × 時段」分配表（長格式，每列為一個日期＋時段組合），事前/事後均為 False，由使用者勾選。
    filter_by_type：True 時依 is_weekend 過濾日期；False 時顯示全部日期。
    """
    rows = []
    for d in all_dates:
        if filter_by_type and (d.weekday() >= 5) != is_weekend:
            continue
        d_str = d.strftime('%Y/%m/%d')
        for p in periods:
            rows.append({
                '日期': d_str,
                '星期': dl.TW_WEEKDAY[d.weekday()],
                '時段': p,
                '事前': False,
                '事後': False,
            })
    return pd.DataFrame(rows, columns=['日期', '星期', '時段', '事前', '事後'])


# ── Session State 初始化 ────────────────────────────────────────────────────
if 'editor_ver' not in st.session_state:
    st.session_state['editor_ver'] = 0
if 'analysis_results' not in st.session_state:
    st.session_state['analysis_results'] = None

# ── 側邊欄（其餘部分）──────────────────────────────────────────────────────
with st.sidebar:
    st.caption(
        f'資料範圍：{all_dates[0].strftime("%Y/%m/%d")} ～ '
        f'{all_dates[-1].strftime("%Y/%m/%d")}（共 {len(all_dates)} 天）'
    )
    _csv_rel = CSV_PATH.relative_to(BASE_DIR).as_posix()
    _commit_date = _get_file_last_commit_date(_csv_rel)
    _update_str = _commit_date or datetime.fromtimestamp(CSV_PATH.stat().st_mtime).strftime('%Y/%m/%d')
    st.caption(f'資料更新：{_update_str}')
    st.divider()

    # ── 日期類型（平日 / 假日）────────────────────────────────────────────
    st.subheader('📅 日期類型')
    day_type = st.radio(
        '分析日期類型',
        ['平常日（一～五）', '週末（六、日）'],
        horizontal=True,
        label_visibility='collapsed',
        key='day_type_radio',
    )
    is_weekend = (day_type == '週末（六、日）')

    st.divider()

    # ── 分析時段（依場域與日期類型預設）─────────────────────────────────
    st.subheader('📅 分析時段')
    site_def     = SITE_DEFAULTS.get(selected_site, {})
    default_key  = 'periods_weekend' if is_weekend else 'periods_weekday'
    site_def_pds = site_def.get(default_key, ['07:00~09:00', '14:00~16:00', '16:00~19:00'])
    default_periods = [p for p in site_def_pds if p in all_periods]

    periods_widget_key = f'periods_{selected_site}_{day_type}'
    _day_type_tracker  = f'_day_type_{selected_site}'
    if st.session_state.get(_day_type_tracker) != day_type:
        # 切換日期類型時強制重設為該類型的預設時段
        st.session_state[_day_type_tracker]    = day_type
        st.session_state[periods_widget_key]   = default_periods
    elif periods_widget_key not in st.session_state:
        st.session_state[periods_widget_key] = default_periods

    selected_periods = st.multiselect(
        '選擇時段（可多選）',
        options=all_periods,
        key=periods_widget_key,   # 切換場域或日期類型時自動重置
    )

    # 日期類型過濾 checkbox 在後方才渲染，先從 session state 讀取上一次的值
    _filter_by_day_type = st.session_state.get('filter_by_day_type', True)

    # 時段選擇改變時，重新建立日期×時段分配表
    periods_key = (selected_site, day_type, tuple(sorted(selected_periods)), _filter_by_day_type)
    if st.session_state.get('_periods_key') != periods_key:
        old_key = st.session_state.get('_periods_key')
        st.session_state['_periods_key'] = periods_key
        # 場域或日期類型改變 → 全部重建（空白）
        # 時段增減或篩選 checkbox 改變 → 差異更新，保留既有勾選狀態
        can_update_incrementally = (
            old_key is not None
            and old_key[0] == selected_site
            and old_key[1] == day_type
            and 'date_df' in st.session_state
        )
        if can_update_incrementally:
            existing_df     = st.session_state['date_df'].copy()
            old_periods_set = set(old_key[2])
            new_periods_set = set(selected_periods)
            added_periods   = new_periods_set - old_periods_set
            removed_periods = old_periods_set - new_periods_set
            if removed_periods:
                existing_df = existing_df[~existing_df['時段'].isin(removed_periods)].reset_index(drop=True)
            if added_periods:
                new_rows_df = _make_date_period_df(
                    list(added_periods), is_weekend, _filter_by_day_type,
                )
                existing_df = pd.concat([existing_df, new_rows_df], ignore_index=True)
            # 篩選 checkbox 改變：補入或移除對應日期列，保留既有勾選
            if old_key[3] != _filter_by_day_type:
                if _filter_by_day_type:
                    # 重新啟用過濾：移除不符合日期類型的列
                    existing_df = existing_df[
                        existing_df['日期'].apply(lambda d: (pd.Timestamp(d).weekday() >= 5) == is_weekend)
                    ].reset_index(drop=True)
                else:
                    # 取消過濾：補入原本隱藏的日期（空白）
                    shown_keys  = set(zip(existing_df['日期'], existing_df['時段']))
                    all_rows_df = _make_date_period_df(
                        list(new_periods_set), is_weekend, filter_by_type=False,
                    )
                    hidden_rows = all_rows_df[
                        ~all_rows_df.apply(lambda r: (r['日期'], r['時段']) in shown_keys, axis=1)
                    ]
                    if not hidden_rows.empty:
                        existing_df = pd.concat([existing_df, hidden_rows], ignore_index=True)
            st.session_state['date_df'] = existing_df
        else:
            # 初次載入、切換場域、切換日期類型 → 全部重建，預設全空白
            st.session_state['date_df'] = _make_date_period_df(
                selected_periods, is_weekend, _filter_by_day_type,
            )
        st.session_state['editor_ver'] = st.session_state.get('editor_ver', 0) + 1

    st.divider()

    # ── 篩選日期範圍（只影響分析，不改變日期分配的勾選）───────────────
    st.subheader('🔍 篩選日期範圍')
    st.caption('**只影響本次分析，不改變日期分配的勾選。** 事前、事後各自只計入範圍內已勾選的日期；'
               '範圍外的勾選仍保留在下方表格，儲存日期分配時也照原本的勾選儲存，範圍放大即恢復計入。')
    _first, _last = all_dates[0].date(), all_dates[-1].date()
    range_before = st.date_input('事前範圍', value=(_first, _last), min_value=_first, max_value=_last,
                                 key=f'range_before_{selected_site}')
    range_after  = st.date_input('事後範圍', value=(_first, _last), min_value=_first, max_value=_last,
                                 key=f'range_after_{selected_site}')

    def _in_range(rng, d: pd.Timestamp) -> bool:
        """日期是否在範圍內；範圍只選了起始日（尚未選完）時視為不篩選。"""
        return len(rng) != 2 or rng[0] <= d.date() <= rng[1]

    st.divider()

    # ── 日期分配表格 ────────────────────────────────────────────────────────
    st.subheader('📆 日期分配')
    st.caption(
        '每列為一組「日期＋時段」，直接勾選歸入「事前」或「事後」。'
    )

    if st.button('↺ 重設', use_container_width=True):
        cleared_df = st.session_state['date_df'].copy()
        cleared_df['事前'] = False
        cleared_df['事後'] = False
        st.session_state['date_df'] = cleared_df
        st.session_state['editor_ver'] += 1
        st.rerun()

    st.checkbox(
        '只顯示符合日期類型的日期',
        value=True,
        key='filter_by_day_type',
        help='取消勾選可同時顯示平常日與週末，適合跨類型比較分析',
    )

    # ── 批次勾選／取消（一次多選日期）──────────────────────────────────
    st.caption('批次操作：多選日期後，一次套用到該幾天的所有時段列。')
    batch_dates = st.multiselect(
        '選擇日期（可多選）',
        options=list(dict.fromkeys(st.session_state['date_df']['日期'])),
        key='batch_select_dates',
        label_visibility='collapsed',
        placeholder='選擇要批次操作的日期',
    )
    bcol1, bcol2, bcol3 = st.columns(3)

    def _apply_batch(set_before: bool | None, set_after: bool | None):
        new_df = st.session_state['date_df'].copy()
        mask = new_df['日期'].isin(batch_dates)
        if set_before is not None:
            new_df.loc[mask, '事前'] = set_before
        if set_after is not None:
            new_df.loc[mask, '事後'] = set_after
        st.session_state['date_df'] = new_df
        st.session_state['editor_ver'] += 1
        st.rerun()

    if bcol1.button('✅ 設為事前', use_container_width=True, disabled=not batch_dates):
        _apply_batch(True, False)
    if bcol2.button('✅ 設為事後', use_container_width=True, disabled=not batch_dates):
        _apply_batch(False, True)
    if bcol3.button('✖ 取消勾選', use_container_width=True, disabled=not batch_dates):
        _apply_batch(False, False)

    _date_df = st.session_state['date_df']
    edited_df = st.data_editor(
        _date_df.assign(資料=[quality_notes.get((pd.Timestamp(d), p), {}).get('short', '')
                            for d, p in zip(_date_df['日期'], _date_df['時段'])]),
        key=f'date_editor_{st.session_state["editor_ver"]}',
        column_config={
            '日期': st.column_config.TextColumn('日期', disabled=True, width='small'),
            '星期': st.column_config.TextColumn('星期', disabled=True, width='small'),
            '時段': st.column_config.TextColumn('時段', disabled=True, width='small'),
            '事前': st.column_config.CheckboxColumn('事前', width='small'),
            '事後': st.column_config.CheckboxColumn('事後', width='small'),
            '資料': st.column_config.TextColumn('資料', disabled=True,
                                                help='資料品質註記：⛔ 錯誤（無 VD 資料或完整度過低）、⚠️ 警告（完整度偏低或通過量異常，'
                                                     '也可能是颱風、連假前夕等特殊日）'),
        },
        hide_index=True,
        height=400,
        use_container_width=True,
    ).drop(columns='資料')

    before_by_period: dict[str, list] = {}
    after_by_period:  dict[str, list] = {}
    for p in selected_periods:
        sub = edited_df[edited_df['時段'] == p]
        before_by_period[p] = [pd.Timestamp(r) for r in sub.loc[sub['事前'], '日期']]
        after_by_period[p]  = [pd.Timestamp(r) for r in sub.loc[sub['事後'], '日期']]

    # 篩選日期範圍：只從本次分析排除範圍外的勾選，edited_df（儲存用）不變
    _n_out_before = _n_out_after = 0
    for p in selected_periods:
        _kept_b = [d for d in before_by_period[p] if _in_range(range_before, d)]
        _kept_a = [d for d in after_by_period[p] if _in_range(range_after, d)]
        _n_out_before += len(before_by_period[p]) - len(_kept_b)
        _n_out_after  += len(after_by_period[p]) - len(_kept_a)
        before_by_period[p], after_by_period[p] = _kept_b, _kept_a

    # 已勾選的日期中有資料品質註記 → 告警，並可選擇分析時排除
    quality_mode = dl.QUALITY_EXCLUDE[0]
    _sel_flags = dl.flagged_rows(quality_notes, {p: (before_by_period[p], after_by_period[p]) for p in selected_periods})
    if _sel_flags:
        _n_err = sum(r['等級'] != dl.QUALITY_WARN for r in _sel_flags)
        st.warning(f'已勾選的日期中有 {len(_sel_flags)} 格有資料註記（{dl.flag_counts(_sel_flags)}）')
        with st.expander('資料註記明細', expanded=_n_err > 0):
            st.dataframe(pd.DataFrame(_sel_flags), hide_index=True, use_container_width=True)
        quality_mode = st.radio('分析時有資料註記的日期', dl.QUALITY_EXCLUDE, key='quality_exclude',
                                help='只影響本次分析，不改變日期分配的勾選；警告可能是特殊交通日而非資料錯誤，請看明細判斷')
        for p in selected_periods:
            before_by_period[p] = [d for d in before_by_period[p] if not dl.is_excluded(quality_notes.get((d, p)), quality_mode)]
            after_by_period[p]  = [d for d in after_by_period[p] if not dl.is_excluded(quality_notes.get((d, p)), quality_mode)]

    if selected_periods:
        counts_str = '　'.join(
            f"{_esc_md(p)}：事前{len(before_by_period[p])}／事後{len(after_by_period[p])}"
            for p in selected_periods
        )
        st.caption(f'已選：{counts_str}')
        if _n_out_before or _n_out_after:
            st.info(f'依「🔍 篩選日期範圍」，表格中另有事前 {_n_out_before} 格、事後 {_n_out_after} 格勾選'
                    '在範圍外，不計入本次分析（勾選仍保留，儲存時照原勾選儲存）。')

    st.divider()

    # ── 儲存 / 載入日期分配 ─────────────────────────────────────────────────
    st.subheader('💾 儲存 / 載入日期分配')
    saved_selections = _load_selections(SELECTION_PATH)

    save_name = st.text_input('分配名稱', key='save_selection_name', placeholder='例如：早尖峰_2026Q1')
    if st.button('💾 儲存目前日期分配', use_container_width=True):
        name = save_name.strip()
        if not name:
            st.warning('請先輸入分配名稱')
        else:
            new_preset = {
                'name':     name,
                'saved_at': datetime.now().strftime('%Y/%m/%d %H:%M'),
                'day_type': day_type,
                'periods':  selected_periods,
                'rows': [
                    {
                        '日期': r['日期'],
                        '星期': r['星期'],
                        '時段': r['時段'],
                        '事前': bool(r['事前']),
                        '事後': bool(r['事後']),
                    }
                    for r in edited_df.to_dict('records')
                ],
            }
            saved_selections = [s for s in saved_selections if s['name'] != name] + [new_preset]
            synced = _save_selections(SELECTION_PATH, saved_selections)
            suffix = '，已同步至 GitHub ✓' if synced else _github_sync_suffix()
            st.session_state['_save_load_msg'] = f'✅ 已儲存「{name}」{suffix}'
            st.rerun()

    def _apply_load_preset():
        """按鈕的 on_click 回呼：必須在此處（而非按鈕觸發後的一般程式流程）寫入
        widget 對應的 session_state 鍵值，否則會因該 widget 已於本次執行中
        建立而噴出 StreamlitAPIException。"""
        presets = _load_selections(SELECTION_PATH)
        preset = next((s for s in presets if s['name'] == st.session_state['load_selection_name']), None)
        if preset is None:
            return
        valid_periods = [p for p in preset['periods'] if p in all_periods]
        preset_is_weekend = preset['day_type'] == '週末（六、日）'
        filter_by_type = st.session_state.get('filter_by_day_type', True)

        # 直接從儲存的 rows 重建，只保留儲存當時的日期範圍
        # filter_by_type 開啟時過濾不符日期類型的列
        base_df = pd.DataFrame(
            [
                {
                    '日期': r['日期'],
                    '星期': r.get('星期') or r.get('星', ''),  # 兼容舊存檔
                    '時段': r['時段'],
                    '事前': bool(r['事前']),
                    '事後': bool(r['事後']),
                }
                for r in preset['rows']
                if r['時段'] in valid_periods
                and (not filter_by_type
                     or (pd.Timestamp(r['日期']).weekday() >= 5) == preset_is_weekend)
            ],
            columns=['日期', '星期', '時段', '事前', '事後'],
        )

        # 補上 preset 儲存後才新增的日期（空白，不自動勾選）
        full_df = _make_date_period_df(valid_periods, preset_is_weekend, filter_by_type)
        existing_keys  = set(zip(base_df['日期'], base_df['時段']))
        new_rows       = full_df[
            ~full_df.apply(lambda r: (r['日期'], r['時段']) in existing_keys, axis=1)
        ]
        new_date_count = new_rows['日期'].nunique() if not new_rows.empty else 0
        if not new_rows.empty:
            base_df = pd.concat([base_df, new_rows], ignore_index=True)
            # 補入的日期可能落在既有日期之間（如資料後來才補齊的 9/8），依日期排回原位
            base_df = base_df.sort_values('日期', kind='stable', ignore_index=True)

        st.session_state['day_type_radio'] = preset['day_type']
        # 同步更新 day_type tracker，避免 rerun 時觸發「切換日期類型→重設時段」的保護邏輯
        st.session_state[f'_day_type_{selected_site}'] = preset['day_type']
        periods_widget_key = f"periods_{selected_site}_{preset['day_type']}"
        st.session_state[periods_widget_key] = valid_periods
        st.session_state['_periods_key'] = (selected_site, preset['day_type'], tuple(sorted(valid_periods)), filter_by_type)
        st.session_state['date_df'] = base_df
        load_msg = f'✅ 已載入「{preset["name"]}」（{preset["day_type"]}，儲存於 {preset["saved_at"]}）'
        if new_date_count > 0:
            load_msg += f'；已補入 {new_date_count} 筆新日期（未勾選）'
        st.session_state['_save_load_msg'] = load_msg
        st.session_state['editor_ver'] = st.session_state.get('editor_ver', 0) + 1

    show_all_types = st.checkbox('顯示全部日期類型', value=False, key='show_all_preset_types')
    visible_selections = sorted(
        (s for s in saved_selections if show_all_types or s['day_type'] == day_type),
        key=lambda s: s['saved_at'],
        reverse=True,
    )
    preset_names = [s['name'] for s in visible_selections]
    # 過濾條件改變後，原選取值可能已不在選項中，先清除以免 selectbox 報錯
    if st.session_state.get('load_selection_name') not in preset_names:
        st.session_state.pop('load_selection_name', None)

    if visible_selections:
        # 已依日期類型過濾時，類型標籤是多餘的，只在顯示全部時附上
        preset_labels = {
            s['name']: ' · '.join(
                [s['name']]
                + ([('假日' if s['day_type'] == '週末（六、日）' else '平日')] if show_all_types else [])
                + [s['saved_at'][5:10]]
            )
            for s in visible_selections
        }
        st.selectbox('選擇已儲存的分配（可輸入文字搜尋）', preset_names,
                     format_func=preset_labels.get, key='load_selection_name')
        st.button('📂 載入所選分配', use_container_width=True, on_click=_apply_load_preset)
    elif saved_selections:
        st.caption(f'尚無「{day_type}」的已儲存分配，可勾選「顯示全部日期類型」')
    else:
        st.caption('尚無已儲存的日期分配')

    if msg := st.session_state.pop('_save_load_msg', None):
        st.success(msg)

    st.divider()

    # ── 編輯日期分配 ─────────────────────────────────────────────────
    st.subheader('✏️ 編輯日期分配')

    if msg := st.session_state.pop('_edit_msg', None):
        st.success(msg)

    if visible_selections:
        cur_name = st.session_state.get('load_selection_name', '')

        if st.button(f'✏️ 重新命名「{cur_name}」', use_container_width=True):
            _rename_dialog(cur_name, saved_selections)

        if st.button(f'🗑️ 刪除「{cur_name}」', use_container_width=True):
            _delete_dialog(cur_name, saved_selections)

    st.download_button(
        '📤 匯出所有已儲存的日期分配',
        data=SELECTION_PATH.read_bytes() if SELECTION_PATH.exists() else b'[]',
        file_name=SELECTION_PATH.name,
        mime='application/json',
        use_container_width=True,
    )

    uploaded_file = st.file_uploader(
        '📥 匯入日期分配',
        type='json',
        key='import_selections_file',
    )
    if uploaded_file is not None:
        try:
            imported = json.loads(uploaded_file.read().decode('utf-8-sig'))
            existing_map = {s['name']: s for s in saved_selections}
            for s in imported:
                existing_map[s['name']] = s   # 同名者以匯入版本為準
            merged = list(existing_map.values())
            synced = _save_selections(SELECTION_PATH, merged)
            added  = len(merged) - len(saved_selections)
            suffix = '，已同步至 GitHub ✓' if synced else _github_sync_suffix()
            st.session_state['_edit_msg'] = f'✅ 匯入完成：共 {len(merged)} 筆（新增 {added} 筆、更新 {len(imported) - added} 筆）{suffix}'
            st.rerun()
        except Exception as e:
            st.error(f'匯入失敗：{e}')

    st.divider()

    # 選項
    st.subheader('⚙️ 選項')
    include_tt  = st.checkbox('包含旅行時間', value=True)
    use_special = st.checkbox('計算特殊總停等延滯', value=False)
    special_int_cols: list[str] = []
    if use_special:
        col_groups   = dl.get_column_groups()
        int_options  = [k for k in col_groups if k not in ('系統總量', '旅行時間廊道')]
        special_int_cols = st.multiselect(
            '選擇納入計算的路口',
            options=int_options,
            key=f'special_int_cols_{selected_site}',
            placeholder='請選擇路口…',
        )

    if st.button('🔍 診斷 GitHub 連線', key='diag_github'):
        cfg    = st.secrets.get('github', {})
        token  = cfg.get('token', '')
        repo   = cfg.get('repo', '')
        branch = cfg.get('branch', 'main')
        st.write(f'**secrets 有 github 區塊：** {bool(cfg)}')
        st.write(f'**token 長度：** {len(token)}，開頭：`{token[:8] if token else "(空)"}...`')
        st.write(f'**repo：** `{repo}`')
        st.write(f'**branch：** `{branch}`')
        if token and repo:
            headers = {
                'Authorization': f'token {token}',
                'Accept': 'application/vnd.github.v3+json',
            }
            test_url = f'https://api.github.com/repos/{repo}'
            try:
                r = requests.get(test_url, headers=headers, timeout=10)
                st.write(f'**GET /repos/{repo} → HTTP {r.status_code}**')
                if not r.ok:
                    st.error(f'錯誤內容：{r.json().get("message", r.text[:200])}')
            except Exception as e:
                st.error(f'連線例外：{type(e).__name__}: {e}')

    st.divider()

    usable_periods = [p for p in selected_periods if before_by_period[p] and after_by_period[p]]
    run_disabled = (not selected_periods or not usable_periods)
    if st.button('🔍 執行分析', type='primary', use_container_width=True, disabled=run_disabled):
        with st.spinner('計算中…'):
            compare_cols = dl.get_column_structure().get('all_data', system_cols)
            all_results  = {}
            for period in selected_periods:
                all_results[period] = cl.compute_comparison(
                    df, period, before_by_period[period], after_by_period[period],
                    compare_cols, include_travel_time=include_tt,
                )
            special_raws: dict[str, dict] = {}
            if use_special and special_int_cols:
                sys_cols_list = dl.get_system_columns()
                sys_vol_col   = sys_cols_list[0] if sys_cols_list else ''
                for period in selected_periods:
                    special_raws[period] = cl.compute_special_raw(
                        df, period, before_by_period[period], after_by_period[period],
                        special_int_cols, sys_vol_col,
                    )
            st.session_state['analysis_results'] = {
                'results':          all_results,
                'before_by_period': before_by_period,
                'after_by_period':  after_by_period,
                'periods':          selected_periods,
                'include_tt':       include_tt,
                'special_raws':     special_raws,
                'quality_mode':     quality_mode,
                'quality_flags':    dl.flagged_rows(quality_notes, {p: (before_by_period[p], after_by_period[p])
                                                                    for p in selected_periods}),
            }
        st.success('分析完成！')

    if run_disabled:
        st.caption('⚠️ 請先選擇時段，且至少一個時段同時有事前與事後日期')
    elif len(usable_periods) < len(selected_periods):
        missing = [p for p in selected_periods if p not in usable_periods]
        st.caption(f'⚠️ 以下時段缺少事前或事後日期，分析結果將留白：{"、".join(_esc_md(p) for p in missing)}')

# ── 全年效益估算分頁 ─────────────────────────────────────────────────────────
_BENEFIT_NONE = '（不計）'
_BENEFIT_FORMULA_MD = """
**每日效益值 ΔD（車-秒/日）**：每個「日期類型 × 時段」各算一次（D̄、V̄ 為所選日期的平均每日總停等延滯、通過量）
- **A 總延滯相減**：ΔD = D̄前 − D̄後（與 Excel 範本相同）
- **B 平均延滯差 × 事後通過量**：ΔD = (D̄前/V̄前 − D̄後/V̄後) × V̄後（扣除事前後交通量差異）

**全年效益值（車-秒/年）**
- **1 只計實測時段**：Σ ΔD × 年天數（平日 260、假日 104）
- **2 時數放大至全日**：Σ ΔD × 代表時數 / 時段時數 × 年天數；尖峰時段只代表自己，
  離峰時段依長度分攤「每日計算時數 − 尖峰時數」

**人-分鐘** = H × 小客車都會平均乘載率 × 60（H = 全年車-秒 / 3600；每車都以小客車乘載率計）

**貨幣化**（H = 全年車-秒 / 3600）：
時間價值 = H × 時間價值；節油（公升）= H × 怠速油耗率；油費 = 節油 × 油價；
CO2（公噸）= 節油 × 排放率（公斤/公升）/ 1000；CO2 效益 = CO2 × 損害成本；合計 = 時間價值 + 油費 + CO2 效益

**改善率** = 全年效益值 ÷ 全年基準延滯（A 基準 = D̄前；B 基準 = D̄前/V̄前 × V̄後，放大方式同效益值）；
油耗、CO2 與停等時間成正比，三者改善率相同。

四種組合（A1、A2、B1、B2）都列出，採**全年貨幣化效益最高者**。完整說明見 `doc/全年效益估算說明.md`。
"""


_DEG_HELP = (
    '來源：平台降階紀錄（sysErrRecord），由計算專案 `aisig publish` 產生。'
    '同一事件會在子系統每台控制器各記一筆，依開始時間排序後，與前一筆相隔不到 5 秒的視為同一事件'
    '（同時發生、不同路口的事件也會併成一次）。'
    '不納入：原因含「因時相轉換暫停運作」「其他閃光」的紀錄，以及開始與結束不在同一天的跨日紀錄。\n\n'
    '- **降階次數**：當日開始的降階事件數。\n'
    '- **小時制降階次數**：當日有降階事件開始的小時數，同一小時內不論幾次只計 1 次。\n'
    '- **降階分鐘**：當日處於降階狀態的總分鐘數，同時發生的降階不重複計。'
)


def _render_degradation_view():
    d = dl.load_degradation(DEGRADATION_PATH)
    if d.empty:
        st.info(f'尚無降階統計（`data/{DEGRADATION_PATH.name}`，由計算專案 aisig publish 產生）。')
        return
    st.markdown(_esc_md(_DEG_HELP))
    lo, hi = d['日期'].min().date(), d['日期'].max().date()
    rng = st.date_input('日期範圍', value=(lo, hi), min_value=lo, max_value=hi, key=f'deg_range_{selected_site}')
    if not (isinstance(rng, tuple) and len(rng) == 2):
        st.info('請選擇起訖日期。')
        return
    d = d[(d['日期'].dt.date >= rng[0]) & (d['日期'].dt.date <= rng[1])]
    n = len(d)
    c = st.columns(4)
    c[0].metric('天數', f'{n}', f'有降階 {(d["降階次數"] > 0).sum()} 天', delta_color='off')
    c[1].metric('降階次數', f'{d["降階次數"].sum()}', f'日均 {d["降階次數"].mean():.2f}', delta_color='off')
    c[2].metric('小時制降階次數', f'{d["小時制降階次數"].sum()}', f'日均 {d["小時制降階次數"].mean():.2f}',
                delta_color='off')
    c[3].metric('降階總時長', f'{d["降階分鐘"].sum() / 60:,.1f} 小時', f'日均 {d["降階分鐘"].mean():.1f} 分鐘',
                delta_color='off')
    st.plotly_chart(cb.make_degradation_count_chart(d), use_container_width=True)
    st.plotly_chart(cb.make_degradation_minutes_chart(d), use_container_width=True)
    st.markdown('**每月彙總**')
    st.dataframe(dl.degradation_monthly(d), hide_index=True, use_container_width=True)
    st.markdown('**每日明細**')
    st.dataframe(d.assign(日期=d['日期'].dt.strftime('%Y/%m/%d')), hide_index=True, use_container_width=True,
                 column_config={'降階分鐘': st.column_config.NumberColumn(format='%.1f')})


def _render_benefit_view():
    cfg    = bl.load_params()
    params = bl.param_values(cfg)
    st.caption('選擇已儲存的平日與假日日期分配，估算全年節省的停等時間、油耗與 CO2。'
               '參數在 `data/benefit_params.json`（更新日期 ' + cfg['updated'] + '）。')

    by_type = {
        t: [s['name'] for s in sorted(saved_selections, key=lambda s: s['saved_at'], reverse=True)
            if (s['day_type'] == '週末（六、日）') == (t == bl.WEEKEND)]
        for t in (bl.WEEKDAY, bl.WEEKEND)
    }
    c1, c2, c3 = st.columns(3)
    names = {
        bl.WEEKDAY: c1.selectbox('平日日期分配', by_type[bl.WEEKDAY] + [_BENEFIT_NONE],
                                 key=f'benefit_wd_{selected_site}'),
        bl.WEEKEND: c2.selectbox('假日日期分配', by_type[bl.WEEKEND] + [_BENEFIT_NONE],
                                 key=f'benefit_we_{selected_site}'),
    }
    entity = c3.selectbox('計算對象', ['系統'] + extra_entities, key=f'benefit_entity_{selected_site}')

    sel_map   = {s['name']: s for s in saved_selections}
    available = set(dl.get_available_periods(df))
    items = []
    for dtype, name in names.items():
        if name == _BENEFIT_NONE:
            continue
        for per, (b, a) in bl.preset_dates(sel_map[name], weekend=(dtype == bl.WEEKEND)).items():
            if per in available:
                items.append({'日期類型': dtype, '時段': per, '納入': bool(b and a),
                              '尖峰': bl.is_peak(per, cfg['peak_windows']),
                              '事前日數': len(b), '事後日數': len(a), '_dates': (b, a)})
    if not items:
        st.info('請至少選擇一筆日期分配。')
        return

    st.markdown(_esc_md(
        '**時段設定**\n'
        '- **納入**：勾選才計入全年效益；同一日期類型的時段不可重疊（如 07:00~09:00 與 06:30~20:30），'
        '否則延滯會重複計算。\n'
        '- **尖峰**：只影響 A2、B2（放大至 06:00~22:00 共 16 小時）。尖峰時段只算自己的時數；'
        '離峰時段依長度分攤「16 − 尖峰時數」。全部勾尖峰時不放大，其餘時間不計（偏保守）。'
        '預設：時段一半以上落在 07~09 或 17~19 即為尖峰。'
    ))
    edited = st.data_editor(
        pd.DataFrame([{k: v for k, v in it.items() if k != '_dates'} for it in items]),
        disabled=['日期類型', '時段', '事前日數', '事後日數'],
        hide_index=True, use_container_width=True,
        key=f'benefit_editor_{selected_site}_{names[bl.WEEKDAY]}_{names[bl.WEEKEND]}',
    )

    b_mode = st.radio('有資料註記的日期', dl.QUALITY_EXCLUDE, horizontal=True, key=f'benefit_quality_{selected_site}',
                      help='⛔ 錯誤 = 無 VD 資料或完整度過低；🚧 排除 = 外部壅塞（路網全面壅塞且非車流增加，如高速公路管制、事故）；'
                           '⚠️ 警告 = 完整度偏低或通過量異常（也可能是颱風、連假前夕等特殊日）')

    detail_items, skipped, used_dates = [], [], {}
    for it, row in zip(items, edited.to_dict('records')):
        if not row['納入']:
            continue
        b, a = ([d for d in ds if not dl.is_excluded(quality_notes.get((d, it['時段'])), b_mode)] for ds in it['_dates'])
        if not (b and a):
            skipped.append(f"{it['日期類型']} {it['時段']}（缺事前或事後日期）")
            continue
        m = bl.period_means(df, it['時段'], b, a, entity)
        if any(pd.isna(v) or v == 0 for v in m.values()):
            skipped.append(f"{it['日期類型']} {it['時段']}（{entity} 無延滯或通過量資料）")
            continue
        detail_items.append({'日期類型': it['日期類型'], '時段': it['時段'], '尖峰': bool(row['尖峰']),
                             **m, '事前日數': len(b), '事後日數': len(a)})
        used_dates[(it['日期類型'], it['時段'])] = (b, a)
    if skipped:
        st.warning('以下時段不計入：' + '、'.join(_esc_md(s) for s in skipped))
    for dtype in (bl.WEEKDAY, bl.WEEKEND):
        pairs = bl.overlapping_pairs([d['時段'] for d in detail_items if d['日期類型'] == dtype])
        if pairs:
            st.error(f'{dtype}時段重疊，延滯會重複計算，請取消其中之一：'
                     + '、'.join(_esc_md(f'{x} 與 {y}') for x, y in pairs))
            return
    if not detail_items:
        st.info('沒有可計算的時段。')
        return
    _qf = [{'日期類型': dtype, **r} for (dtype, per), ba in used_dates.items()
           for r in dl.flagged_rows(quality_notes, {per: ba})]
    if _qf:
        _n_err = sum(r['等級'] != dl.QUALITY_WARN for r in _qf)
        st.warning(f'計算使用的日期中有 {len(_qf)} 格有資料註記（{dl.flag_counts(_qf)}），'
                   '平均延滯與通過量可能受影響；可在上方選擇排除。')
        with st.expander('資料註記明細', expanded=_n_err > 0):
            st.dataframe(pd.DataFrame(_qf), hide_index=True, use_container_width=True)

    detail      = bl.build_detail(detail_items, params)
    table, best = bl.summarize(detail, params)
    r = table.loc[best]

    st.subheader('🌱 全年效益（採用 ' + bl.combo_label(best[0], best[1]) + '）')
    _pct = None if pd.isna(r['改善率']) else (
        f"{r['改善率'] * 100:+.1f}% " + ('改善' if r['改善率'] >= 0 else '惡化'))
    k1, k2, k3, k4 = st.columns(4)
    k1.metric('節省停等時間', f"{r['全年效益值(車-小時)']:,.0f} 車-小時/年", _pct)
    k1.caption(f"≈ {r['節省停等時間(人-分鐘)'] / 1e4:,.0f} 萬 人-分鐘/年（× 乘載率 {params['occupancy']} 人/車 × 60）")
    k2.metric('節省油耗', f"{r['油耗節省(公升)']:,.0f} 公升/年", _pct)
    k3.metric('減少 CO2', f"{r['CO2減少量(公噸)']:,.1f} 公噸/年", _pct)
    k4.metric('全年貨幣化效益', f"{r['全年貨幣化效益(元)'] / 1e4:,.0f} 萬元/年")
    st.caption('採用四種組合中全年貨幣化效益最高者；各組合結果見下表。'
               '改善率 = 節省量 ÷ 原時制下的全年停等延滯（A 用事前總延滯，B 用事前每車延滯 × 事後通過量）；'
               '油耗、CO2 都與停等時間成正比，所以三者改善率相同。')

    st.markdown('**四種計算組合**')
    _fmt = {c: '{:,.0f}' for c in table.columns if c != '組合'}
    _fmt.update({'全年效益值(車-小時)': '{:,.1f}', '油耗節省(公升)': '{:,.1f}', 'CO2減少量(公噸)': '{:,.2f}',
                 '改善率': '{:+.1%}'})
    st.dataframe(
        table.style.format(_fmt).apply(
            lambda s: ['background-color: #C6EFCE; color: #006100; font-weight: bold' if s.name == best else ''] * len(s), axis=1),
        use_container_width=True, hide_index=True,
    )

    st.markdown(f'**各時段明細**（{entity}）')
    show = detail.rename(columns={'D_before': '事前總延滯', 'D_after': '事後總延滯',
                                  'V_before': '事前通過量', 'V_after': '事後通過量'})
    show['尖峰'] = show['尖峰'].map({True: '是', False: '否'})
    _dfmt = {c: '{:,.0f}' for c in show.columns
             if c.startswith(('事前總', '事後總', '事前通', '事後通', 'ΔD', '基準', '年車秒', '年基準'))}
    _dfmt.update({'時數': '{:.1f}', '代表時數': '{:.1f}'})
    st.dataframe(show.style.format(_dfmt), use_container_width=True, hide_index=True)
    st.caption('單位：總延滯、ΔD、基準為車-秒/日；通過量為輛/日；年車秒為該時段對全年效益值的貢獻、'
               '年基準為改善率的分母（車-秒/年）。')

    with st.expander('📐 計算方式與參數', expanded=False):
        st.markdown(_BENEFIT_FORMULA_MD)
        st.dataframe(pd.DataFrame([{'參數': v['label'], '數值': v['value'], '單位': v['unit'], '出處': v['source']}
                                   for v in cfg['params'].values()]),
                     use_container_width=True, hide_index=True)

    meta = {'場域': selected_site, '平日日期分配': names[bl.WEEKDAY], '假日日期分配': names[bl.WEEKEND],
            '計算對象': entity, '資料註記': b_mode, '產製時間': datetime.now().strftime('%Y/%m/%d %H:%M')}
    st.download_button(
        '⬇️ 下載全年效益 Excel（含公式）',
        data=eb.build_benefit_xlsx(detail, best, cfg, meta, used_dates,
                                   {k: v['text'] for k, v in quality_notes.items()}),
        file_name=f"全年效益_{selected_site}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


# ── 主畫面 ──────────────────────────────────────────────────────────────────
st.title('🚦 AI 號誌事前後分析系統')
st.subheader(f'場域：{selected_site}')


# ── 關鍵數字列與共用選擇器（分析後才填入，位置在分頁上方）───────────────────────
focus_box = st.container()

# 分頁以 radio 實作：st.tabs 無法得知目前分頁，橫幅與選擇器需依分頁切換。
# 只有目前分頁的 widget 會被渲染，未渲染者的狀態會被 Streamlit 清除，故先自我指派保留。
st.session_state.setdefault('daily_k', 1.5)
for _k in ('focus_period', 'focus_metric', 'show_directions', 'daily_scope', 'daily_k'):
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]

VIEW_OVERVIEW, VIEW_DETAIL, VIEW_DAILY, VIEW_BENEFIT, VIEW_DEGRADE, VIEW_EXPORT = (
    '📊 總覽', '🔍 時段明細', '📅 每日分析', '🌱 全年效益', '📉 降階紀錄', '💾 匯出')
view = st.radio('分頁', [VIEW_OVERVIEW, VIEW_DETAIL, VIEW_DAILY, VIEW_BENEFIT, VIEW_DEGRADE, VIEW_EXPORT],
                horizontal=True, key='main_view', label_visibility='collapsed')

# ── 全年效益估算（直接讀已儲存的平日／假日日期分配，不需先執行分析）──────────────
if view == VIEW_BENEFIT:
    _render_benefit_view()

# ── 降階紀錄（讀計算專案發佈的每日降階統計，不需先執行分析）──────────────────────
if view == VIEW_DEGRADE:
    _render_degradation_view()

if st.session_state['analysis_results'] is None:
    if view == VIEW_OVERVIEW:
        st.info('請在左側設定分析條件後，點擊「執行分析」按鈕。')
        st.markdown("""
**使用步驟：**
1. 左側選擇「場域」（桃園四期大湳 或 桃園三期高鐵）
2. 選擇「日期類型」（平常日 / 週末）—— 分析時段會自動切換
3. 在「💾 儲存 / 載入日期分配」載入日期分配（如「*_自動更新」），
   可視需要用「🔍 篩選日期範圍」分別限縮事前／事後的日期，或直接手動勾選
4. 視需要勾選「包含旅行時間」
5. 點擊「執行分析」
""")
    elif view not in (VIEW_BENEFIT, VIEW_DEGRADE):
        st.info('請先在左側設定分析條件並點擊「執行分析」。')
    st.stop()

# 取出分析結果
saved           = st.session_state['analysis_results']
all_results     = saved['results']
bd_by_period    = saved['before_by_period']
ad_by_period    = saved['after_by_period']
periods         = saved['periods']
inc_tt          = saved['include_tt']
special_raws    = saved.get('special_raws', {})

if view not in (VIEW_BENEFIT, VIEW_DEGRADE) and saved.get('quality_flags'):
    _qf = saved['quality_flags']
    st.warning(f"本次分析使用的日期中有 {len(_qf)} 格有資料註記（{dl.flag_counts(_qf)}；"
               f"設定：{saved['quality_mode']}），結果可能受影響。可在左側「日期分配」下方選擇排除後重新分析。")
    with st.expander('資料註記明細'):
        st.dataframe(pd.DataFrame(_qf), hide_index=True, use_container_width=True)


# ── 共用選擇器（時段明細、每日分析共用）＋ 系統層級關鍵數字 ──────────────────────
_focus_metrics = ['平均停等延滯', '總停等延滯', '通過量'] + (['旅行時間'] if inc_tt else [])
# 重新分析後時段或指標清單可能改變，失效的選擇先清除，避免 selectbox 取到不存在的值
if st.session_state.get('focus_period') not in periods:
    st.session_state.pop('focus_period', None)
if st.session_state.get('focus_metric') not in _focus_metrics:
    st.session_state.pop('focus_metric', None)

overview_all = cl.aggregate_periods(all_results, periods, include_travel_time=inc_tt, extra_entities=extra_entities)
# 時段明細／每日分析：顯示選擇器，橫幅為所選時段；其餘分頁：隱藏選擇器，橫幅為全時段合計
_focus_view = view in (VIEW_DETAIL, VIEW_DAILY)

with focus_box:
    if _focus_view:
        _fc1, _fc2, _fc3 = st.columns([2, 2, 2])
        focus_period = _fc1.selectbox('時段', periods, key='focus_period',
                                      help='「時段明細」與「每日分析」分頁共用此設定')
        focus_metric = _fc2.selectbox('指標', _focus_metrics, key='focus_metric',
                                      help='「時段明細」與「每日分析」分頁共用此設定')
        _fc3.markdown('<div style="height: 1.9rem"></div>', unsafe_allow_html=True)
        show_directions = _fc3.checkbox('顯示全方向績效', value=False, key='show_directions',
                                        help='勾選後，表格與圖表會加入各路口來向（A/B/…）欄位；預設僅顯示系統與路口層級。')
        _kpi_src = all_results.get(focus_period, {})
    else:
        _kpi_src = overview_all

    # (卡片標題, 結果表指標, 欄位)；加權平均旅行時間只在有此廊道欄且納入旅行時間時顯示
    # 分左右兩欄（「事前 → 事後」字串較長，4 欄並排時會被截斷）：
    # 左欄 平均停等延滯、加權平均旅行時間；右欄 總停等延滯、通過量
    _left  = [('系統 平均停等延滯', '平均停等延滯', '系統')]
    if inc_tt and '加權平均旅行時間' in dl.get_travel_time_columns():
        _left.append(('加權平均旅行時間', '旅行時間', '加權平均旅行時間'))
    _right = [(f'系統 {_m}', _m, '系統') for _m in ['總停等延滯', '通過量']]
    _kpi_l, _kpi_r = st.columns(2)
    for _col, (_label, _m, _entity) in [(_kpi_l, k) for k in _left] + [(_kpi_r, k) for k in _right]:
        _mdf = _kpi_src.get(_m, pd.DataFrame())
        _row = _mdf[_mdf['欄位'] == _entity] if not _mdf.empty else _mdf
        if _row.empty:
            _col.metric(_label, '—')
            continue
        _b, _a, _pct = (_row[c].values[0] for c in ['事前平均', '事後平均', '改善%'])
        _f = '{:,.0f}' if _m == '通過量' else '{:,.1f}'
        _col.metric(
            f'{_label}（{cl.METRIC_UNITS[_m]}）',
            f'{_f.format(_b)} → {_f.format(_a)}',
            None if pd.isna(_pct) else f'{_pct * 100:+.1f}% '
            + (('增加' if _pct >= 0 else '減少') if _m == '通過量' else ('改善' if _pct >= 0 else '惡化')),
        )
    if _focus_view:
        st.caption(f'關鍵數字為 {_esc_md(focus_period)} 時段、事前 {len(bd_by_period[focus_period])} 日 / '
                   f'事後 {len(ad_by_period[focus_period])} 日的結果；綠色＝改善。')
    else:
        st.caption('關鍵數字為所有已選時段的系統層級合計（延滯與通過量加總、旅行時間取各時段平均）；綠色＝改善。')

# ── 總覽 ──────────────────────────────────────────────────────────────────────
if view == VIEW_OVERVIEW:
    # ── 頂部摘要指標（各時段事前／事後天數可能不同，逐時段列出）──────────────────
    summary_rows = [
        {'時段': p, '事前日數': len(bd_by_period[p]), '事後日數': len(ad_by_period[p])}
        for p in periods
    ]
    st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

    # ── 全時段合計概覽 ────────────────────────────────────────────────────────────
    st.subheader('📊 系統層級改善率概覽')
    _cap_col, _info_col = st.columns([10, 1])
    _cap_col.caption('整合所有已選時段：總停等延滯／通過量採加總計算，平均停等延滯由加總後重新推導，旅行時間採各時段平均。')
    if inc_tt:
        with _info_col:
            if st.button('ℹ️', help='旅行時間加權計算方式', key='tt_info_btn'):
                _show_tt_info_dialog()
    agg_special_raw = cl.aggregate_special_raws(special_raws, periods) if special_raws else None
    agg_special     = cl.format_special_metrics(agg_special_raw) if agg_special_raw else None
    _display_overview_table(overview_all, inc_tt, extra_entities, agg_special)

    st.divider()

    # ── 概覽表 ────────────────────────────────────────────────────────────────────
    st.subheader('📊 各時段系統層級改善率概覽')
    for period, results in all_results.items():
        if len(all_results) > 1:
            st.markdown(f"**⏱ {period}**")
        period_special = cl.format_special_metrics(special_raws[period]) if special_raws.get(period) else None
        _display_overview_table(results, inc_tt, extra_entities, period_special)

    # ── 分析摘要 ──────────────────────────────────────────────────────────────────
    with st.expander('📝 分析摘要（展開）', expanded=False):
        before_counts = {p: len(bd_by_period[p]) for p in periods}
        after_counts  = {p: len(ad_by_period[p]) for p in periods}
        st.markdown(cl.generate_analysis_text(all_results, before_counts, after_counts))


# ── 時段明細：依共用選擇器顯示單一時段 × 單一指標 ────────────────────────────
_approach_col_set = set(dl.get_approach_columns())

if view == VIEW_DETAIL:
    comp_df = all_results.get(focus_period, {}).get(focus_metric, pd.DataFrame())
    st.subheader(f'{"🛣️" if focus_metric == "旅行時間" else "📊"} {_esc_md(focus_period)}　{focus_metric}')
    if comp_df.empty:
        st.warning(f'無 {focus_metric} 資料')
    else:
        if focus_metric != '旅行時間' and not show_directions:
            comp_df = comp_df[~comp_df['欄位'].isin(_approach_col_set)].reset_index(drop=True)
        display_df, raw_pct = _format_comp_df(comp_df, focus_metric)
        st.dataframe(
            display_df.style.apply(_highlight_pct_col, axis=0, raw_pct=raw_pct),
            use_container_width=True,
            hide_index=True,
        )
        st.plotly_chart(
            cb.make_travel_time_chart(comp_df, focus_period) if focus_metric == '旅行時間'
            else cb.make_metric_bar_chart(comp_df, focus_metric, focus_period),
            use_container_width=True,
        )


if view == VIEW_DAILY:
    # ── 每日資料分析 ──────────────────────────────────────────────────────────────
    st.caption(f'{_esc_md(focus_period)}　{focus_metric}：逐日檢視事前／事後數值；以 IQR 法在同組內找出明顯較差／較好的日期。'
               '數值為 0 或缺值的日期標為「資料異常」，不列入統計與異常判定。')

    daily_period, daily_metric = focus_period, focus_metric
    _dc1, _dc2 = st.columns(2)
    daily_scope  = _dc1.radio('顯示範圍', ['事前＋事後', '只看事前', '只看事後'],
                              horizontal=True, key='daily_scope')
    daily_k      = _dc2.slider('異常門檻（IQR 倍數）', 1.0, 3.0, step=0.5, key='daily_k',
                               help='超出 Q1 − k×IQR ～ Q3 + k×IQR 範圍者視為異常；k 越小標記越多')

    if daily_metric == '旅行時間':
        _entity_opts = dl.get_travel_time_columns()
    else:
        _groups = dl.get_column_structure().get('groups', {})
        _entity_opts = dl.get_system_columns() + list(_groups)
        if show_directions:
            _entity_opts += dl.get_approach_columns()
    daily_cols = st.multiselect(
        '對象', _entity_opts, default=_entity_opts[:1], format_func=dl.get_display_name,
        key=f'daily_cols_{daily_metric}_{show_directions}',
    )

    if daily_cols:
        _unit     = cl.METRIC_UNITS.get(daily_metric, '')
        _num_fmt  = '{:,.0f}' if daily_metric == '通過量' else '{:,.1f}'
        _b_dates  = bd_by_period[daily_period]
        _a_dates  = ad_by_period[daily_period]
        _analyses = {c: cl.analyze_daily(df, daily_period, _b_dates, _a_dates, daily_metric, c, daily_k)
                     for c in daily_cols}
        _scope_groups = {'事前＋事後': ('事前', '事後'), '只看事前': ('事前',), '只看事後': ('事後',)}[daily_scope]
        _flag_icons = {cl.DAILY_WORSE: '🔴 較差', cl.DAILY_BETTER: '🟢 較好', cl.DAILY_INVALID: '⚠️ 資料異常'}

        # 每日明細表（一列一天，每個對象三欄：數值／差%／標記）
        _base = _analyses[daily_cols[0]][0]
        _base = _base[_base['組別'].isin(_scope_groups)]
        tbl = pd.DataFrame({
            '日期':    _base['日期'].dt.strftime('%Y/%m/%d'),
            '星期':    _base['日期'].apply(lambda d: dl.TW_WEEKDAY[d.weekday()]),
            '組別':    _base['組別'],
            '資料註記': _base['日期'].apply(lambda d: quality_notes.get((d, daily_period), {}).get('text', '')),
        })
        _flag_by_col: dict[str, pd.Series] = {}
        for c in daily_cols:
            d = _analyses[c][0].loc[_base.index]
            name = dl.get_display_name(c)
            tbl[name]           = d['數值']
            tbl[f'{name} 差%']  = d['差%']
            tbl[f'{name} 標記'] = d['標記'].map(_flag_icons).fillna('')
            _flag_by_col[name]  = d['標記']

        _flag_bg = {cl.DAILY_WORSE: 'background-color: #FFC7CE',
                    cl.DAILY_BETTER: 'background-color: #C6EFCE',
                    cl.DAILY_INVALID: 'background-color: #FFF2CC'}
        _lower = daily_metric in cl.LOWER_BETTER

        def _daily_style(frame):
            sty = pd.DataFrame('', index=frame.index, columns=frame.columns)
            for name, flags in _flag_by_col.items():
                sty[name] = flags.map(_flag_bg).fillna('')
                pct = frame[f'{name} 差%']
                worse = pct > 0 if _lower else pct < 0
                sty[f'{name} 差%'] = [
                    '' if pd.isna(v) else ('color: #b00020' if w else 'color: #1a7a2e')
                    for v, w in zip(pct, worse)
                ]
            return sty

        _fmt = {name: _num_fmt for name in _flag_by_col}
        _fmt.update({f'{name} 差%': '{:+.1%}' for name in _flag_by_col})
        st.markdown(f'**{_esc_md(daily_period)}　{daily_metric}（{_unit}）每日明細**　'
                    '— 點欄位標題可排序；右上角可下載 CSV')
        st.dataframe(
            tbl.style.apply(_daily_style, axis=None).format(_fmt, na_rep='—'),
            use_container_width=True, hide_index=True,
        )

        # 各對象：統計摘要 + 趨勢圖 + 分布圖
        for c in daily_cols:
            daily, summ = _analyses[c]
            daily = daily[daily['組別'].isin(_scope_groups)]
            name  = dl.get_display_name(c)
            st.markdown(f'#### {name}')

            m1, m2 = st.columns(2)
            _pct = lambda v: '—' if pd.isna(v) else f'{v * 100:+.1f}%'
            m1.metric('改善率（全部日期）', _pct(summ['改善%']))
            m2.metric('改善率（排除有標記的日期）', _pct(summ['改善%_排除異常日']),
                      help='排除「較差／較好／資料異常」日期後重新計算；與全部日期差距大，代表結論受少數日期影響')

            _fmt_v = lambda v: '—' if pd.isna(v) else _num_fmt.format(v)
            _fmt_d = lambda d: '—' if d is None else dl.format_date(d)
            stat_rows = []
            for g in _scope_groups:
                sg = summ[g]
                stat_rows.append({
                    '組別': g,
                    '天數（有效/總計）': f"{sg['有效天數']}/{sg['天數']}",
                    '平均': _fmt_v(sg['平均']),
                    '中位數': _fmt_v(sg['中位數']),
                    '標準差': _fmt_v(sg['標準差']),
                    'CV': '—' if pd.isna(sg['CV']) else f"{sg['CV']:.1%}",
                    '最佳日': _fmt_d(sg['最佳日']),
                    '最差日': _fmt_d(sg['最差日']),
                })
            st.dataframe(pd.DataFrame(stat_rows), use_container_width=True, hide_index=True)
            _few = [g for g in _scope_groups if summ[g]['有效天數'] < cl.MIN_DAYS_FOR_OUTLIER]
            if _few:
                st.caption(f'ℹ️ {"、".join(_few)}有效天數少於 {cl.MIN_DAYS_FOR_OUTLIER} 天，不做異常判定。')

            ch1, ch2 = st.columns([3, 1])
            ch1.plotly_chart(
                cb.make_daily_trend_chart(
                    daily, {g: summ[g]['平均'] for g in _scope_groups},
                    f'{name}　{daily_metric}（{_unit}）每日趨勢',
                ),
                use_container_width=True,
            )
            ch2.plotly_chart(cb.make_daily_box_chart(daily, '分布'), use_container_width=True)

if view == VIEW_EXPORT:
    # ── Excel 匯出 ────────────────────────────────────────────────────────────────
    include_raw = st.checkbox(
        '包含原始資料工作表',
        value=False,
        help='勾選後，Excel 報告末頁會加入「原始資料」工作表，列出所選日期的完整數據',
    )
    include_daily = st.checkbox(
        '包含每日明細工作表',
        value=True,
        help='每個時段一張「每日_」工作表：列出系統、路口（與旅行時間）的每日數值，'
             '並依「每日分析」分頁的「異常門檻」設定標示明顯較差／較好的日期',
    )
    include_deg = st.checkbox(
        '包含降階紀錄工作表',
        value=True,
        help='「降階紀錄」工作表：本次分析事前／事後日期的每日降階次數、小時制次數、降階分鐘與主要原因，'
             '並附兩組合計與日平均（降階為整天數字，不分時段）',
    )
    if st.button('產生 Excel 報告'):
        with st.spinner('產生 Excel 中…'):
            buf = eb.build_comparison_xlsx(
                all_results, bd_by_period, ad_by_period,
                include_travel_time=inc_tt,
                raw_df=df if include_raw else None,
                raw_periods=periods if include_raw else None,
                extra_summary_entities=extra_entities,
                day_type_label='假日' if is_weekend else '平日',
                daily_df=df if include_daily else None,
                daily_k=st.session_state['daily_k'],
                degradation_df=dl.load_degradation(DEGRADATION_PATH) if include_deg else None,
            )
        ts = datetime.now().strftime('%Y%m%d_%H%M')
        st.download_button(
            label='⬇️ 下載 Excel 報告',
            data=buf,
            file_name=f'績效比較_{selected_site}_{ts}.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
