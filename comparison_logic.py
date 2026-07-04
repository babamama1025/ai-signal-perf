"""統計計算與分析文字產生模組

計算原則：
- 總停等延滯、通過量：直接取選定日期的欄位平均值
- 平均停等延滯：mean(總停等延滯) / mean(通過量)，不使用 CSV 的平均停等延滯列
  （避免「平均的平均」造成的加權偏差）
- 旅行時間：取旅行時間欄有資料的指標列均值（通常為總停等延滯列）
"""
import pandas as pd
import data_loader as dl

# 改善方向：列於此集合者「越小越好」，其餘視為「越大越好」
LOWER_BETTER = {'總停等延滯', '平均停等延滯', '旅行時間'}

# 衍生指標定義：{衍生指標名稱: (分子指標, 分母指標)}
# 平均停等延滯 = 總停等延滯 / 通過量，從 CSV 資料計算，不直接使用 CSV 的平均停等延滯列
DERIVED_METRICS: dict[str, tuple[str, str]] = {
    '平均停等延滯': ('總停等延滯', '通過量'),
}

# 顯示單位（僅介面用）
METRIC_UNITS: dict[str, str] = {
    '總停等延滯': '秒',
    '通過量': '輛',
    '平均停等延滯': '秒/輛',
    '旅行時間': '秒',
}

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

    計算流程：
    1. 對每個「主要指標」（非衍生），計算各欄位在事前/事後日期的平均值
    2. 對「衍生指標」（平均停等延滯），以 mean(分子) / mean(分母) 計算
    3. 旅行時間廊道同主要指標，但只取有旅行時間資料的指標列
    """
    period_df  = df[df['時段'] == period]
    before_set = set(pd.Timestamp(d) for d in before_dates)
    after_set  = set(pd.Timestamp(d) for d in after_dates)
    metrics    = dl.get_metrics(df)

    # 第一輪：計算所有主要指標的欄位均值
    raw_means: dict[str, dict] = {}
    for metric in metrics:
        if metric in DERIVED_METRICS:
            continue
        m_df = period_df[period_df['指標'] == metric]
        b_df = m_df[m_df['日期'].isin(before_set)]
        a_df = m_df[m_df['日期'].isin(after_set)]
        raw_means[metric] = {
            'before': {col: b_df[col].mean() for col in columns if col in b_df.columns},
            'after':  {col: a_df[col].mean() for col in columns if col in a_df.columns},
        }

    # 第二輪：依原始指標順序組裝結果（衍生指標從主要指標均值計算）
    result: dict[str, pd.DataFrame] = {}
    for metric in metrics:
        if metric in DERIVED_METRICS:
            num_m, denom_m = DERIVED_METRICS[metric]
            rows = _build_derived_rows(
                raw_means.get(num_m, {}),
                raw_means.get(denom_m, {}),
                columns, metric,
            )
        else:
            rows = _build_primary_rows(raw_means.get(metric, {}), columns, metric)
        result[metric] = pd.DataFrame(rows, columns=['欄位', '事前平均', '事後平均', '差異', '改善%'])

    # 旅行時間廊道（只從有旅行時間資料的指標列取值）
    if include_travel_time:
        tt_cols = dl.get_travel_time_columns()
        if tt_cols:
            tt_metric = _find_tt_metric(df, tt_cols)
            if tt_metric:
                tt_df  = period_df[period_df['指標'] == tt_metric]
                b_tt   = tt_df[tt_df['日期'].isin(before_set)]
                a_tt   = tt_df[tt_df['日期'].isin(after_set)]
                tt_means = {
                    'before': {col: b_tt[col].mean() for col in tt_cols if col in b_tt.columns},
                    'after':  {col: a_tt[col].mean() for col in tt_cols if col in a_tt.columns},
                }
                rows = _build_primary_rows(tt_means, tt_cols, '旅行時間')
                result['旅行時間'] = pd.DataFrame(rows, columns=['欄位', '事前平均', '事後平均', '差異', '改善%'])

    return result


# ── 內部輔助函式 ────────────────────────────────────────────────────────────

def _build_primary_rows(means_dict: dict, cols: list[str], metric: str) -> list:
    """直接從均值字典建立比較列。"""
    rows = []
    for col in cols:
        b = means_dict.get('before', {}).get(col, float('nan'))
        a = means_dict.get('after',  {}).get(col, float('nan'))
        if pd.isna(b) and pd.isna(a):
            continue
        diff = a - b if not (pd.isna(a) or pd.isna(b)) else float('nan')
        rows.append([dl.get_display_name(col), b, a, diff, _improvement_pct(b, a, metric)])
    return rows


def _build_derived_rows(
    num_means: dict, denom_means: dict, cols: list[str], metric: str
) -> list:
    """衍生指標：值 = mean(分子欄) / mean(分母欄)。"""
    rows = []
    for col in cols:
        b_num = num_means.get('before', {}).get(col, float('nan'))
        a_num = num_means.get('after',  {}).get(col, float('nan'))
        b_den = denom_means.get('before', {}).get(col, float('nan'))
        a_den = denom_means.get('after',  {}).get(col, float('nan'))

        b = b_num / b_den if _valid_ratio(b_num, b_den) else float('nan')
        a = a_num / a_den if _valid_ratio(a_num, a_den) else float('nan')

        if pd.isna(b) and pd.isna(a):
            continue
        diff = a - b if not (pd.isna(a) or pd.isna(b)) else float('nan')
        rows.append([dl.get_display_name(col), b, a, diff, _improvement_pct(b, a, metric)])
    return rows


def _valid_ratio(num: float, denom: float) -> bool:
    return not (pd.isna(num) or pd.isna(denom) or denom == 0)


def _find_tt_metric(df: pd.DataFrame, tt_cols: list[str]) -> str | None:
    """找出旅行時間欄有非 NaN 資料的指標名稱。"""
    first_tt = tt_cols[0]
    for metric in df['指標'].unique():
        if df[df['指標'] == metric][first_tt].notna().any():
            return metric
    return None


def _improvement_pct(before: float, after: float, metric: str) -> float:
    if pd.isna(before) or pd.isna(after) or before == 0:
        return float('nan')
    return (before - after) / before if metric in LOWER_BETTER else (after - before) / before


# ── 分析文字與樣式 ─────────────────────────────────────────────────────────

def generate_analysis_text(
    all_results: dict[str, dict[str, pd.DataFrame]],
    before_count: int,
    after_count: int,
) -> str:
    lines = [
        f"本次分析共選取 **{before_count}** 個事前（定時時制）日、"
        f"**{after_count}** 個事後（AI 號誌）日。\n"
    ]
    for period, results in all_results.items():
        lines.append(f"\n### {period} 時段\n")
        for metric, mdf in results.items():
            if metric == '旅行時間' or mdf is None or mdf.empty:
                continue
            sys_row = mdf[mdf['欄位'] == '系統']
            if sys_row.empty:
                continue
            b, a, pct = (sys_row[c].values[0] for c in ['事前平均', '事後平均', '改善%'])
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
