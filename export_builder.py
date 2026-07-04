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

from comparison_logic import LOWER_BETTER, METRIC_UNITS


def build_comparison_xlsx(
    all_results: dict[str, dict[str, pd.DataFrame]],
    before_dates: list,
    after_dates: list,
    include_travel_time: bool = True,
    raw_df: pd.DataFrame | None = None,
    raw_periods: list | None = None,
) -> io.BytesIO:
    wb = Workbook()
    wb.remove(wb.active)

    _build_info_sheet(wb, before_dates, after_dates)
    _build_summary_sheet(wb, all_results, before_dates, after_dates)
    for period, results in all_results.items():
        _build_period_sheet(wb, period, results, before_dates, after_dates, include_travel_time)
    if raw_df is not None and raw_periods:
        _build_raw_data_sheet(wb, raw_df, before_dates, after_dates, raw_periods)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _fmt_date(d) -> str:
    _WD = ['一', '二', '三', '四', '五', '六', '日']
    ts = d if isinstance(d, pd.Timestamp) else pd.Timestamp(d)
    return f"{ts.strftime('%Y/%m/%d')} (週{_WD[ts.weekday()]})"


def _build_info_sheet(wb, before_dates, after_dates):
    from datetime import datetime
    ws = wb.create_sheet('分析說明')

    ws.merge_cells('A1:C1')
    ws['A1'] = 'AI 號誌績效比較分析'
    ws['A1'].font = Font(name='微軟正黑體', bold=True, size=14)
    ws['A1'].alignment = Alignment(horizontal='center')

    ws['A2'] = f"產製時間：{datetime.now().strftime('%Y/%m/%d %H:%M')}"
    ws['A2'].font = Font(name='微軟正黑體', italic=True, color='666666')

    row = 4
    for label, dates, fill in [
        (f'事前日期（定時時制，共 {len(before_dates)} 天）', before_dates, FILL_SUBHEADER),
        (f'事後日期（AI 號誌，共 {len(after_dates)} 天）',   after_dates,  FILL_SUMMARY_HDR),
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

    ws.column_dimensions['A'].width = 28


def _build_summary_sheet(wb, all_results, before_dates, after_dates):
    ws = wb.create_sheet('總表')
    periods = list(all_results.keys())

    # 標題列
    ws.cell(1, 1, '指標').font = FONT_BOLD
    ws.cell(1, 1).fill = FILL_SUMMARY_HDR
    ws.cell(2, 1, '欄位').font = FONT_BOLD
    ws.cell(2, 1).fill = FILL_SUMMARY_HDR

    col_start = 2
    for p, period in enumerate(periods):
        c = col_start + p * 2
        ws.merge_cells(start_row=1, start_column=c, end_row=1, end_column=c + 1)
        ws.cell(1, c, period).font = FONT_BOLD
        ws.cell(1, c).fill = FILL_SUMMARY_HDR
        ws.cell(1, c).alignment = Alignment(horizontal='center')
        ws.cell(2, c, '事前平均').font = FONT_BOLD
        ws.cell(2, c).fill = FILL_SUMMARY_HDR
        ws.cell(2, c + 1, '事後平均').font = FONT_BOLD
        ws.cell(2, c + 1).fill = FILL_SUMMARY_HDR

    row = 3
    # 取第一個時段的第一個指標結果，從中讀取系統欄名稱（不硬編碼）
    first_period = periods[0]
    first_metric_df = next(
        (v for v in all_results[first_period].values()
         if isinstance(v, pd.DataFrame) and not v.empty and '欄位' in v.columns),
        pd.DataFrame(),
    )
    # 系統總量欄：取第一個時段第一個指標的前幾筆（通常是系統層級聚合欄）
    # 實際上系統欄由 data_loader.get_system_columns() 決定，此處從結果欄位清單取得
    import data_loader as _dl
    sys_col_names = _dl.get_system_columns()
    # 轉成顯示名稱（compare_comparison 已套用 display_name，但系統欄名稱通常不需轉換）
    sys_display = [_dl.get_display_name(c) for c in sys_col_names]

    metrics_in_results = [k for k in all_results[first_period] if k != '旅行時間']
    for metric in metrics_in_results:
        ws.cell(row, 1, f'▶ {metric}').font = FONT_WHITE_BOLD
        ws.cell(row, 1).fill = FILL_HEADER
        ws.merge_cells(start_row=row, start_column=1,
                       end_row=row, end_column=1 + len(periods) * 2)
        row += 1
        for field in sys_display:
            ws.cell(row, 1, field).font = FONT_NORMAL
            for p, period in enumerate(periods):
                c = col_start + p * 2
                df = all_results[period].get(metric, pd.DataFrame())
                r = df[df['欄位'] == field] if not df.empty else pd.DataFrame()
                if not r.empty:
                    b, a = r['事前平均'].values[0], r['事後平均'].values[0]
                    ws.cell(row, c, b if not pd.isna(b) else None)
                    ws.cell(row, c + 1, a if not pd.isna(a) else None)
                    _apply_num_format(ws.cell(row, c), metric)
                    _apply_num_format(ws.cell(row, c + 1), metric)
            row += 1

    # 欄寬
    ws.column_dimensions['A'].width = 18
    for col in range(2, 2 + len(periods) * 2):
        ws.column_dimensions[get_column_letter(col)].width = 14

    # 說明
    ws.cell(row + 1, 1, f'事前：{len(before_dates)} 日  事後：{len(after_dates)} 日').font = Font(italic=True, color='666666')


def _build_period_sheet(wb, period, results, before_dates, after_dates, include_travel_time):
    safe_name = period.replace(':', '').replace('~', '-')[:31]
    ws = wb.create_sheet(safe_name)

    # 第一列：時段標題
    ws.merge_cells('A1:E1')
    ws.cell(1, 1, f"{period}  │  事前：{len(before_dates)} 日  │  事後：{len(after_dates)} 日")
    ws.cell(1, 1).font = Font(name='微軟正黑體', bold=True, size=12)
    ws.cell(1, 1).alignment = Alignment(horizontal='center')

    # 第二列：欄位標頭
    headers = ['項目', '事前平均', '事後平均', '差異', '改善%']
    for c, h in enumerate(headers, 1):
        cell = ws.cell(2, c, h)
        cell.font = FONT_WHITE_BOLD
        cell.fill = FILL_HEADER
        cell.alignment = Alignment(horizontal='center')

    row = 3
    # 依指標順序寫入（排除旅行時間；旅行時間接在擁有旅行時間資料的指標後面）
    tt_df = results.get('旅行時間', pd.DataFrame())
    tt_written = False
    metrics_in_results = [k for k in results if k != '旅行時間']
    for metric in metrics_in_results:
        mdf = results.get(metric, pd.DataFrame())
        unit = METRIC_UNITS.get(metric, '')
        row = _write_section(ws, row, f'▶ {metric} ({unit})', mdf, metric, FILL_HEADER)
        # 旅行時間接在第一個有旅行時間資料的量測指標之後
        if include_travel_time and not tt_written and not tt_df.empty:
            row = _write_section(ws, row, '   ▶▶ 旅行時間廊道 (秒)', tt_df, '旅行時間', FILL_SUBHEADER)
            tt_written = True

    # 欄寬
    ws.column_dimensions['A'].width = 30
    for col_letter in ['B', 'C', 'D', 'E']:
        ws.column_dimensions[col_letter].width = 14

    # 凍結前兩列
    ws.freeze_panes = 'A3'


def _build_raw_data_sheet(wb, df: pd.DataFrame, before_dates, after_dates, periods):
    ws = wb.create_sheet('原始資料')

    before_set = {pd.Timestamp(d) for d in before_dates}
    after_set   = {pd.Timestamp(d) for d in after_dates}
    all_date_set = before_set | after_set

    raw = df[df['時段'].isin(periods) & df['日期'].isin(all_date_set)].copy()
    raw.insert(0, '分組', raw['日期'].apply(
        lambda d: '事前' if d in before_set else '事後'
    ))
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


def _write_section(ws, row: int, header: str, df: pd.DataFrame, metric: str, fill) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
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

        pct_cell = ws.cell(row, 5)
        if not pd.isna(pct):
            pct_cell.value = pct
            pct_cell.number_format = '+0.0%;-0.0%;0.0%'
            if pct > 0:
                pct_cell.fill = FILL_GREEN
                pct_cell.font = FONT_GREEN
            elif pct < 0:
                pct_cell.fill = FILL_RED
                pct_cell.font = FONT_RED
        else:
            pct_cell.value = '—'

        for c in range(2, 6):
            ws.cell(row, c).alignment = Alignment(horizontal='right')
        row += 1

    return row


def _write_num(cell, val, metric: str):
    if pd.isna(val):
        cell.value = '—'
        return
    cell.value = val
    _apply_num_format(cell, metric)


def _apply_num_format(cell, metric: str):
    # LOWER_BETTER 指標為小數格式；非 LOWER_BETTER（通過量類）為整數格式
    cell.number_format = '#,##0' if metric not in LOWER_BETTER else '#,##0.0'
    cell.font = FONT_NORMAL
