"""AI 號誌事前後分析系統 (Streamlit 應用程式)"""
import re
import json
import base64
import requests
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime, date, timedelta

import data_loader as dl
import comparison_logic as cl
import chart_builder as cb
import export_builder as eb

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
LOG_COLS  = ['日期', '時段', '狀態', '備註']

# 各場域獨立的 AI 操作紀錄檔（可用 Excel 直接開啟編輯）
LOG_PATHS = {
    '桃園四期(大湳)': 'ai_operation_log_4.csv',
    '桃園三期(高鐵)': 'ai_operation_log_3.csv',
}

# 各場域獨立的已儲存日期分配檔
SELECTION_PATHS = {
    '桃園四期(大湳)': 'date_selections_4.json',
    '桃園三期(高鐵)': 'date_selections_3.json',
}

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


def _display_overview_table(results: dict, inc_tt: bool, extra_entities: list[str] | None = None):
    """系統層級改善率概覽表格（替代原概覽長條圖）。
    extra_entities：桃園三期等場域需額外顯示的系統層級欄位（如高鐵周邊、A19周邊、前期範圍）。
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


# ── 操作紀錄 I/O ─────────────────────────────────────────────────────────────
def _load_log() -> pd.DataFrame:
    if LOG_PATH.exists():
        try:
            return pd.read_csv(LOG_PATH, dtype=str).fillna('')
        except Exception:
            pass
    return pd.DataFrame(columns=LOG_COLS)


def _save_log(log_df: pd.DataFrame) -> bool:
    """儲存至本機並同步 GitHub，回傳 GitHub 同步是否成功。"""
    log_df.to_csv(LOG_PATH, index=False, encoding='utf-8-sig')
    return _github_push_file(
        LOG_PATH.relative_to(BASE_DIR).as_posix(),
        LOG_PATH.read_bytes(),
        f'auto: update {LOG_PATH.name}',
    )


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


def _parse_period(period: str) -> tuple[int, int] | None:
    """解析「HH:MM~HH:MM」為 (起始分鐘, 結束分鐘)；格式錯誤或起始未早於結束則回傳 None。"""
    m = re.match(r'^(\d{1,2}):(\d{2})~(\d{1,2}):(\d{2})$', period.strip())
    if not m:
        return None
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if not (0 <= h1 <= 24 and 0 <= m1 < 60 and 0 <= h2 <= 24 and 0 <= m2 < 60):
        return None
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if start >= end:
        return None
    return start, end


def _validate_log_periods(log_df: pd.DataFrame) -> list[str]:
    """檢查操作紀錄的「時段」欄是否為合法值，回傳錯誤訊息列表（空list代表通過）。
    時段、狀態皆空白的列視為純備註列（如颱風停班說明），不強制檢查。
    時段可為「全天」或任意「HH:MM~HH:MM」時間範圍（不限於分析時段清單）。"""
    errors = []
    for i, (period, status) in enumerate(zip(log_df['時段'], log_df['狀態']), start=1):
        period = (period or '').strip()
        status = (status or '').strip()
        if not period and not status:
            continue
        if not period:
            errors.append(f'第 {i} 列：已填寫狀態但時段未填寫')
        elif period != '全天' and _parse_period(period) is None:
            errors.append(f'第 {i} 列：「{period}」不是有效時段（需為「全天」或 HH:MM~HH:MM 格式，且起始時間需早於結束時間）')
    return errors


def _normalize_log_date(date_str: str) -> str:
    """將操作紀錄中的日期字串標準化為 YYYY/MM/DD（補零），以便與日期分配表的鍵值比對。
    無法解析時原樣回傳。"""
    try:
        return pd.Timestamp(str(date_str).strip()).strftime('%Y/%m/%d')
    except (ValueError, TypeError):
        return str(date_str).strip()


def _period_status_map(log_df: pd.DataFrame, periods: list[str]) -> dict[tuple[str, str], str]:
    """由 AI 操作紀錄建立 {(日期, 時段): 狀態} 對照表（僅限 periods 內的分析時段）。
    「全天」列先展開套用到 periods 內所有時段（最低優先權）；
    其餘列若其時間範圍完整涵蓋某分析時段，則覆蓋該時段狀態（較明確者優先）。"""
    period_ranges = {p: _parse_period(p) for p in periods}
    status: dict[tuple[str, str], str] = {}
    records = log_df.to_dict('records')
    for r in records:
        if r.get('時段') == '全天' and r.get('狀態') in ('啟動', '關閉'):
            d = _normalize_log_date(r['日期'])
            for p in periods:
                status[(d, p)] = r['狀態']
    for r in records:
        r_period = (r.get('時段') or '').strip()
        if r_period == '全天' or r.get('狀態') not in ('啟動', '關閉'):
            continue
        r_range = _parse_period(r_period)
        if r_range is None:
            continue
        r_start, r_end = r_range
        d = _normalize_log_date(r['日期'])
        for p in periods:
            p_range = period_ranges[p]
            if p_range is not None and r_start <= p_range[0] and p_range[1] <= r_end:
                status[(d, p)] = r['狀態']
    return status


# ── 資料載入（快取，依路徑分別快取）────────────────────────────────────────
@st.cache_data(show_spinner='載入績效資料中…')
def _load_df(csv_path: str, _mtime: float):
    df = dl.load_performance_csv(Path(csv_path))
    return df, dict(dl.get_column_structure())


# ── 場域選擇（側邊欄最頂部，資料載入前先取得選擇）──────────────────────────
st.sidebar.title('🚦 分析設定')
st.sidebar.subheader('🗺️ 場域選擇')
selected_site = st.sidebar.selectbox('選擇分析場域', list(SITE_OPTIONS.keys()))
st.sidebar.divider()

# 偵測場域切換，重置日期與分析結果
if st.session_state.get('_current_site') != selected_site:
    st.session_state['_current_site'] = selected_site
    st.session_state.pop('date_df', None)
    st.session_state.pop('analysis_results', None)
    st.session_state['editor_ver'] = st.session_state.get('editor_ver', 0) + 1

# 載入當前場域資料
CSV_PATH = DATA_DIR / SITE_OPTIONS[selected_site]
LOG_PATH = DATA_DIR / LOG_PATHS[selected_site]
SELECTION_PATH = DATA_DIR / SELECTION_PATHS[selected_site]

if not CSV_PATH.exists():
    st.error(f'找不到資料檔案：{CSV_PATH.name}，請確認已上傳至正確位置。')
    st.stop()

df, col_struct = _load_df(str(CSV_PATH), CSV_PATH.stat().st_mtime)
dl._col_structure = col_struct          # 還原模組層級快取（繞過 st.cache_data）

all_dates      = dl.get_available_dates(df)
all_periods    = dl.get_available_periods(df)
system_cols    = dl.get_system_columns()
extra_entities = SITE3_EXTRA_ENTITIES if selected_site == '桃園三期(高鐵)' else []


# ── 日期表格建立輔助函式 ─────────────────────────────────────────────────────
def _make_date_period_df(periods: list[str], log_df: pd.DataFrame,
                          is_weekend: bool = False, filter_by_type: bool = True) -> pd.DataFrame:
    """
    建立「日期 × 時段」分配表（長格式，每列為一個日期＋時段組合）。
    狀態完全依 AI 操作紀錄判斷：啟動 → 事後；關閉或無紀錄 → 事前
    （無操作紀錄視為預設開啟定時時制 TOD）。
    filter_by_type：True 時依 is_weekend 過濾日期；False 時顯示全部日期。
    """
    status_map = _period_status_map(log_df, periods)
    rows = []
    for d in all_dates:
        if filter_by_type and (d.weekday() >= 5) != is_weekend:
            continue
        d_str = d.strftime('%Y/%m/%d')
        for p in periods:
            status = status_map.get((d_str, p), '關閉')
            rows.append({
                '日期': d_str,
                '星期': dl.TW_WEEKDAY[d.weekday()],
                '時段': p,
                '事前': status == '關閉',
                '事後': status == '啟動',
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
    st.caption(
        f'資料更新：{datetime.fromtimestamp(CSV_PATH.stat().st_mtime).strftime("%Y/%m/%d")}'
    )
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

    # 時段選擇改變時，依 AI 操作紀錄重新建立日期×時段分配表
    periods_key = (selected_site, day_type, tuple(sorted(selected_periods)), _filter_by_day_type)
    if st.session_state.get('_periods_key') != periods_key:
        st.session_state['_periods_key'] = periods_key
        st.session_state['date_df'] = _make_date_period_df(
            selected_periods, _load_log(), is_weekend, _filter_by_day_type,
        )
        st.session_state['editor_ver'] = st.session_state.get('editor_ver', 0) + 1

    st.divider()

    # ── 自動篩選日期（依 AI 操作紀錄）───────────────────────────────────
    st.subheader('🔍 自動篩選日期')
    st.caption('依 AI 操作紀錄，將指定日期範圍與日期類型內的天數自動歸類；範圍外或類型不符的天數會取消勾選。')

    range_opt = st.selectbox(
        '日期範圍',
        ['最近 2 週', '最近 1 個月', '最近 3 個月', '自訂區間'],
        label_visibility='collapsed',
        key='range_opt',
    )
    today = date.today()
    if range_opt == '自訂區間':
        col_a, col_b = st.columns(2)
        range_start = col_a.date_input('起始', value=today - timedelta(days=30), key='range_start')
        range_end   = col_b.date_input('結束', value=today, key='range_end')
    else:
        days_map    = {'最近 2 週': 14, '最近 1 個月': 30, '最近 3 個月': 90}
        range_start = today - timedelta(days=days_map[range_opt])
        range_end   = today

    if st.button('⚡ 依操作紀錄自動填入', use_container_width=True):
        status_map = _period_status_map(_load_log(), selected_periods)
        ts_start = pd.Timestamp(range_start)
        ts_end   = pd.Timestamp(range_end)

        new_df = st.session_state['date_df'].copy()
        for idx, row in new_df.iterrows():
            d = pd.Timestamp(row['日期'])
            in_scope = (ts_start <= d <= ts_end) and ((d.weekday() >= 5) == is_weekend)
            if in_scope:
                status = status_map.get((row['日期'], row['時段']), '關閉')
                new_df.at[idx, '事前'] = (status == '關閉')
                new_df.at[idx, '事後'] = (status == '啟動')
            else:
                new_df.at[idx, '事前'] = False
                new_df.at[idx, '事後'] = False

        st.session_state['date_df'] = new_df
        st.session_state['editor_ver'] += 1
        st.rerun()

    st.divider()

    # ── 日期分配表格 ────────────────────────────────────────────────────────
    st.subheader('📆 日期分配')
    st.caption(
        '每列為一組「日期＋時段」，直接勾選歸入「事前」或「事後」。'
        '預設依 AI 操作紀錄判斷：無紀錄視為開啟定時時制（事前）。'
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

    edited_df = st.data_editor(
        st.session_state['date_df'],
        key=f'date_editor_{st.session_state["editor_ver"]}',
        column_config={
            '日期': st.column_config.TextColumn('日期', disabled=True, width='small'),
            '星期': st.column_config.TextColumn('星期', disabled=True, width='small'),
            '時段': st.column_config.TextColumn('時段', disabled=True, width='small'),
            '事前': st.column_config.CheckboxColumn('事前', width='small'),
            '事後': st.column_config.CheckboxColumn('事後', width='small'),
        },
        hide_index=True,
        height=400,
        use_container_width=True,
    )

    before_by_period: dict[str, list] = {}
    after_by_period:  dict[str, list] = {}
    for p in selected_periods:
        sub = edited_df[edited_df['時段'] == p]
        before_by_period[p] = [pd.Timestamp(r) for r in sub.loc[sub['事前'], '日期']]
        after_by_period[p]  = [pd.Timestamp(r) for r in sub.loc[sub['事後'], '日期']]

    if selected_periods:
        counts_str = '　'.join(
            f"{_esc_md(p)}：事前{len(before_by_period[p])}／事後{len(after_by_period[p])}"
            for p in selected_periods
        )
        st.caption(f'已選：{counts_str}')

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

        st.session_state['day_type_radio'] = preset['day_type']
        # 同步更新 day_type tracker，避免 rerun 時觸發「切換日期類型→重設時段」的保護邏輯
        st.session_state[f'_day_type_{selected_site}'] = preset['day_type']
        periods_widget_key = f"periods_{selected_site}_{preset['day_type']}"
        st.session_state[periods_widget_key] = valid_periods
        st.session_state['_periods_key'] = (selected_site, preset['day_type'], tuple(sorted(valid_periods)), filter_by_type)
        st.session_state['date_df'] = base_df
        st.session_state['_save_load_msg'] = (
            f'✅ 已載入「{preset["name"]}」'
            f'（{preset["day_type"]}，儲存於 {preset["saved_at"]}）'
        )
        st.session_state['editor_ver'] = st.session_state.get('editor_ver', 0) + 1

    if saved_selections:
        preset_names = [s['name'] for s in saved_selections]
        st.selectbox('選擇已儲存的分配', preset_names, key='load_selection_name')
        st.button('📂 載入所選分配', use_container_width=True, on_click=_apply_load_preset)
    else:
        st.caption('尚無已儲存的日期分配')

    if msg := st.session_state.pop('_save_load_msg', None):
        st.success(msg)

    st.divider()

    # ── 編輯日期分配 ─────────────────────────────────────────────────
    st.subheader('✏️ 編輯日期分配')

    if msg := st.session_state.pop('_edit_msg', None):
        st.success(msg)

    if saved_selections:
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
    show_detail = st.checkbox('顯示各路口各方向明細', value=True)

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
            compare_cols = dl.get_column_structure().get('all_data', system_cols) if show_detail else system_cols
            all_results  = {}
            for period in selected_periods:
                all_results[period] = cl.compute_comparison(
                    df, period, before_by_period[period], after_by_period[period],
                    compare_cols, include_travel_time=include_tt,
                )
            st.session_state['analysis_results'] = {
                'results':          all_results,
                'before_by_period': before_by_period,
                'after_by_period':  after_by_period,
                'periods':          selected_periods,
                'include_tt':       include_tt,
            }
        st.success('分析完成！')

    if run_disabled:
        st.caption('⚠️ 請先選擇時段，且至少一個時段同時有事前與事後日期')
    elif len(usable_periods) < len(selected_periods):
        missing = [p for p in selected_periods if p not in usable_periods]
        st.caption(f'⚠️ 以下時段缺少事前或事後日期，分析結果將留白：{"、".join(_esc_md(p) for p in missing)}')

# ── 主畫面 ──────────────────────────────────────────────────────────────────
st.title('🚦 AI 號誌事前後分析系統')
st.subheader(f'場域：{selected_site}')

# ── AI 操作紀錄編輯器 ─────────────────────────────────────────────────────────
with st.expander('📋 AI 操作紀錄', expanded=False):
    st.caption(
        f'紀錄檔：`{LOG_PATH.name}`（位於 data 資料夾，可用 Excel 直接開啟修改）  \n'
        '**狀態**欄：啟動 = AI 號誌運行中；關閉 = 退回定時時制（請填備註說明原因）  \n'
        '**時段**欄請填「全天」或任意「HH:MM~HH:MM」時間範圍（不限於分析時段清單，'
        '系統會自動判斷分析時段是否完整落在此範圍內）'
    )
    log_df_ui = _load_log()
    edited_log = st.data_editor(
        log_df_ui,
        column_config={
            '日期': st.column_config.TextColumn('日期 (YYYY/MM/DD)', width='small'),
            '時段': st.column_config.TextColumn(
                '時段（全天 或 HH:MM~HH:MM）', width='medium'
            ),
            '狀態': st.column_config.SelectboxColumn(
                '狀態', options=['啟動', '關閉'], width='small'
            ),
            '備註': st.column_config.TextColumn(
                '備註（如：設備維護、颱風停班）', width='large'
            ),
        },
        num_rows='dynamic',
        use_container_width=True,
        hide_index=True,
        key='log_editor',
    )
    if st.button('💾 儲存操作紀錄', key='save_log'):
        errors = _validate_log_periods(edited_log)
        if errors:
            st.error('時段欄位有誤，請修正後再儲存：\n' + '\n'.join(f'- {e}' for e in errors))
        else:
            synced = _save_log(edited_log)
            suffix = '，已同步至 GitHub ✓' if synced else _github_sync_suffix()
            st.session_state['_log_msg'] = f'✅ 已儲存 {LOG_PATH.name}{suffix}'
            st.rerun()

    if msg := st.session_state.pop('_log_msg', None):
        st.success(msg)

if st.session_state['analysis_results'] is None:
    st.info('請在左側設定分析條件後，點擊「執行分析」按鈕。')
    st.markdown("""
**使用步驟：**
1. 左側選擇「場域」（桃園四期大湳 或 桃園三期高鐵）
2. 選擇「日期類型」（平常日 / 週末）—— 分析時段會自動切換
3. 「日期分配」表已依「AI 操作紀錄」自動判斷各日期＋時段的事前／事後（無紀錄視為定時時制／事前），
   可視需要用「⚡ 依操作紀錄自動填入」限縮至特定日期範圍，或直接手動勾選
4. 視需要勾選「包含旅行時間」、「顯示各路口各方向明細」
5. 點擊「執行分析」
""")
    st.stop()

# 取出分析結果
saved           = st.session_state['analysis_results']
all_results     = saved['results']
bd_by_period    = saved['before_by_period']
ad_by_period    = saved['after_by_period']
periods         = saved['periods']
inc_tt          = saved['include_tt']

# ── 頂部摘要指標（各時段事前／事後天數可能不同，逐時段列出）──────────────────
summary_rows = [
    {'時段': p, '事前日數': len(bd_by_period[p]), '事後日數': len(ad_by_period[p])}
    for p in periods
]
st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

# ── 全時段合計概覽 ────────────────────────────────────────────────────────────
st.subheader('📊 系統層級改善率概覽')
st.caption('整合所有已選時段：總停等延滯／通過量採加總計算，平均停等延滯由加總後重新推導，旅行時間採各時段平均。')
overview_all = cl.aggregate_periods(all_results, periods, include_travel_time=inc_tt, extra_entities=extra_entities)
_display_overview_table(overview_all, inc_tt, extra_entities)

st.divider()

# ── 概覽表 ────────────────────────────────────────────────────────────────────
st.subheader('📊 各時段系統層級改善率概覽')
for period, results in all_results.items():
    if len(all_results) > 1:
        st.markdown(f"**⏱ {period}**")
    _display_overview_table(results, inc_tt, extra_entities)

# ── 分析摘要 ──────────────────────────────────────────────────────────────────
with st.expander('📝 分析摘要（展開）', expanded=True):
    before_counts = {p: len(bd_by_period[p]) for p in periods}
    after_counts  = {p: len(ad_by_period[p]) for p in periods}
    st.markdown(cl.generate_analysis_text(all_results, before_counts, after_counts))

st.divider()

# ── 分頁：每個時段一個分頁 ────────────────────────────────────────────────────
tab_containers = st.tabs(periods) if len(periods) > 1 else [st.container()]

for i, period in enumerate(periods):
    results = all_results.get(period, {})
    with tab_containers[i]:
        for metric in ['平均停等延滯', '總停等延滯', '通過量']:
            comp_df = results.get(metric, pd.DataFrame())
            st.subheader(f'📊 {metric}')
            if comp_df.empty:
                st.warning(f'無 {metric} 資料')
                continue

            display_df, raw_pct = _format_comp_df(comp_df, metric)
            st.dataframe(
                display_df.style.apply(_highlight_pct_col, axis=0, raw_pct=raw_pct),
                use_container_width=True,
                hide_index=True,
            )
            st.plotly_chart(
                cb.make_metric_bar_chart(comp_df, metric, period),
                use_container_width=True,
            )

        if inc_tt:
            tt_df = results.get('旅行時間', pd.DataFrame())
            if not tt_df.empty:
                st.subheader('🛣️ 旅行時間')
                disp_tt, raw_pct_tt = _format_comp_df(tt_df, '旅行時間')
                st.dataframe(
                    disp_tt.style.apply(_highlight_pct_col, axis=0, raw_pct=raw_pct_tt),
                    use_container_width=True,
                    hide_index=True,
                )
                st.plotly_chart(
                    cb.make_travel_time_chart(tt_df, period),
                    use_container_width=True,
                )

# ── Excel 匯出 ────────────────────────────────────────────────────────────────
st.divider()
st.subheader('💾 匯出報告')
include_raw = st.checkbox(
    '包含原始資料工作表',
    value=False,
    help='勾選後，Excel 報告末頁會加入「原始資料」工作表，列出所選日期的完整數據',
)
if st.button('產生 Excel 報告'):
    with st.spinner('產生 Excel 中…'):
        buf = eb.build_comparison_xlsx(
            all_results, bd_by_period, ad_by_period,
            include_travel_time=inc_tt,
            raw_df=df if include_raw else None,
            raw_periods=periods if include_raw else None,
            extra_summary_entities=extra_entities,
        )
    ts = datetime.now().strftime('%Y%m%d_%H%M')
    st.download_button(
        label='⬇️ 下載 Excel 報告',
        data=buf,
        file_name=f'績效比較_{selected_site}_{ts}.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
