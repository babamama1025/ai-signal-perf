"""AI 號誌績效比較分析工具 (Streamlit 應用程式)"""
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime

import data_loader as dl
import comparison_logic as cl
import chart_builder as cb
import export_builder as eb

# ── 場域選項 ────────────────────────────────────────────────────────────────
SITE_OPTIONS = {
    '桃園四期(大湳)': 'perf_summary_4.csv',
    '桃園三期(高鐵)': 'perf_summary_3.csv',
}

# ── 路徑設定 ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
XLSX_PATH = next(BASE_DIR.glob('=*績效*.xlsx'), None)

# ── 頁面設定 ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title='AI 號誌績效比較分析',
    page_icon='🚦',
    layout='wide',
)

# ── 表格格式化輔助函式 ────────────────────────────────────────────────────────
def _format_comp_df(comp_df: pd.DataFrame, metric: str):
    """回傳（格式化後 DataFrame, 原始 改善% Series）供樣式使用。"""
    is_vol = (metric == '通過量')
    raw_pct = comp_df['改善%'].copy()
    d = comp_df.copy()

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
def _make_date_df() -> pd.DataFrame:
    rows = []
    for d in all_dates:
        key = d.strftime('%Y-%m-%d')
        c = cls_map.get(key, '未知')
        rows.append({
            '日期': d.strftime('%Y/%m/%d'),
            '星': dl.TW_WEEKDAY[d.weekday()],
            '分類': c,
            '事前': c == 'FIX',
            '事後': c == 'AI',
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

    # 分析時段
    st.subheader('📅 分析時段')
    default_periods = [p for p in ['07:00~09:00', '14:00~16:00', '16:00~19:00']
                       if p in all_periods]
    selected_periods = st.multiselect(
        '選擇時段（可多選）',
        options=all_periods,
        default=default_periods,
    )

    st.divider()

    # 日期分配表格
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

    before_dates = [all_dates[i] for i, row in edited_df.iterrows() if row['事前']]
    after_dates  = [all_dates[i] for i, row in edited_df.iterrows() if row['事後']]
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
            all_results = {}
            for period in selected_periods:
                all_results[period] = cl.compute_comparison(
                    df, period, before_dates, after_dates,
                    compare_cols, include_travel_time=include_tt,
                )
            st.session_state['analysis_results'] = {
                'results': all_results,
                'before_dates': before_dates,
                'after_dates': after_dates,
                'periods': selected_periods,
                'include_tt': include_tt,
            }
        st.success('分析完成！')

    if run_disabled:
        st.caption('⚠️ 請先選擇時段與事前／事後日期')

# ── 主畫面 ──────────────────────────────────────────────────────────────────
st.title(f'🚦 AI 號誌績效比較分析 ─ {selected_site}')

if st.session_state['analysis_results'] is None:
    st.info('請在左側設定分析條件後，點擊「執行分析」按鈕。')
    st.markdown("""
**使用步驟：**
1. 左側選擇「場域」（桃園四期大湳 或 桃園三期高鐵）
2. 確認或手動調整日期的事前／事後勾選（若有測試日工作表會自動預填，可點「重置」還原）
3. 選擇要分析的時段（可多選）
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
c1, c2, c3 = st.columns(3)
c1.metric('事前日數', f"{len(bd)} 日")
c2.metric('事後日數', f"{len(ad)} 日")
c3.metric('分析時段數', f"{len(periods)} 個")

# ── 概覽圖 ────────────────────────────────────────────────────────────────────
st.plotly_chart(cb.make_improvement_overview_chart(all_results), use_container_width=True)

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
