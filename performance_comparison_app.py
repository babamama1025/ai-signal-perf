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
XLSX_PATH = next(BASE_DIR.glob('=*績效*.xlsx'), None)
LOG_PATH  = BASE_DIR / 'ai_operation_log.csv'   # AI 操作紀錄（可用 Excel 直接開啟編輯）
LOG_COLS  = ['日期', '時段', '狀態', '備註']

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


# ── 資料載入（快取，依路徑分別快取）────────────────────────────────────────
@st.cache_data(show_spinner='載入績效資料中…')
def _load_df(csv_path: str):
    df = dl.load_performance_csv(Path(csv_path))
    return df, dict(dl.get_column_structure())

@st.cache_data(show_spinner='讀取測試日分類中…')
def _load_classification():
    if XLSX_PATH is None:
        return {}
    try:
        return dl.load_test_day_classification(XLSX_PATH)
    except Exception as e:
        st.warning(f'無法讀取測試日工作表：{e}')
        return {}


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

if not CSV_PATH.exists():
    st.error(f'找不到資料檔案：{CSV_PATH.name}，請確認已上傳至正確位置。')
    st.stop()

df, col_struct = _load_df(str(CSV_PATH))
dl._col_structure = col_struct          # 還原模組層級快取（繞過 st.cache_data）

all_dates   = dl.get_available_dates(df)
all_periods = dl.get_available_periods(df)
system_cols = dl.get_system_columns()
cls_map     = _load_classification()


# ── 日期表格建立輔助函式 ─────────────────────────────────────────────────────
def _make_date_df(before_set: set[str] | None = None,
                  after_set:  set[str] | None = None) -> pd.DataFrame:
    """
    建立日期分配 DataFrame。
    before_set / after_set 若提供（格式 'YYYY/MM/DD'），覆蓋 XLSX 自動分類；
    不提供時依 cls_map 判斷。
    """
    rows = []
    for d in all_dates:
        key   = d.strftime('%Y-%m-%d')
        d_str = d.strftime('%Y/%m/%d')
        c     = cls_map.get(key, '未知')
        is_before = (d_str in before_set) if before_set is not None else (c == 'FIX')
        is_after  = (d_str in after_set)  if after_set  is not None else (c == 'AI')
        rows.append({
            '日期': d_str,
            '星':   dl.TW_WEEKDAY[d.weekday()],
            '分類': c,
            '事前': is_before,
            '事後': is_after,
        })
    return pd.DataFrame(rows)


# ── Session State 初始化 ────────────────────────────────────────────────────
if 'date_df' not in st.session_state:
    st.session_state['date_df'] = _make_date_df()
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

    st.divider()

    # ── 自動篩選日期（依 AI 操作紀錄）───────────────────────────────────
    st.subheader('🔍 自動篩選日期')
    st.caption('依 AI 操作紀錄與日期範圍，自動填入事前後勾選。')

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
        log_df   = _load_log()
        log_dict = dict(zip(log_df['日期'], log_df['狀態']))  # 'YYYY/MM/DD' → '啟動'/'關閉'

        new_before: set[str] = set()
        new_after:  set[str] = set()
        ts_start = pd.Timestamp(range_start)
        ts_end   = pd.Timestamp(range_end)

        for d in all_dates:
            if not (ts_start <= d <= ts_end):
                continue
            if is_weekend and d.weekday() < 5:      # 週末模式：跳過平日
                continue
            if not is_weekend and d.weekday() >= 5:  # 平日模式：跳過週末
                continue
            d_str  = d.strftime('%Y/%m/%d')
            status = log_dict.get(d_str)
            if status == '啟動':
                new_after.add(d_str)
            elif status == '關閉':
                new_before.add(d_str)
            else:
                # 無操作紀錄：退回 XLSX 分類
                c = cls_map.get(d.strftime('%Y-%m-%d'), '未知')
                if c == 'AI':
                    new_after.add(d_str)
                elif c == 'FIX':
                    new_before.add(d_str)

        new_df = st.session_state['date_df'].copy()
        new_df['事前'] = new_df['日期'].isin(new_before)
        new_df['事後'] = new_df['日期'].isin(new_after)
        st.session_state['date_df'] = new_df
        st.session_state['editor_ver'] += 1
        st.rerun()

    st.divider()

    # ── 日期分配表格 ────────────────────────────────────────────────────────
    st.subheader('📆 日期分配')
    st.caption('直接勾選每天歸入「事前」或「事後」。預設依測試日工作表自動分類。')

    if st.button('↺ 重置為測試日分類', use_container_width=True):
        st.session_state['date_df'] = _make_date_df()
        st.session_state['editor_ver'] += 1

    edited_df = st.data_editor(
        st.session_state['date_df'],
        key=f'date_editor_{st.session_state["editor_ver"]}',
        column_config={
            '日期': st.column_config.TextColumn('日期', disabled=True, width='small'),
            '星':  st.column_config.TextColumn('星', disabled=True, width='small'),
            '分類': st.column_config.TextColumn('分類', disabled=True, width='small'),
            '事前': st.column_config.CheckboxColumn('事前', width='small'),
            '事後': st.column_config.CheckboxColumn('事後', width='small'),
        },
        hide_index=True,
        height=400,
        use_container_width=True,
    )

    before_dates = [pd.Timestamp(r) for r in edited_df.loc[edited_df['事前'], '日期']]
    after_dates  = [pd.Timestamp(r) for r in edited_df.loc[edited_df['事後'], '日期']]
    n_unk = int((edited_df['分類'] == '未知').sum())

    st.caption(
        f'已選：事前 **{len(before_dates)}** 日 ／ 事後 **{len(after_dates)}** 日'
        + (f'  ⚠️ 另有 {n_unk} 天未分類' if n_unk else '')
    )

    st.divider()

    # 選項
    st.subheader('⚙️ 選項')
    include_tt  = st.checkbox('包含旅行時間', value=True)
    show_detail = st.checkbox('顯示各路口各方向明細', value=False)

    st.divider()

    run_disabled = (not selected_periods or not before_dates or not after_dates)
    if st.button('🔍 執行分析', type='primary', use_container_width=True, disabled=run_disabled):
        with st.spinner('計算中…'):
            compare_cols = dl.get_column_structure().get('all_data', system_cols) if show_detail else system_cols
            all_results  = {}
            for period in selected_periods:
                all_results[period] = cl.compute_comparison(
                    df, period, before_dates, after_dates,
                    compare_cols, include_travel_time=include_tt,
                )
            st.session_state['analysis_results'] = {
                'results':      all_results,
                'before_dates': before_dates,
                'after_dates':  after_dates,
                'periods':      selected_periods,
                'include_tt':   include_tt,
            }
        st.success('分析完成！')

    if run_disabled:
        st.caption('⚠️ 請先選擇時段與事前／事後日期')

# ── 主畫面 ──────────────────────────────────────────────────────────────────
st.title(f'🚦 AI 號誌事前後分析系統 ─ {selected_site}')

# ── AI 操作紀錄編輯器 ─────────────────────────────────────────────────────────
with st.expander('📋 AI 操作紀錄', expanded=False):
    st.caption(
        f'紀錄檔：`{LOG_PATH.name}`（與程式同目錄，可用 Excel 直接開啟修改）  \n'
        '**狀態**欄：啟動 = AI 號誌運行中；關閉 = 退回定時時制（請填備註說明原因）'
    )
    log_df_ui = _load_log()
    edited_log = st.data_editor(
        log_df_ui,
        column_config={
            '日期': st.column_config.TextColumn('日期 (YYYY/MM/DD)', width='small'),
            '時段': st.column_config.SelectboxColumn(
                '時段', options=['全天'] + list(all_periods), width='medium'
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
        _save_log(edited_log)
        st.success(f'已儲存 → {LOG_PATH}')

if st.session_state['analysis_results'] is None:
    st.info('請在左側設定分析條件後，點擊「執行分析」按鈕。')
    st.markdown("""
**使用步驟：**
1. 左側選擇「場域」（桃園四期大湳 或 桃園三期高鐵）
2. 選擇「日期類型」（平常日 / 週末）—— 分析時段會自動切換
3. 可點「⚡ 依操作紀錄自動填入」依紀錄篩選日期，或手動勾選事前／事後日期
4. 視需要勾選「包含旅行時間」、「顯示各路口各方向明細」
5. 點擊「執行分析」
""")
    st.stop()

# 取出分析結果
saved       = st.session_state['analysis_results']
all_results = saved['results']
bd          = saved['before_dates']
ad          = saved['after_dates']
periods     = saved['periods']
inc_tt      = saved['include_tt']

# ── 頂部摘要指標 ──────────────────────────────────────────────────────────────
before_help  = "事前日期（共 {} 日）：\n".format(len(bd)) + \
               "\n".join(f"• {d.strftime('%Y/%m/%d')} (週{dl.TW_WEEKDAY[d.weekday()]})" for d in bd)
after_help   = "事後日期（共 {} 日）：\n".format(len(ad)) + \
               "\n".join(f"• {d.strftime('%Y/%m/%d')} (週{dl.TW_WEEKDAY[d.weekday()]})" for d in ad)
periods_help = "分析時段：\n" + "\n".join(f"• {p}" for p in periods)

c1, c2, c3 = st.columns(3)
c1.metric('事前日數', f"{len(bd)} 日", help=before_help)
c2.metric('事後日數', f"{len(ad)} 日", help=after_help)
c3.metric('分析時段數', f"{len(periods)} 個", help=periods_help)

# ── 概覽表 ────────────────────────────────────────────────────────────────────
st.subheader('📊 各時段系統層級改善率概覽')
for period, results in all_results.items():
    if len(all_results) > 1:
        st.markdown(f"**⏱ {period}**")
    _display_overview_table(results, inc_tt)

# ── 分析摘要 ──────────────────────────────────────────────────────────────────
with st.expander('📝 分析摘要（展開）', expanded=True):
    st.markdown(cl.generate_analysis_text(all_results, len(bd), len(ad)))

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
            all_results, bd, ad,
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
