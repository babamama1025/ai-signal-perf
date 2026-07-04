"""統計計算與分析文字產生模組

指標名稱、欄位名稱均從傳入的 DataFrame 動態取得，不硬編碼。
唯 LOWER_BETTER（改善方向）與 METRIC_UNITS（顯示單位）屬領域知識，
仍以設定字典定義，其餘結構資訊一律來自 CSV。
"""
import pandas as pd
import data_loader as dl

# 改善方向：列於此集合者為「越小越好」，其餘視為「越大越好」
LOWER_BETTER = {'總停等延滯', '平均停等延滯', '旅行時間'}

# 顯示單位對應（僅供介面顯示，不影響計算邏輯）
METRIC_UNITS: dict[str, str] = {
    '總停等延滯': '秒',
    '通過量': '輛',
    '平均停等延滯': '秒/輛',
    '旅行時間': '秒',
}

# 通過量類指標（整數格式）：名稱含「量」或不在 LOWER_BETTER 中
_USE_INT_FORMAT = lambda m: m not in LOWER_BETTER


def compute_comparison(
    df: pd.DataFrame,
    period: str,
    before_dates: list,
    after_dates: list,
    columns: list[str],
    include_travel_time: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    回傳 {指標: DataFrame(欄位, 事前平均, 事後平均, 差異, 改善%)}。
    旅行時間以 '旅行時間' 為鍵單獨傳回；
    旅行時間欄與「只在一種指標出現資料」的欄相同，由結構自動偵測。
    """
    period_df = df[df['時段'] == period]
    before_set = set(pd.Timestamp(d) for d in before_dates)
    after_set  = set(pd.Timestamp(d) for d in after_dates)
    metrics    = dl.get_metrics(df)  # 從 CSV 的 '指標' 欄取得，不硬編碼

    result: dict[str, pd.DataFrame] = {}
    for metric in metrics:
        m_df = period_df[period_df['指標'] == metric]
        b_df = m_df[m_df['日期'].isin(before_set)]
        a_df = m_df[m_df['日期'].isin(after_set)]
        rows = _build_rows(b_df, a_df, columns, metric)
        result[metric] = pd.DataFrame(rows, columns=['欄位', '事前平均', '事後平均', '差異', '改善%'])

    if include_travel_time:
        tt_cols = dl.get_travel_time_columns()
        if tt_cols:
            # 找出旅行時間欄有資料的指標（由資料樣態決定，不硬編碼）
            tt_metric = _find_tt_metric(df, tt_cols)
            if tt_metric:
                tt_df = period_df[period_df['指標'] == tt_metric]
                b_tt  = tt_df[tt_df['日期'].isin(before_set)]
                a_tt  = tt_df[tt_df['日期'].isin(after_set)]
                rows  = _build_rows(b_tt, a_tt, tt_cols, '旅行時間')
                result['旅行時間'] = pd.DataFrame(rows, columns=['欄位', '事前平均', '事後平均', '差異', '改善%'])

    return result


def _find_tt_metric(df: pd.DataFrame, tt_cols: list[str]) -> str | None:
    """回傳旅行時間欄中有非 NaN 資料的指標名稱。"""
    first_tt = tt_cols[0]
    for metric in df['指標'].unique():
        if df[df['指標'] == metric][first_tt].notna().any():
            return metric
    return None


def _build_rows(b_df: pd.DataFrame, a_df: pd.DataFrame, cols: list[str], metric: str) -> list:
    rows = []
    for col in cols:
        b = b_df[col].mean() if col in b_df.columns else float('nan')
        a = a_df[col].mean() if col in a_df.columns else float('nan')
        if pd.isna(b) and pd.isna(a):
            continue
        diff = a - b if not (pd.isna(a) or pd.isna(b)) else float('nan')
        impr = _improvement_pct(b, a, metric)
        rows.append([dl.get_display_name(col), b, a, diff, impr])
    return rows


def _improvement_pct(before: float, after: float, metric: str) -> float:
    if pd.isna(before) or pd.isna(after) or before == 0:
        return float('nan')
    return (before - after) / before if metric in LOWER_BETTER else (after - before) / before


def generate_analysis_text(
    all_results: dict[str, dict[str, pd.DataFrame]],
    before_count: int,
    after_count: int,
) -> str:
    """自動依 all_results 的鍵值迭代，不依賴硬編碼指標清單。"""
    lines = [
        f"本次分析共選取 **{before_count}** 個事前（定時時制）日、"
        f"**{after_count}** 個事後（AI 號誌）日。\n"
    ]
    for period, results in all_results.items():
        lines.append(f"\n### {period} 時段\n")
        # 排除旅行時間（另行處理），依指標順序輸出
        for metric, mdf in results.items():
            if metric == '旅行時間' or mdf is None or mdf.empty:
                continue
            sys_row = mdf[mdf['欄位'] == '系統']
            if sys_row.empty:
                continue
            b   = sys_row['事前平均'].values[0]
            a   = sys_row['事後平均'].values[0]
            pct = sys_row['改善%'].values[0]
            if pd.isna(b) or pd.isna(a):
                continue
            unit      = METRIC_UNITS.get(metric, '')
            direction = '改善' if (not pd.isna(pct) and pct > 0) else '惡化'
            pct_str   = f"{abs(pct) * 100:.1f}%" if not pd.isna(pct) else 'N/A'
            fmt       = '{:,.0f}' if _USE_INT_FORMAT(metric) else '{:,.1f}'
            lines.append(
                f"- **{metric}**：事前 {fmt.format(b)} {unit} → 事後 {fmt.format(a)} {unit}，"
                f"{direction} **{pct_str}**"
            )
        tt_df = results.get('旅行時間')
        if tt_df is not None and not tt_df.empty:
            improving = int((tt_df['改善%'] > 0).sum())
            total     = int(tt_df['改善%'].notna().sum())
            if total > 0:
                lines.append(f"- **旅行時間**：{total} 條廊道中 {improving} 條有所改善")
    return "\n".join(lines)
