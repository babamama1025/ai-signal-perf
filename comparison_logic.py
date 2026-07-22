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


# ── 特殊總停等延滯 ──────────────────────────────────────────────────────────

def compute_special_raw(
    df: pd.DataFrame,
    period: str,
    before_dates: list,
    after_dates: list,
    special_int_cols: list[str],
    system_vol_col: str,
) -> dict:
    """計算特殊路口的總停等延滯加總，以及系統通過量（供後續 format_special_metrics 使用）。"""
    period_df  = df[df['時段'] == period]
    before_set = set(pd.Timestamp(d) for d in before_dates)
    after_set  = set(pd.Timestamp(d) for d in after_dates)

    delay_df = period_df[period_df['指標'] == '總停等延滯']
    b_rows   = delay_df[delay_df['日期'].isin(before_set)]
    a_rows   = delay_df[delay_df['日期'].isin(after_set)]

    def _col_sum(rows, cols):
        vals  = [rows[c].mean() for c in cols if c in rows.columns]
        valid = [v for v in vals if not pd.isna(v)]
        return sum(valid) if valid else float('nan')

    vol_df   = period_df[period_df['指標'] == '通過量']
    b_v_rows = vol_df[vol_df['日期'].isin(before_set)]
    a_v_rows = vol_df[vol_df['日期'].isin(after_set)]

    return {
        'special_delay_before': _col_sum(b_rows, special_int_cols),
        'special_delay_after':  _col_sum(a_rows, special_int_cols),
        'sys_vol_before': b_v_rows[system_vol_col].mean() if system_vol_col in b_v_rows.columns else float('nan'),
        'sys_vol_after':  a_v_rows[system_vol_col].mean() if system_vol_col in a_v_rows.columns else float('nan'),
    }


def aggregate_special_raws(all_raws: dict[str, dict], periods: list[str]) -> dict:
    """將各時段的 special raw 加總，供總表使用。"""
    b_d = a_d = b_v = a_v = 0.0
    found = False
    for p in periods:
        r = all_raws.get(p, {})
        if not r or any(pd.isna(r.get(k, float('nan')))
                        for k in ('special_delay_before', 'special_delay_after',
                                  'sys_vol_before', 'sys_vol_after')):
            continue
        b_d   += r['special_delay_before']
        a_d   += r['special_delay_after']
        b_v   += r['sys_vol_before']
        a_v   += r['sys_vol_after']
        found  = True
    nan4 = {k: float('nan') for k in ('special_delay_before', 'special_delay_after',
                                       'sys_vol_before', 'sys_vol_after')}
    return nan4 if not found else {
        'special_delay_before': b_d, 'special_delay_after': a_d,
        'sys_vol_before': b_v,       'sys_vol_after': a_v,
    }


def format_special_metrics(raw: dict) -> dict[str, dict]:
    """將 raw 值轉換為 _display_overview_table 可直接使用的 {指標名: {before,after,diff,pct}}。"""
    b_d = raw.get('special_delay_before', float('nan'))
    a_d = raw.get('special_delay_after',  float('nan'))
    b_v = raw.get('sys_vol_before',       float('nan'))
    a_v = raw.get('sys_vol_after',        float('nan'))

    b_avg = b_d / b_v if _valid_ratio(b_d, b_v) else float('nan')
    a_avg = a_d / a_v if _valid_ratio(a_d, a_v) else float('nan')

    def _entry(b, a, metric):
        diff = a - b if not (pd.isna(a) or pd.isna(b)) else float('nan')
        return {'before': b, 'after': a, 'diff': diff, 'pct': _improvement_pct(b, a, metric)}

    return {
        '特殊總停等延滯':   _entry(b_d,   a_d,   '總停等延滯'),
        '特殊平均停等延滯': _entry(b_avg, a_avg, '平均停等延滯'),
    }


def aggregate_periods(
    all_results: dict[str, dict[str, pd.DataFrame]],
    periods: list[str],
    include_travel_time: bool = True,
    extra_entities: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """把多個時段的系統層級比較結果整合成單一「全時段合計」比較表。

    - 總停等延滯、通過量：各時段系統層級數值加總後，重新計算差異／改善%
    - 平均停等延滯：由加總後的總停等延滯 / 通過量 計算（避免「平均的平均」）
    - 旅行時間：各路徑／路段的事前、事後平均值取各時段平均
    - extra_entities：桃園三期等場域的額外彙整欄位（如高鐵周邊、A19周邊、前期範圍）
    """
    entities = ['系統'] + (list(extra_entities) if extra_entities else [])

    def _sum_entity(metric: str, entity: str) -> tuple[float, float]:
        b_sum = a_sum = 0.0
        found = False
        for period in periods:
            mdf = all_results.get(period, {}).get(metric, pd.DataFrame())
            if mdf.empty:
                continue
            row = mdf[mdf['欄位'] == entity]
            if row.empty:
                continue
            b, a = row['事前平均'].values[0], row['事後平均'].values[0]
            if pd.isna(b) or pd.isna(a):
                continue
            b_sum += b
            a_sum += a
            found = True
        return (b_sum, a_sum) if found else (float('nan'), float('nan'))

    result: dict[str, pd.DataFrame] = {}

    # 先計算各實體的延滯與通過量，供後續平均停等延滯使用
    entity_delay: dict[str, tuple[float, float]] = {e: _sum_entity('總停等延滯', e) for e in entities}
    entity_vol:   dict[str, tuple[float, float]] = {e: _sum_entity('通過量', e)     for e in entities}

    for metric, entity_sums in (('總停等延滯', entity_delay), ('通過量', entity_vol)):
        rows = []
        for entity in entities:
            b, a = entity_sums[entity]
            diff = a - b if not (pd.isna(a) or pd.isna(b)) else float('nan')
            rows.append([entity, b, a, diff, _improvement_pct(b, a, metric)])
        result[metric] = pd.DataFrame(rows, columns=['欄位', '事前平均', '事後平均', '差異', '改善%'])

    avg_rows = []
    for entity in entities:
        b_delay, a_delay = entity_delay[entity]
        b_vol,   a_vol   = entity_vol[entity]
        b_avg = b_delay / b_vol if _valid_ratio(b_delay, b_vol) else float('nan')
        a_avg = a_delay / a_vol if _valid_ratio(a_delay, a_vol) else float('nan')
        diff_avg = a_avg - b_avg if not (pd.isna(a_avg) or pd.isna(b_avg)) else float('nan')
        avg_rows.append([entity, b_avg, a_avg, diff_avg, _improvement_pct(b_avg, a_avg, '平均停等延滯')])
    result['平均停等延滯'] = pd.DataFrame(avg_rows, columns=['欄位', '事前平均', '事後平均', '差異', '改善%'])

    if include_travel_time:
        tt_before: dict[str, list[float]] = {}
        tt_after:  dict[str, list[float]] = {}
        tt_order:  list[str] = []
        for period in periods:
            tdf = all_results.get(period, {}).get('旅行時間', pd.DataFrame())
            if tdf.empty:
                continue
            for _, r in tdf.iterrows():
                col = r['欄位']
                if col not in tt_order:
                    tt_order.append(col)
                if not pd.isna(r['事前平均']):
                    tt_before.setdefault(col, []).append(r['事前平均'])
                if not pd.isna(r['事後平均']):
                    tt_after.setdefault(col, []).append(r['事後平均'])

        rows = []
        for col in tt_order:
            b_list, a_list = tt_before.get(col, []), tt_after.get(col, [])
            b = sum(b_list) / len(b_list) if b_list else float('nan')
            a = sum(a_list) / len(a_list) if a_list else float('nan')
            if pd.isna(b) and pd.isna(a):
                continue
            diff = a - b if not (pd.isna(a) or pd.isna(b)) else float('nan')
            rows.append([col, b, a, diff, _improvement_pct(b, a, '旅行時間')])
        result['旅行時間'] = pd.DataFrame(rows, columns=['欄位', '事前平均', '事後平均', '差異', '改善%'])

    return result


# ── 分析文字與樣式 ─────────────────────────────────────────────────────────

def generate_analysis_text(
    all_results: dict[str, dict[str, pd.DataFrame]],
    before_counts: dict[str, int],
    after_counts: dict[str, int],
) -> str:
    lines = ["本次分析各時段選取天數（事前 = 定時時制、事後 = AI 號誌）：\n"]
    for period in all_results:
        lines.append(
            f"- **{period}**：事前 **{before_counts.get(period, 0)}** 日、"
            f"事後 **{after_counts.get(period, 0)}** 日"
        )
    lines.append("")
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
            if metric == '通過量':
                direction = '增加' if (not pd.isna(pct) and pct > 0) else '減少'
            else:
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
