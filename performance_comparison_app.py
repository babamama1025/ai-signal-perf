"""AI 號誌事前後分析系統 (Streamlit 應用程式)"""
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

# ── 場域預設時段（平日 / 假日）─────────────────────────────────────────────
# 修改此區塊可調整各場域的預設勾選時段
SITE_DEFAULTS = {
    '桃園四期(大湳)': {
        'periods_weekday': ['07:00~09:00', '14:00~16:00', '16:00~19:00'],
        'periods_weekend': ['10:00~12:00', '16:00~19:00'],
    },
    '桃園三期(高鐵)': {
        'periods_weekday': ['07:00~09:00', '14:00~16:00', '16:00~19:00'],
        'periods_weekend': ['10:00~12:00', '16:00~19:00'],
    },
}

# ── 路徑設定 ────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
LOG_COLS  = ['日期', '時段', '狀態', '備註']

# 各場域獨立的 AI 操作紀錄檔（可用 Excel 直接開啟編輯）
LOG_PATHS = {
    '桃園四期(大湳)': 'ai_operation_log_4.csv',
    '桃園三期(高鐵)': 'ai_operation_log_3.csv',
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


def _display_overview_table(results: dict, inc_tt: bool):
    """系統層級改善率概覽表格（替代原概覽長條圖）。"""
    base_metrics = ['總停等延滯', '通過量', '平均停等延滯']
    row_labels   = ['事前平均', '事後平均', '差異', '改善(%)']

    col_raw: dict[str, dict] = {}

    for metric in base_metrics:
        mdf = results.get(metric, pd.DataFrame())
        if not mdf.empty:
            sys_row = mdf[mdf['欄位'] == '系統']
            if not sys_row.empty:
                col_raw[metric] = {
                    '事前平均': sys_row['事前平均'].values[0],
                    '事後平均': sys_row['事後平均'].values[0],
                    '差異':     sys_row['差異'].values[0],
                    '改善(%)':  sys_row['改善%'].values[0],
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
        is_vol = (col_name == '通過量')
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


def _save_log(log_df: pd.DataFrame):
    log_df.to_csv(LOG_PATH, index=False, encoding='utf-8-sig')


def _validate_log_periods(log_df: pd.DataFrame, all_periods: list[str]) -> list[str]:
    """檢查操作紀錄的「時段」欄是否為合法值，回傳錯誤訊息列表（空list代表通過）。
    時段、狀態皆空白的列視為純備註列（如颱風停班說明），不強制檢查。"""
    valid = {'全天', *all_periods}
    errors = []
    for i, (period, status) in enumerate(zip(log_df['時段'], log_df['狀態']), start=1):
        period = (period or '').strip()
        status = (status or '').strip()
        if not period and not status:
            continue
        if not period:
            errors.append(f'第 {i} 列：已填寫狀態但時段未填寫')
        elif period not in valid:
            errors.append(f'第 {i} 列：「{period}」不是有效時段（需為「全天」或：{"、".join(all_periods)}）')
    return errors


def _period_status_map(log_df: pd.DataFrame, periods: list[str]) -> dict[tuple[str, str], str]:
    """由 AI 操作紀錄建立 {(日期, 時段): 狀態} 對照表（僅限 periods 內的時段）。
    「全天」列先展開套用到 periods 內所有時段，同日期同時段的個別列再覆蓋（較明確者優先）。"""
    status: dict[tuple[str, str], str] = {}
    records = log_df.to_dict('records')
    for r in records:
        if r.get('時段') == '全天' and r.get('狀態') in ('啟動', '關閉'):
            for p in periods:
                status[(r['日期'], p)] = r['狀態']
    for r in records:
        if r.get('時段') in periods and r.get('狀態') in ('啟動', '關閉'):
            status[(r['日期'], r['時段'])] = r['狀態']
    return status


# ── 資料載入（快取，依路徑分別快取）────────────────────────────────────────
@st.cache_data(show_spinner='載入績效資料中…')
def _load_df(csv_path: str):
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
CSV_PATH = BASE_DIR / SITE_OPTIONS[selected_site]
LOG_PATH = BASE_DIR / LOG_PATHS[selected_site]

if not CSV_PATH.exists():
    st.error(f'找不到資料檔案：{CSV_PATH.name}，請確認已上傳至正確位置。')
    st.stop()

df, col_struct = _load_df(str(CSV_PATH))
dl._col_structure = col_struct          # 還原模組層級快取（繞過 st.cache_data）

all_dates   = dl.get_available_dates(df)
all_periods = dl.get_available_periods(df)
system_cols = dl.get_system_columns()


# ── 日期表格建立輔助函式 ─────────────────────────────────────────────────────
def _make_date_period_df(periods: list[str], log_df: pd.DataFrame) -> pd.DataFrame:
    """
    建立「日期 × 時段」分配表（長格式，每列為一個日期＋時段組合）。
    狀態完全依 AI 操作紀錄判斷：啟動 → 事後；關閉或無紀錄 → 事前
    （無操作紀錄視為預設開啟定時時制 TOD）。
    """
    status_map = _period_status_map(log_df, periods)
    rows = []
    for d in all_dates:
        d_str = d.strftime('%Y/%m/%d')
        for p in periods:
            status = status_map.get((d_str, p), '關閉')
            rows.append({
                '日期': d_str,
                '星':   dl.TW_WEEKDAY[d.weekday()],
                '時段': p,
                '事前': status == '關閉',
                '事後': status == '啟動',
            })
    return pd.DataFrame(rows, columns=['日期', '星', '時段', '事前', '事後'])


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

    selected_periods = st.multiselect(
        '選擇時段（可多選）',
        options=all_periods,
        default=default_periods,
        key=f'periods_{selected_site}_{day_type}',   # 切換場域或日期類型時自動重置
    )

    # 時段選擇改變時，依 AI 操作紀錄重新建立日期×時段分配表
    periods_key = (selected_site, tuple(sorted(selected_periods)))
    if st.session_state.get('_periods_key') != periods_key:
        st.session_state['_periods_key'] = periods_key
        st.session_state['date_df'] = _make_date_period_df(selected_periods, _load_log())
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

    if st.button('↺ 重置為 AI 操作紀錄預設值', use_container_width=True):
        st.session_state['date_df'] = _make_date_period_df(selected_periods, _load_log())
        st.session_state['editor_ver'] += 1
        st.rerun()

    edited_df = st.data_editor(
        st.session_state['date_df'],
        key=f'date_editor_{st.session_state["editor_ver"]}',
        column_config={
            '日期': st.column_config.TextColumn('日期', disabled=True, width='small'),
            '星':  st.column_config.TextColumn('星', disabled=True, width='small'),
            '時段': st.column_config.TextColumn('時段', disabled=True, width='medium'),
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
            f"{p}：事前{len(before_by_period[p])}／事後{len(after_by_period[p])}"
            for p in selected_periods
        )
        st.caption(f'已選：{counts_str}')

    st.divider()

    # 選項
    st.subheader('⚙️ 選項')
    include_tt  = st.checkbox('包含旅行時間', value=True)
    show_detail = st.checkbox('顯示各路口各方向明細', value=False)

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
        st.caption(f'⚠️ 以下時段缺少事前或事後日期，分析結果將留白：{"、".join(missing)}')

# ── 主畫面 ──────────────────────────────────────────────────────────────────
st.title('🚦 AI 號誌事前後分析系統')
st.subheader(f'場域：{selected_site}')

# ── AI 操作紀錄編輯器 ─────────────────────────────────────────────────────────
with st.expander('📋 AI 操作紀錄', expanded=False):
    st.caption(
        f'紀錄檔：`{LOG_PATH.name}`（與程式同目錄，可用 Excel 直接開啟修改）  \n'
        '**狀態**欄：啟動 = AI 號誌運行中；關閉 = 退回定時時制（請填備註說明原因）  \n'
        f'**時段**欄請填「全天」或以下其中之一：{"、".join(all_periods)}'
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
        errors = _validate_log_periods(edited_log, all_periods)
        if errors:
            st.error('時段欄位有誤，請修正後再儲存：\n' + '\n'.join(f'- {e}' for e in errors))
        else:
            _save_log(edited_log)
            st.success(f'已儲存 → {LOG_PATH}')

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

# ── 概覽表 ────────────────────────────────────────────────────────────────────
st.subheader('📊 各時段系統層級改善率概覽')
for period, results in all_results.items():
    if len(all_results) > 1:
        st.markdown(f"**⏱ {period}**")
    _display_overview_table(results, inc_tt)

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
        for metric in ['總停等延滯', '通過量', '平均停等延滯']:
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
        )
    ts = datetime.now().strftime('%Y%m%d_%H%M')
    st.download_button(
        label='⬇️ 下載 Excel 報告',
        data=buf,
        file_name=f'績效比較_{selected_site}_{ts}.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
