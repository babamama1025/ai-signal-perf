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

METRICS = ['總停等延滯', '通過量', '平均停等延滯']
METRIC_UNITS = {'總停等延滯': '秒', '通過量': '輛', '平均停等延滯': '秒/輛', '旅行時間': '秒'}


def build_comparison_xlsx(
    all_results: dict[str, dict[str, pd.DataFrame]],
    before_dates: list,
    after_dates: list,
    include_travel_time: bool = True,
) -> io.BytesIO:
    wb = Workbook()
    wb.remove(wb.active)

    _build_summary_sheet(wb, all_results, before_dates, after_dates)
    for period, results in all_results.items():
        _build_period_sheet(wb, period, results, before_dates, after_dates, include_travel_time)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


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
    sys_cols = ['系統', '高鐵周邊', 'A19周邊', '前期範圍']
    for metric in METRICS:
        ws.cell(row, 1, f'▶ {metric}').font = FONT_WHITE_BOLD
        ws.cell(row, 1).fill = FILL_HEADER
        ws.merge_cells(start_row=row, start_column=1,
                       end_row=row, end_column=1 + len(periods) * 2)
        row += 1
        for field in sys_cols:
            ws.cell(row, 1, field).font = FONT_NORMAL
            for p, period in enumerate(periods):
                c = col_start + p * 2
                df = all_results[period].get(metric, pd.DataFrame())
                r = df[df['欄位'] == field] if not df.empty else pd.DataFrame()
                if not r.empty:
                    b, a = r['事前平均'].values[0], r['事後平均'].values[0]
                    fmt = '{:,.0f}' if metric == '通過量' else '{:,.1f}'
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
    for metric in METRICS:
        df = results.get(metric, pd.DataFrame())
        unit = METRIC_UNITS.get(metric, '')
        row = _write_section(ws, row, f'▶ {metric} ({unit})', df, metric, FILL_HEADER)

        if include_travel_time and metric == '總停等延滯':
            tt_df = results.get('旅行時間', pd.DataFrame())
            if not tt_df.empty:
                row = _write_section(ws, row, '   ▶▶ 旅行時間廊道 (秒)', tt_df, '旅行時間', FILL_SUBHEADER)

    # 欄寬
    ws.column_dimensions['A'].width = 30
    for col_letter in ['B', 'C', 'D', 'E']:
        ws.column_dimensions[col_letter].width = 14

    # 凍結前兩列
    ws.freeze_panes = 'A3'


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
    if metric == '通過量':
        cell.number_format = '#,##0'
    else:
        cell.number_format = '#,##0.0'
    cell.font = FONT_NORMAL
