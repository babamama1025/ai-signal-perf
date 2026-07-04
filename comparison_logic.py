"""統計計算與分析文字產生模組"""
import pandas as pd
import numpy as np
import data_loader as dl

METRICS = ['總停等延滯', '通過量', '平均停等延滯']
LOWER_BETTER = {'總停等延滯', '平均停等延滯', '旅行時間'}
METRIC_UNITS = {
    '總停等延滯': '秒',
    '通過量': '輛',
    '平均停等延滯': '秒/輛',
    '旅行時間': '秒',
}


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
    旅行時間單獨以 '旅行時間' 鍵傳回（只從「總停等延滯」列取值）。
    """
    period_df = df[df['時段'] == period]
    before_set = set(pd.Timestamp(d) for d in before_dates)
    after_set = set(pd.Timestamp(d) for d in after_dates)

    result = {}
    for metric in METRICS:
        metric_df = period_df[period_df['指標'] == metric]
        b_df = metric_df[metric_df['日期'].isin(before_set)]
        a_df = metric_df[metric_df['日期'].isin(after_set)]
        rows = _build_rows(b_df, a_df, columns, metric)
        result[metric] = pd.DataFrame(rows, columns=['欄位', '事前平均', '事後平均', '差異', '改善%'])

    if include_travel_time:
        delay_df = period_df[period_df['指標'] == '總停等延滯']
        b_tt = delay_df[delay_df['日期'].isin(before_set)]
        a_tt = delay_df[delay_df['日期'].isin(after_set)]
        tt_cols = df.columns[54:75].tolist()
        rows = _build_rows(b_tt, a_tt, tt_cols, '旅行時間')
        result['旅行時間'] = pd.DataFrame(rows, columns=['欄位', '事前平均', '事後平均', '差異', '改善%'])

    return result


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
    if metric in LOWER_BETTER:
        return (before - after) / before
    return (after - before) / before


def generate_analysis_text(
    all_results: dict[str, dict[str, pd.DataFrame]],
    before_count: int,
    after_count: int,
) -> str:
    """all_results: {period: {metric: DataFrame}}"""
    lines = [
        f"本次分析共選取 **{before_count}** 個事前（定時時制）日、"
        f"**{after_count}** 個事後（AI 號誌）日。\n"
    ]
    for period, results in all_results.items():
        lines.append(f"\n### {period} 時段\n")
        for metric in METRICS:
            df = results.get(metric)
            if df is None or df.empty:
                continue
            sys_row = df[df['欄位'] == '系統']
            if sys_row.empty:
                continue
            b = sys_row['事前平均'].values[0]
            a = sys_row['事後平均'].values[0]
            pct = sys_row['改善%'].values[0]
            if pd.isna(b) or pd.isna(a):
                continue
            unit = METRIC_UNITS.get(metric, '')
            direction = '改善' if (not pd.isna(pct) and pct > 0) else '惡化'
            pct_str = f"{abs(pct) * 100:.1f}%" if not pd.isna(pct) else 'N/A'
            b_fmt = f"{b:,.1f}" if metric != '通過量' else f"{b:,.0f}"
            a_fmt = f"{a:,.1f}" if metric != '通過量' else f"{a:,.0f}"
            lines.append(
                f"- **{metric}**：事前 {b_fmt} {unit} → 事後 {a_fmt} {unit}，"
                f"{direction} **{pct_str}**"
            )
        tt_df = results.get('旅行時間')
        if tt_df is not None and not tt_df.empty:
            improving = int((tt_df['改善%'] > 0).sum())
            total = int(tt_df['改善%'].notna().sum())
            if total > 0:
                lines.append(f"- **旅行時間**：{total} 條廊道中 {improving} 條有所改善")
    return "\n".join(lines)


def style_comparison_df(df: pd.DataFrame, metric: str):
    """為比較表格加上改善/惡化的色彩樣式。"""
    is_volume = (metric == '通過量')

    def fmt_num(v):
        if pd.isna(v):
            return '—'
        return f"{v:,.0f}" if is_volume else f"{v:,.1f}"

    def fmt_pct(v):
        if pd.isna(v):
            return '—'
        sign = '+' if v > 0 else ''
        return f"{sign}{v * 100:.1f}%"

    def fmt_diff(v):
        if pd.isna(v):
            return '—'
        sign = '+' if v > 0 else ''
        return (f"{sign}{v:,.0f}" if is_volume else f"{sign}{v:,.1f}")

    formatted = df.copy()
    for col in ['事前平均', '事後平均']:
        if col in formatted.columns:
            formatted[col] = formatted[col].apply(fmt_num)
    if '差異' in formatted.columns:
        formatted['差異'] = formatted['差異'].apply(fmt_diff)
    if '改善%' in formatted.columns:
        formatted['改善%'] = formatted['改善%'].apply(fmt_pct)

    styler = formatted.style.apply(_color_improvement_col, axis=0,
                                   raw_pct=df.get('改善%', pd.Series(dtype=float)))
    return styler


def _color_improvement_col(col_series, raw_pct):
    colors = []
    for i, val in enumerate(col_series):
        if col_series.name in ('改善%', '差異'):
            pct = raw_pct.iloc[i] if i < len(raw_pct) else float('nan')
            if pd.isna(pct):
                colors.append('')
            elif pct > 0:
                colors.append('background-color: #d4edda; color: #155724')
            else:
                colors.append('background-color: #f8d7da; color: #721c24')
        else:
            colors.append('')
    return colors
