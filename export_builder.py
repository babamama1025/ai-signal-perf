"""openpyxl Excel 匯出模組"""
import io
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import (
    PatternFill, Font, Alignment, Border, Side, numbers
)
from openpyxl.utils import get_column_letter

# 色彩定義
FILL_HEADER = PatternFill('solid', fgColor='4472C4')
FILL_SUBHEADER = PatternFill('solid', fgColor='9DC3E6')
FILL_GREEN = PatternFill('solid', fgColor='C6EFCE')
FILL_RED = PatternFill('solid', fgColor='FFC7CE')
FILL_SUMMARY_HDR = PatternFill('solid', fgColor='D9E1F2')

FONT_WHITE_BOLD = Font(name='微軟正黑體', bold=True, color='FFFFFF')
FONT_BOLD = Font(name='微軟正黑體', bold=True)
FONT_GREEN = Font(name='微軟正黑體', color='375623')
FONT_RED = Font(name='微軟正黑體', color='9C0006')
FONT_NORMAL = Font(name='微軟正黑體')

# 服務水準 (LOS) — 台灣公路容量手冊：號誌化路口平均停等延滯對應值
_LOS_THRESHOLDS = [(15, 'A'), (30, 'B'), (45, 'C'), (60, 'D'), (80, 'E')]

_LOS_FILL = {
    'A': PatternFill('solid', fgColor='00B050'),
    'B': PatternFill('solid', fgColor='92D050'),
    'C': PatternFill('solid', fgColor='FFFF00'),
    'D': PatternFill('solid', fgColor='FFC000'),
    'E': PatternFill('solid', fgColor='FF0000'),
    'F': PatternFill('solid', fgColor='C00000'),
}

_LOS_FONT = {
    'A': Font(name='微軟正黑體', bold=True, color='FFFFFF'),
    'B': Font(name='微軟正黑體', bold=True, color='375623'),
    'C': Font(name='微軟正黑體', bold=True, color='000000'),
    'D': Font(name='微軟正黑體', bold=True, color='000000'),
    'E': Font(name='微軟正黑體', bold=True, color='FFFFFF'),
    'F': Font(name='微軟正黑體', bold=True, color='FFFFFF'),
}

from comparison_logic import (
    LOWER_BETTER, METRIC_UNITS, aggregate_periods, analyze_daily,
    DAILY_WORSE, DAILY_BETTER, DAILY_INVALID,
)
import data_loader as dl


def build_comparison_xlsx(
    all_results: dict[str, dict[str, pd.DataFrame]],
    before_by_period: dict[str, list],
    after_by_period: dict[str, list],
    include_travel_time: bool = True,
    raw_df: pd.DataFrame | None = None,
    raw_periods: list | None = None,
    extra_summary_entities: list[str] | None = None,
    day_type_label: str = '平日',
    daily_df: pd.DataFrame | None = None,
    daily_k: float = 1.5,
    degradation_df: pd.DataFrame | None = None,
) -> io.BytesIO:
    wb = Workbook()
    wb.remove(wb.active)

    _build_info_sheet(wb, before_by_period, after_by_period)
    _build_summary_sheet(wb, all_results, before_by_period, after_by_period,
                         extra_summary_entities=extra_summary_entities,
                         include_travel_time=include_travel_time)
    _build_intersection_sheet(wb, all_results, extra_summary_entities, day_type_label)
    for period, results in all_results.items():
        _build_period_sheet(
            wb, period, results,
            before_by_period.get(period, []), after_by_period.get(period, []),
            include_travel_time,
        )
    if daily_df is not None:
        for period in all_results:
            _build_daily_sheet(
                wb, daily_df, period,
                before_by_period.get(period, []), after_by_period.get(period, []),
                include_travel_time, daily_k,
            )
    if degradation_df is not None:
        _build_degradation_sheet(wb, degradation_df, before_by_period, after_by_period)
    if raw_df is not None and raw_periods:
        _build_raw_data_sheet(wb, raw_df, before_by_period, after_by_period, raw_periods)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _fmt_date(d) -> str:
    _WD = ['一', '二', '三', '四', '五', '六', '日']
    ts = d if isinstance(d, pd.Timestamp) else pd.Timestamp(d)
    return f"{ts.strftime('%Y/%m/%d')} (週{_WD[ts.weekday()]})"


def _build_info_sheet(wb, before_by_period, after_by_period):
    from datetime import datetime
    ws = wb.create_sheet('分析說明')

    ws.merge_cells('A1:C1')
    ws['A1'] = 'AI 號誌績效比較分析'
    ws['A1'].font = Font(name='微軟正黑體', bold=True, size=14)
    ws['A1'].alignment = Alignment(horizontal='center')

    ws['A2'] = f"產製時間：{datetime.now().strftime('%Y/%m/%d %H:%M')}"
    ws['A2'].font = Font(name='微軟正黑體', italic=True, color='666666')

    row = 4
    for period in before_by_period:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
        cell = ws.cell(row, 1, f'⏱ {period}')
        cell.font = FONT_WHITE_BOLD
        cell.fill = FILL_HEADER
        row += 1
        for label, dates, fill in [
            (f'事前日期（定時時制，共 {len(before_by_period[period])} 天）', before_by_period[period], FILL_SUBHEADER),
            (f'事後日期（AI 號誌，共 {len(after_by_period[period])} 天）',   after_by_period[period],  FILL_SUMMARY_HDR),
        ]:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
            cell = ws.cell(row, 1, label)
            cell.font = FONT_BOLD
            cell.fill = fill
            row += 1
            for d in dates:
                ws.cell(row, 1, _fmt_date(d)).font = FONT_NORMAL
                row += 1
            row += 1
        row += 1

    ws.column_dimensions['A'].width = 28


def _build_summary_sheet(wb, all_results, before_by_period, after_by_period,
                          extra_summary_entities: list[str] | None = None,
                          include_travel_time: bool = True):
    ws = wb.create_sheet('總表')
    periods = list(all_results.keys())
    extra_entities = list(extra_summary_entities) if extra_summary_entities else []

    # 「全時段合計」：總停等延滯／通過量加總、平均停等延滯由加總重新推導
    aggregated = aggregate_periods(all_results, periods, include_travel_time=include_travel_time,
                                   extra_entities=extra_entities or None)
    n_before_all = sum(len(before_by_period.get(p, [])) for p in periods)
    n_after_all  = sum(len(after_by_period.get(p, [])) for p in periods)

    col_blocks = [('全時段合計', aggregated, n_before_all, n_after_all)]
    col_blocks += [
        (period, all_results[period], len(before_by_period.get(period, [])), len(after_by_period.get(period, [])))
        for period in periods
    ]

    # 標題列
    ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)
    ws.cell(1, 1, '指標').font = FONT_BOLD
    ws.cell(1, 1).fill = FILL_SUMMARY_HDR
    ws.cell(1, 1).alignment = Alignment(horizontal='center', vertical='center')

    col_start = 2
    sub_headers = ['事前平均', '事後平均', '差異', '改善%']
    for i, (label, _, n_before, n_after) in enumerate(col_blocks):
        c = col_start + i * 4
        ws.merge_cells(start_row=1, start_column=c, end_row=1, end_column=c + 3)
        ws.cell(1, c, f'{label}（前{n_before}/後{n_after}）').font = FONT_BOLD
        ws.cell(1, c).fill = FILL_SUMMARY_HDR
        ws.cell(1, c).alignment = Alignment(horizontal='center')
        for j, h in enumerate(sub_headers):
            ws.cell(2, c + j, h).font = FONT_BOLD
            ws.cell(2, c + j).fill = FILL_SUMMARY_HDR

    row = 3
    first_period = periods[0]
    metrics_in_results = [k for k in all_results[first_period] if k != '旅行時間']
    for metric in metrics_in_results:
        # 指標標題列（顯示系統層級數據）
        ws.cell(row, 1, metric).font = FONT_WHITE_BOLD
        ws.cell(row, 1).fill = FILL_HEADER
        for i, (_, results_dict, _n_before, _n_after) in enumerate(col_blocks):
            c = col_start + i * 4
            df = results_dict.get(metric, pd.DataFrame())
            r = df[df['欄位'] == '系統'] if not df.empty else pd.DataFrame()
            if not r.empty:
                b, a = r['事前平均'].values[0], r['事後平均'].values[0]
                diff, pct = r['差異'].values[0], r['改善%'].values[0]
                ws.cell(row, c, b if not pd.isna(b) else None)
                ws.cell(row, c + 1, a if not pd.isna(a) else None)
                ws.cell(row, c + 2, diff if not pd.isna(diff) else None)
                _apply_num_format(ws.cell(row, c), metric)
                _apply_num_format(ws.cell(row, c + 1), metric)
                _apply_num_format(ws.cell(row, c + 2), metric)
                _write_pct(ws.cell(row, c + 3), pct)
        row += 1

        # 額外實體子列（桃園三期：高鐵周邊、A19周邊、前期範圍）
        for entity in extra_entities:
            ws.cell(row, 1, f'  {entity}').font = FONT_NORMAL
            ws.cell(row, 1).fill = FILL_SUBHEADER
            for i, (_, results_dict, _n_before, _n_after) in enumerate(col_blocks):
                c = col_start + i * 4
                df = results_dict.get(metric, pd.DataFrame())
                r = df[df['欄位'] == entity] if not df.empty else pd.DataFrame()
                if not r.empty:
                    b, a = r['事前平均'].values[0], r['事後平均'].values[0]
                    diff, pct = r['差異'].values[0], r['改善%'].values[0]
                    ws.cell(row, c, b if not pd.isna(b) else None)
                    ws.cell(row, c + 1, a if not pd.isna(a) else None)
                    ws.cell(row, c + 2, diff if not pd.isna(diff) else None)
                    _apply_num_format(ws.cell(row, c), metric)
                    _apply_num_format(ws.cell(row, c + 1), metric)
                    _apply_num_format(ws.cell(row, c + 2), metric)
                    _write_pct(ws.cell(row, c + 3), pct)
            row += 1

    # 旅行時間各廊道（全時段用各時段平均）
    if include_travel_time:
        corridor_order: list[str] = []
        seen_corridors: set[str] = set()
        for _, results_dict, _, _ in col_blocks:
            tdf = results_dict.get('旅行時間', pd.DataFrame())
            if not tdf.empty:
                for corridor in tdf['欄位']:
                    if corridor not in seen_corridors:
                        seen_corridors.add(corridor)
                        corridor_order.append(corridor)
        if corridor_order:
            ws.cell(row, 1, '旅行時間').font = FONT_WHITE_BOLD
            ws.cell(row, 1).fill = FILL_HEADER
            row += 1
            for corridor in corridor_order:
                ws.cell(row, 1, f'  {corridor}').font = FONT_NORMAL
                ws.cell(row, 1).fill = FILL_SUBHEADER
                for i, (_, results_dict, _, _) in enumerate(col_blocks):
                    c = col_start + i * 4
                    tdf = results_dict.get('旅行時間', pd.DataFrame())
                    r = tdf[tdf['欄位'] == corridor] if not tdf.empty else pd.DataFrame()
                    if not r.empty:
                        b, a = r['事前平均'].values[0], r['事後平均'].values[0]
                        diff, pct = r['差異'].values[0], r['改善%'].values[0]
                        ws.cell(row, c, b if not pd.isna(b) else None)
                        ws.cell(row, c + 1, a if not pd.isna(a) else None)
                        ws.cell(row, c + 2, diff if not pd.isna(diff) else None)
                        _apply_num_format(ws.cell(row, c), '旅行時間')
                        _apply_num_format(ws.cell(row, c + 1), '旅行時間')
                        _apply_num_format(ws.cell(row, c + 2), '旅行時間')
                        _write_pct(ws.cell(row, c + 3), pct)
                row += 1

    # 欄寬
    ws.column_dimensions['A'].width = 18
    for col in range(2, 2 + len(col_blocks) * 4):
        ws.column_dimensions[get_column_letter(col)].width = 13

    # 說明
    ws.cell(row + 1, 1, '「全時段合計」總停等延滯／通過量為各時段加總，平均停等延滯由加總重新推導，旅行時間為各時段平均；完整日期清單見「分析說明」工作表').font = Font(italic=True, color='666666')


_INTERSECTION_METRICS = ['總停等延滯', '通過量', '平均停等延滯']
_INTERSECTION_METRIC_LABELS = {'總停等延滯': '停等延滯', '通過量': '通過量', '平均停等延滯': '平均停等延滯'}


def _build_intersection_sheet(wb, all_results, extra_entities, day_type_label='平日'):
    """依路口分組、各時段並排的路口績效總覽（列＝路口×指標，欄＝時段×定時/AI/差異/%）。"""
    ws = wb.create_sheet('路口績效')
    periods = list(all_results.keys())

    groups = dl.get_column_groups()
    intersection_names = [k for k in groups if k not in ('系統總量', '旅行時間廊道')]
    entities = list(extra_entities or []) + intersection_names

    def _has_data(entity: str) -> bool:
        for period in periods:
            for metric in _INTERSECTION_METRICS:
                df = all_results.get(period, {}).get(metric, pd.DataFrame())
                if not df.empty and entity in df['欄位'].values:
                    return True
        return False
    entities = [e for e in entities if _has_data(e)]

    if not entities or not periods:
        ws.cell(1, 1, '（無資料）').font = Font(italic=True, color='888888')
        return

    col_start = 3
    sub_headers = ['定時時制', 'AI號誌', '差異', '改善%']

    ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=2)
    corner = ws.cell(1, 1, day_type_label)
    corner.font = FONT_WHITE_BOLD
    corner.fill = FILL_HEADER
    corner.alignment = Alignment(horizontal='center', vertical='center')

    for i, period in enumerate(periods):
        c = col_start + i * 4
        ws.merge_cells(start_row=1, start_column=c, end_row=1, end_column=c + 3)
        head = ws.cell(1, c, period)
        head.font = FONT_WHITE_BOLD
        head.fill = FILL_HEADER
        head.alignment = Alignment(horizontal='center')
        for j, h in enumerate(sub_headers):
            sub_cell = ws.cell(2, c + j, h)
            sub_cell.font = FONT_WHITE_BOLD
            sub_cell.fill = FILL_HEADER
            sub_cell.alignment = Alignment(horizontal='center')

    row = 3
    for entity in entities:
        start_row = row
        ws.merge_cells(start_row=start_row, start_column=1, end_row=start_row + len(_INTERSECTION_METRICS) - 1, end_column=1)
        ename_cell = ws.cell(start_row, 1, entity)
        ename_cell.font = FONT_WHITE_BOLD
        ename_cell.fill = FILL_HEADER
        ename_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

        for metric in _INTERSECTION_METRICS:
            mcell = ws.cell(row, 2, _INTERSECTION_METRIC_LABELS[metric])
            mcell.font = FONT_WHITE_BOLD
            mcell.fill = FILL_SUBHEADER
            mcell.alignment = Alignment(vertical='center')

            for i, period in enumerate(periods):
                c = col_start + i * 4
                df = all_results.get(period, {}).get(metric, pd.DataFrame())
                r = df[df['欄位'] == entity] if not df.empty else pd.DataFrame()
                if not r.empty:
                    b, a, diff, pct = (r[col].values[0] for col in ['事前平均', '事後平均', '差異', '改善%'])
                    _write_num(ws.cell(row, c), b, metric)
                    _write_num(ws.cell(row, c + 1), a, metric)
                    _write_num(ws.cell(row, c + 2), diff, metric)
                    _write_pct(ws.cell(row, c + 3), pct)
                for cc in range(c, c + 4):
                    ws.cell(row, cc).alignment = Alignment(horizontal='right')
            row += 1

    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 14
    for col in range(col_start, col_start + len(periods) * 4):
        ws.column_dimensions[get_column_letter(col)].width = 12
    ws.freeze_panes = 'C3'


def _build_period_sheet(wb, period, results, before_dates, after_dates, include_travel_time):
    safe_name = period.replace(':', '').replace('~', '-')[:31]
    ws = wb.create_sheet(safe_name)

    # 依指標順序並排顯示（排除旅行時間；旅行時間接在最後一個路口來向列之後）
    # 平均停等延滯額外佔 2 欄放事前／事後服務水準
    metrics_in_results = [k for k in results if k != '旅行時間']

    metric_col_starts: dict[str, int] = {}
    _c = 2
    for metric in metrics_in_results:
        metric_col_starts[metric] = _c
        _c += 6 if metric == '平均停等延滯' else 4
    total_cols = max(_c - 1, 5)

    # 第一列：時段標題
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    ws.cell(1, 1, f"{period}  │  事前：{len(before_dates)} 日  │  事後：{len(after_dates)} 日")
    ws.cell(1, 1).font = Font(name='微軟正黑體', bold=True, size=12)
    ws.cell(1, 1).alignment = Alignment(horizontal='center')

    # 第二、三列：欄位標頭（各指標並排；平均停等延滯多2欄放服務水準）
    ws.merge_cells(start_row=2, start_column=1, end_row=3, end_column=1)
    item_cell = ws.cell(2, 1, '項目')
    item_cell.font = FONT_WHITE_BOLD
    item_cell.fill = FILL_HEADER
    item_cell.alignment = Alignment(horizontal='center', vertical='center')

    for metric in metrics_in_results:
        c = metric_col_starts[metric]
        span = 6 if metric == '平均停等延滯' else 4
        unit = METRIC_UNITS.get(metric, '')
        ws.merge_cells(start_row=2, start_column=c, end_row=2, end_column=c + span - 1)
        head_cell = ws.cell(2, c, f'{metric} ({unit})')
        head_cell.font = FONT_WHITE_BOLD
        head_cell.fill = FILL_HEADER
        head_cell.alignment = Alignment(horizontal='center')
        sub_headers = ['事前平均', '事後平均', '差異', '改善%']
        if metric == '平均停等延滯':
            sub_headers += ['事前服務水準', '事後服務水準']
        for i, h in enumerate(sub_headers):
            sub_cell = ws.cell(3, c + i, h)
            sub_cell.font = FONT_WHITE_BOLD
            sub_cell.fill = FILL_HEADER
            sub_cell.alignment = Alignment(horizontal='center')

    # 資料列：欄位順序取各指標資料的聯集（依出現順序）
    field_order = []
    seen = set()
    for metric in metrics_in_results:
        mdf = results.get(metric, pd.DataFrame())
        if mdf.empty:
            continue
        for f in mdf['欄位']:
            if f not in seen:
                seen.add(f)
                field_order.append(f)

    row = 4
    for field in field_order:
        ws.cell(row, 1, field).font = FONT_NORMAL
        for metric in metrics_in_results:
            c = metric_col_starts[metric]
            mdf = results.get(metric, pd.DataFrame())
            r = mdf[mdf['欄位'] == field] if not mdf.empty else pd.DataFrame()
            if not r.empty:
                b, a, diff, pct = (r[col].values[0] for col in ['事前平均', '事後平均', '差異', '改善%'])
                _write_num(ws.cell(row, c), b, metric)
                _write_num(ws.cell(row, c + 1), a, metric)
                _write_num(ws.cell(row, c + 2), diff, metric)
                _write_pct(ws.cell(row, c + 3), pct)
                if metric == '平均停等延滯':
                    _write_los(ws.cell(row, c + 4), b)
                    _write_los(ws.cell(row, c + 5), a)
            for cc in range(c, c + 4):
                ws.cell(row, cc).alignment = Alignment(horizontal='right')
            if metric == '平均停等延滯':
                ws.cell(row, c + 4).alignment = Alignment(horizontal='center')
                ws.cell(row, c + 5).alignment = Alignment(horizontal='center')
        row += 1

    # 旅行時間：接續在最後一個路口來向列之後
    tt_df = results.get('旅行時間', pd.DataFrame())
    if include_travel_time and not tt_df.empty:
        row = _write_section(
            ws, row, f'▶ 旅行時間 ({METRIC_UNITS.get("旅行時間", "")})',
            tt_df, '旅行時間', FILL_HEADER, total_cols,
        )

    # 欄寬
    ws.column_dimensions['A'].width = 30
    for col in range(2, total_cols + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14

    # 凍結前三列
    ws.freeze_panes = 'A4'


def _build_raw_data_sheet(wb, df: pd.DataFrame, before_by_period, after_by_period, periods):
    ws = wb.create_sheet('原始資料')

    parts = []
    for period in periods:
        before_set = {pd.Timestamp(d) for d in before_by_period.get(period, [])}
        after_set  = {pd.Timestamp(d) for d in after_by_period.get(period, [])}
        date_set   = before_set | after_set
        sub = df[(df['時段'] == period) & (df['日期'].isin(date_set))].copy()
        sub.insert(0, '分組', sub['日期'].apply(
            lambda d: '事前' if d in before_set else '事後'
        ))
        parts.append(sub)
    raw = pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0].copy()
    raw['日期'] = raw['日期'].apply(lambda d: d.strftime('%Y/%m/%d'))

    headers = raw.columns.tolist()
    for c, h in enumerate(headers, 1):
        cell = ws.cell(1, c, h)
        cell.font = FONT_WHITE_BOLD
        cell.fill = FILL_HEADER
        cell.alignment = Alignment(horizontal='center')

    for r, (_, row_data) in enumerate(raw.iterrows(), 2):
        for c, val in enumerate(row_data, 1):
            cell = ws.cell(r, c)
            if pd.isna(val):
                cell.value = None
            elif isinstance(val, float) and val == int(val):
                cell.value = int(val)
            else:
                cell.value = val
            cell.font = FONT_NORMAL

    ws.column_dimensions['A'].width = 8
    ws.column_dimensions['B'].width = 14
    ws.column_dimensions['C'].width = 16
    ws.column_dimensions['D'].width = 14
    for i in range(5, len(headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 11
    ws.freeze_panes = 'A2'


FILL_YELLOW = PatternFill('solid', fgColor='FFF2CC')
_DAILY_FLAG_FILL = {DAILY_WORSE: FILL_RED, DAILY_BETTER: FILL_GREEN, DAILY_INVALID: FILL_YELLOW}
_DAILY_METRICS = ['平均停等延滯', '總停等延滯', '通過量']


def _build_daily_sheet(wb, df, period, before_dates, after_dates,
                       include_travel_time, k):
    """每日明細（列＝日期，欄＝指標×對象），依同組 IQR 判定結果著色，末尾附各組平均與改善率。"""
    ws = wb.create_sheet(('每日_' + period.replace(':', '').replace('~', '-'))[:31])

    entities = dl.get_system_columns() + list(dl.get_column_structure().get('groups', {}))
    blocks = [(m, entities) for m in _DAILY_METRICS]
    if include_travel_time and dl.get_travel_time_columns():
        blocks.append(('旅行時間', dl.get_travel_time_columns()))
    columns = [(m, c) for m, cols in blocks for c in cols]
    analyses = {(m, c): analyze_daily(df, period, before_dates, after_dates, m, c, k)
                for m, c in columns}

    fixed = ['日期', '星期', '組別']
    total_cols = len(fixed) + len(columns)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=min(total_cols, 12))
    ws.cell(1, 1, f'{period} 每日明細 │ 異常門檻 IQR×{k:g} │ '
                  '紅底＝明顯較差、綠底＝明顯較好、黃底＝資料異常（0 或缺值）').font = FONT_BOLD

    for i, h in enumerate(fixed, 1):
        ws.merge_cells(start_row=2, start_column=i, end_row=3, end_column=i)
        cell = ws.cell(2, i, h)
        cell.font, cell.fill = FONT_WHITE_BOLD, FILL_HEADER
        cell.alignment = Alignment(horizontal='center', vertical='center')
    c = len(fixed) + 1
    for metric, cols in blocks:
        ws.merge_cells(start_row=2, start_column=c, end_row=2, end_column=c + len(cols) - 1)
        head = ws.cell(2, c, f'{metric} ({METRIC_UNITS.get(metric, "")})')
        head.font, head.fill = FONT_WHITE_BOLD, FILL_HEADER
        head.alignment = Alignment(horizontal='center')
        for j, col in enumerate(cols):
            sub = ws.cell(3, c + j, dl.get_display_name(col))
            sub.font, sub.fill = FONT_BOLD, FILL_SUBHEADER
            sub.alignment = Alignment(horizontal='center', wrap_text=True)
        c += len(cols)

    base = analyses[columns[0]][0]
    row = 4
    for idx, d in base['日期'].items():
        d_str = d.strftime('%Y/%m/%d')
        ws.cell(row, 1, d_str).font = FONT_NORMAL
        ws.cell(row, 2, _fmt_date(d)[-3:-1]).font = FONT_NORMAL
        ws.cell(row, 3, base.at[idx, '組別']).font = FONT_NORMAL
        for j, key in enumerate(columns):
            daily = analyses[key][0]
            cell = ws.cell(row, len(fixed) + 1 + j)
            _write_num(cell, daily.at[idx, '數值'], key[0])
            if daily.at[idx, '標記'] in _DAILY_FLAG_FILL:
                cell.fill = _DAILY_FLAG_FILL[daily.at[idx, '標記']]
        row += 1

    row += 1
    for label, getter, is_pct in [
        ('事前平均', lambda s: s['事前']['平均'], False),
        ('事後平均', lambda s: s['事後']['平均'], False),
        ('改善%（全部日期）', lambda s: s['改善%'], True),
        ('改善%（排除有標記日期）', lambda s: s['改善%_排除異常日'], True),
    ]:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(fixed))
        lab = ws.cell(row, 1, label)
        lab.font, lab.fill = FONT_BOLD, FILL_SUMMARY_HDR
        for j, key in enumerate(columns):
            cell = ws.cell(row, len(fixed) + 1 + j)
            val = getter(analyses[key][1])
            _write_pct(cell, val) if is_pct else _write_num(cell, val, key[0])
        row += 1

    ws.column_dimensions['A'].width = 12
    for col in range(2, len(fixed) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 8
    for col in range(len(fixed) + 1, total_cols + 1):
        ws.column_dimensions[get_column_letter(col)].width = 12
    ws.freeze_panes = ws.cell(4, len(fixed) + 1)


_DEG_COLS = ['降階次數', '小時制降階次數', '降階小時區間', '降階分鐘', '主要原因']


def _build_degradation_sheet(wb, deg, before_by_period, after_by_period):
    """降階紀錄：分析所用的事前／事後日期（各時段聯集）的每日降階統計；降階為整天數字，不分時段。
    上方為各組合計與日平均（日平均以有降階資料的天數為分母），下方為每日明細。"""
    ws = wb.create_sheet('降階紀錄')
    groups = {g: {pd.Timestamp(d).normalize() for ds in by.values() for d in ds}
              for g, by in (('事前', before_by_period), ('事後', after_by_period))}
    by_date = deg.set_index(deg['日期'].dt.normalize())

    ws.cell(1, 1, '降階統計為整天數字（不分時段），列出本次分析各時段事前／事後日期的聯集；'
                  '來源：平台降階紀錄（計算專案 aisig publish 產生）').font = FONT_BOLD

    sum_hdr = ['組別', '天數', '有降階資料天數', '有降階天數', '降階次數', '小時制降階次數', '降階時數',
               '日均降階次數', '日均小時制次數', '日均降階分鐘']
    for c, h in enumerate(sum_hdr, 1):
        _hdr(ws.cell(3, c, h))
    for r, (g, dates) in enumerate(groups.items(), 4):
        d = by_date.loc[by_date.index.isin(dates)]
        n = len(d)
        vals = [g, len(dates), n, int((d['降階次數'] > 0).sum()), int(d['降階次數'].sum()),
                int(d['小時制降階次數'].sum()), round(d['降階分鐘'].sum() / 60, 1),
                round(d['降階次數'].sum() / n, 2) if n else '—',
                round(d['小時制降階次數'].sum() / n, 2) if n else '—',
                round(d['降階分鐘'].mean(), 1) if n else '—']
        for c, v in enumerate(vals, 1):
            ws.cell(r, c, v).font = FONT_NORMAL

    row = 4 + len(groups) + 1
    for c, h in enumerate(['日期', '星期', '組別'] + _DEG_COLS, 1):
        _hdr(ws.cell(row, c, h))
    for d in sorted(groups['事前'] | groups['事後']):
        row += 1
        g = '、'.join(k for k, v in groups.items() if d in v)
        vals = ([by_date.loc[d, c] for c in _DEG_COLS] if d in by_date.index
                else ['無資料'] + [''] * (len(_DEG_COLS) - 1))
        for c, v in enumerate([d.strftime('%Y/%m/%d'), '一二三四五六日'[d.weekday()], g] + vals, 1):
            cell = ws.cell(row, c, v)
            cell.font = FONT_NORMAL
            if c == 4 + _DEG_COLS.index('降階分鐘'):
                cell.number_format = '0.0'

    for col, w in zip('ABCDEFGHIJ', (12, 8, 12, 12, 16, 22, 12, 40, 14, 14)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = ws.cell(4 + len(groups) + 2, 1)


def _write_section(ws, row: int, header: str, df: pd.DataFrame, metric: str, fill, total_cols: int = 5) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=total_cols)
    cell = ws.cell(row, 1, header)
    if fill == FILL_HEADER:
        cell.font = FONT_WHITE_BOLD
    else:
        cell.font = FONT_BOLD
    cell.fill = fill
    row += 1

    if df.empty:
        ws.cell(row, 1, '（無資料）').font = Font(italic=True, color='888888')
        return row + 1

    for _, data_row in df.iterrows():
        field = data_row['欄位']
        b = data_row['事前平均']
        a = data_row['事後平均']
        diff = data_row['差異']
        pct = data_row['改善%']

        ws.cell(row, 1, field).font = FONT_NORMAL
        _write_num(ws.cell(row, 2), b, metric)
        _write_num(ws.cell(row, 3), a, metric)
        _write_num(ws.cell(row, 4), diff, metric)

        _write_pct(ws.cell(row, 5), pct)

        for c in range(2, 6):
            ws.cell(row, c).alignment = Alignment(horizontal='right')
        row += 1

    return row


def _write_pct(cell, pct):
    if not pd.isna(pct):
        cell.value = pct
        cell.number_format = '+0.0%;-0.0%;0.0%'
        if pct > 0:
            cell.fill = FILL_GREEN
            cell.font = FONT_GREEN
        elif pct < 0:
            cell.fill = FILL_RED
            cell.font = FONT_RED
    else:
        cell.value = '—'


def _write_num(cell, val, metric: str):
    if pd.isna(val):
        cell.value = '—'
        return
    cell.value = val
    _apply_num_format(cell, metric)


def _apply_num_format(cell, metric: str):
    cell.number_format = '0'
    cell.font = FONT_NORMAL


def _delay_to_los(delay) -> str:
    """台灣公路容量手冊：號誌化路口平均停等延滯（秒/輛）→ 服務水準字母。"""
    if pd.isna(delay):
        return '—'
    for threshold, level in _LOS_THRESHOLDS:
        if delay <= threshold:
            return level
    return 'F'


def _write_los(cell, delay):
    los = _delay_to_los(delay)
    cell.value = los
    cell.alignment = Alignment(horizontal='center')
    if los in _LOS_FILL:
        cell.fill = _LOS_FILL[los]
        cell.font = _LOS_FONT[los]
    else:
        cell.font = FONT_NORMAL


# ── 全年效益估算（含 Excel 公式，同仁可直接檢查計算過程）────────────────────────

_BENEFIT_DETAIL_COLS = [
    # (表頭, 明細欄名 或 None=公式, 數值格式)
    ('日期類型', '日期類型', None), ('時段', '時段', None), ('尖峰', '尖峰', None),
    ('時數', '時數', '0.0'), ('代表時數', '代表時數', '0.0'), ('年天數', '年天數', '0'),
    ('事前日數', '事前日數', '0'), ('事後日數', '事後日數', '0'),
    ('事前總停等延滯 D前(車-秒/日)', 'D_before', '#,##0.0'), ('事後總停等延滯 D後(車-秒/日)', 'D_after', '#,##0.0'),
    ('事前通過量 V前(輛/日)', 'V_before', '#,##0.0'), ('事後通過量 V後(輛/日)', 'V_after', '#,##0.0'),
    ('ΔD_A 總延滯相減 =D前−D後', None, '#,##0.0'),
    ('ΔD_B 平均延滯差×事後通過量 =(D前/V前−D後/V後)×V後', None, '#,##0.0'),
    ('全年車-秒 A1 =ΔD_A×年天數', None, '#,##0'),
    ('全年車-秒 A2 =ΔD_A×代表時數/時數×年天數', None, '#,##0'),
    ('全年車-秒 B1 =ΔD_B×年天數', None, '#,##0'),
    ('全年車-秒 B2 =ΔD_B×代表時數/時數×年天數', None, '#,##0'),
    ('基準_A 事前總延滯 =D前', None, '#,##0.0'),
    ('基準_B 事前每車延滯×事後通過量 =D前/V前×V後', None, '#,##0.0'),
    ('全年基準 A1 =基準_A×年天數', None, '#,##0'),
    ('全年基準 A2 =基準_A×代表時數/時數×年天數', None, '#,##0'),
    ('全年基準 B1 =基準_B×年天數', None, '#,##0'),
    ('全年基準 B2 =基準_B×代表時數/時數×年天數', None, '#,##0'),
]

# (列名, 公式樣板, 數值格式)；{tot}/{base} = 該組合全年車-秒／全年基準的加總，{p[key]} = 參數儲存格，{r[n]} = 本欄第 n 列
_BENEFIT_STEPS = [
    ('全年效益值(車-秒)',   '={tot}',                        '#,##0'),
    ('全年效益值(車-小時)', '={r[0]}/3600',                  '#,##0.0'),
    ('節省停等時間(人-分鐘)', '={r[1]}*{p[occupancy]}*60',   '#,##0'),
    ('時間價值節省(元)',    '={r[1]}*{p[time_value]}',       '#,##0'),
    ('油耗節省(公升)',      '={r[1]}*{p[idle_fuel_rate]}',   '#,##0.0'),
    ('油耗節省(元)',        '={r[4]}*{p[fuel_price]}',       '#,##0'),
    ('CO2減少量(公噸)',     '={r[4]}*{p[co2_per_litre]}/1000', '#,##0.00'),
    ('CO2貨幣化效益(元)',   '={r[6]}*{p[co2_cost]}',         '#,##0'),
    ('全年貨幣化效益(元)',  '={r[3]}+{r[5]}+{r[7]}',         '#,##0'),
    ('改善率(停等時間、油耗、CO2 相同)', '={tot}/{base}',     '0.0%'),
]


def build_benefit_xlsx(detail: pd.DataFrame, best: str, cfg: dict, meta: dict[str, str],
                       used_dates: dict[tuple[str, str], tuple[list, list]],
                       notes: dict[tuple[pd.Timestamp, str], str] | None = None) -> io.BytesIO:
    """全年效益估算報告：全年效益（四種組合）→ 各時段 → 使用日期 → 參數。數值皆以 Excel 公式計算。

    used_dates：{(日期類型, 時段): (事前日期, 事後日期)}，列出各時段計算所用的日期。
    notes：{(日期, 時段): 資料品質註記}，列在「使用日期」工作表。
    """
    from benefit_logic import COMBOS, combo_label
    wb = Workbook()
    ws_sum = wb.active
    ws_sum.title = '全年效益'
    ws_det = wb.create_sheet('各時段')
    ws_dat = wb.create_sheet('使用日期')
    ws_par = wb.create_sheet('參數')

    # 參數
    p_cell = {}
    for c, h in enumerate(['參數', '數值', '單位', '出處'], 1):
        _hdr(ws_par.cell(1, c, h))
    for i, (key, v) in enumerate(cfg['params'].items(), 2):
        ws_par.cell(i, 1, v['label']).font = FONT_NORMAL
        ws_par.cell(i, 2, v['value']).font = FONT_BOLD
        ws_par.cell(i, 3, v['unit']).font = FONT_NORMAL
        ws_par.cell(i, 4, v['source']).font = FONT_NORMAL
        p_cell[key] = f"參數!$B${i}"
    ws_par.cell(len(cfg['params']) + 3, 1, f"參數更新日期：{cfg['updated']}").font = FONT_NORMAL
    for col, w in zip('ABCD', (28, 10, 12, 100)):
        ws_par.column_dimensions[col].width = w

    # 各時段
    for c, (h, _, _) in enumerate(_BENEFIT_DETAIL_COLS, 1):
        _hdr(ws_det.cell(1, c, h))
        ws_det.column_dimensions[get_column_letter(c)].width = 16
    L = {h.split(' ')[0]: get_column_letter(c) for c, (h, _, _) in enumerate(_BENEFIT_DETAIL_COLS, 1)}
    n = len(detail)
    for i, rec in enumerate(detail.to_dict('records'), 2):
        f = {k: f'{L[k]}{i}' for k in L}
        formulas = {
            'ΔD_A': f"={f['事前總停等延滯']}-{f['事後總停等延滯']}",
            'ΔD_B': f"=({f['事前總停等延滯']}/{f['事前通過量']}-{f['事後總停等延滯']}/{f['事後通過量']})*{f['事後通過量']}",
            '基準_A': f"={f['事前總停等延滯']}",
            '基準_B': f"={f['事前總停等延滯']}/{f['事前通過量']}*{f['事後通過量']}",
        }
        for c, (h, src, fmt) in enumerate(_BENEFIT_DETAIL_COLS, 1):
            if src is not None:
                val = rec[src]
                val = ('是' if val else '否') if src == '尖峰' else val
            elif h.startswith(('ΔD_', '基準_')):
                val = formulas[h.split(' ')[0]]
            else:
                combo = h.split(' ')[1]
                daily = 'ΔD_' if h.startswith('全年車-秒') else '基準_'
                dd = f"{L[daily + combo[0]]}{i}"
                scale = '' if combo[1] == '1' else f"*{f['代表時數']}/{f['時數']}"
                val = f"={dd}{scale}*{f['年天數']}"
            cell = ws_det.cell(i, c, val)
            cell.font = FONT_NORMAL
            if fmt:
                cell.number_format = fmt
    combo_cols = {h.split(' ')[1]: get_column_letter(c)
                  for c, (h, _, _) in enumerate(_BENEFIT_DETAIL_COLS, 1) if h.startswith('全年車-秒')}
    base_cols = {h.split(' ')[1]: get_column_letter(c)
                 for c, (h, _, _) in enumerate(_BENEFIT_DETAIL_COLS, 1) if h.startswith('全年基準')}
    ws_det.freeze_panes = 'C2'

    # 使用日期（每列一個日期；平均值取這些日期中有資料者）
    notes = notes or {}
    for c, h in enumerate(['日期類型', '時段', '事前/事後', '日期', '星期', '資料註記'], 1):
        _hdr(ws_dat.cell(1, c, h))
    i = 2
    for rec in detail.to_dict('records'):
        for label, dates in zip(('事前', '事後'), used_dates[(rec['日期類型'], rec['時段'])]):
            for d in sorted(dates):
                for c, v in enumerate([rec['日期類型'], rec['時段'], label, d.strftime('%Y/%m/%d'),
                                       '一二三四五六日'[d.weekday()], notes.get((pd.Timestamp(d), rec['時段']), '')], 1):
                    ws_dat.cell(i, c, v).font = FONT_NORMAL
                i += 1
    for col, w in zip('ABCDEF', (10, 14, 10, 12, 6, 80)):
        ws_dat.column_dimensions[col].width = w
    ws_dat.freeze_panes = 'A2'
    ws_dat.auto_filter.ref = f'A1:F{max(i - 1, 1)}'

    # 全年效益
    ws_sum['A1'] = '全年效益估算（停等延滯節省 → 時間價值、節油、減碳）'
    ws_sum['A1'].font = Font(name='微軟正黑體', bold=True, size=14)
    row = 2
    for k, v in meta.items():
        ws_sum.cell(row, 1, f'{k}：{v}').font = FONT_NORMAL
        row += 1
    row += 1
    top = row
    _hdr(ws_sum.cell(top, 1, '項目'))
    for j, (d, s) in enumerate(COMBOS, 2):
        code = f'{d}{s}'
        cell = ws_sum.cell(top, j, combo_label(d, s) + ('　★採用' if code == best else ''))
        _hdr(cell)
        cell.alignment = Alignment(wrap_text=True, horizontal='center', vertical='center')
        ws_sum.column_dimensions[get_column_letter(j)].width = 24
    ws_sum.row_dimensions[top].height = 48
    for k, (label, _, _) in enumerate(_BENEFIT_STEPS):
        ws_sum.cell(top + 1 + k, 1, label).font = FONT_BOLD
    for j, (d, s) in enumerate(COMBOS, 2):
        col = get_column_letter(j)
        r = [f'{col}{top + 1 + k}' for k in range(len(_BENEFIT_STEPS))]
        tot = f"SUM(各時段!{combo_cols[d + s]}2:{combo_cols[d + s]}{n + 1})" if n else '0'
        base = f"SUM(各時段!{base_cols[d + s]}2:{base_cols[d + s]}{n + 1})" if n else '1'
        for k, (_, tmpl, fmt) in enumerate(_BENEFIT_STEPS):
            cell = ws_sum.cell(top + 1 + k, j, tmpl.format(tot=tot, base=base, p=p_cell, r=r))
            cell.number_format = fmt
            cell.font = FONT_BOLD if f'{d}{s}' == best else FONT_NORMAL
            if f'{d}{s}' == best:
                cell.fill = FILL_GREEN
    ws_sum.column_dimensions['A'].width = 22
    notes = [
        '說明：',
        'ΔD（每日效益值，車-秒/日）：A = 事前平均總停等延滯 − 事後平均總停等延滯；'
        'B = (事前平均每車延滯 − 事後平均每車延滯) × 事後平均通過量（扣除事前後交通量差異）。',
        '全年化：1 = 只計實測時段 × 年天數；2 = 尖峰時段只代表自己，離峰時段分攤「每日計算時數 − 尖峰時數」後 × 年天數。',
        '四種組合皆列出，報告採全年貨幣化效益最高者（★）。效益值為負代表該組合下 AI 號誌延滯增加。',
        '改善率 = 全年效益值 ÷ 全年基準（A 基準 = 事前總延滯；B 基準 = 事前每車延滯 × 事後通過量，同樣放大到全年）；'
        '油耗、CO2 與停等時間成正比，三者改善率相同。',
        '人-分鐘 = 車-小時 × 小客車都會平均乘載率 × 60（每車皆以小客車乘載率計）。',
        '停等延滯為大車＋小車實際車數（未換算小客車當量、不含機車），油耗以小客車怠速油耗率估算。',
        '各時段使用的事前／事後日期見「使用日期」工作表；公式與參數出處詳見「各時段」「參數」工作表，及 ai-signal-perf/doc/全年效益估算說明.md。',
    ]
    row = top + len(_BENEFIT_STEPS) + 2
    for t in notes:
        ws_sum.cell(row, 1, t).font = FONT_NORMAL
        row += 1

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _hdr(cell):
    cell.font = FONT_WHITE_BOLD
    cell.fill = FILL_HEADER
    cell.alignment = Alignment(wrap_text=True, vertical='center')
