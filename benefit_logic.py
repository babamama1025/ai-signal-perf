"""全年效益估算：停等延滯節省 → 時間價值、節油、減碳

公式、參數出處與限制見 doc/全年效益估算說明.md；參數值在 data/benefit_params.json。
本模組只做計算，不碰 UI。

每個「日期類型 × 時段」先算每日效益值 ΔD（車-秒/日），兩種算法：
- A 總延滯相減：ΔD = D̄前 − D̄後
- B 平均延滯差 × 事後通過量：ΔD = (D̄前/V̄前 − D̄後/V̄後) × V̄後
再放大成全年，兩種算法：
- 1 只計實測時段：全年 = Σ ΔD × 年天數
- 2 時數放大至全日：全年 = Σ ΔD × (代表時數 / 時段時數) × 年天數
四種組合都算，報告採全年貨幣化效益最高者。

改善率 = 全年效益值 ÷ 全年基準延滯（同一組合、同樣的放大方式）：
- A 基準 = D̄前（事前總延滯）
- B 基準 = D̄前/V̄前 × V̄後（事前每車延滯 × 事後通過量）
節油、減碳都與車-小時成正比，所以三者改善率相同。

減少旅行時間（人-分鐘）：有「總加權旅行時間」欄的場域（Σ 路徑旅行時間 × 代表路段流量，車-秒），
以同樣的 A/B × 1/2 組合換算（A 用總量相減；B 的流量 W = 總加權旅行時間 ÷ 加權平均旅行時間），
再 × 乘載率 × 60 / 3600。只是換算，不計入貨幣化（旅行時間含停等延滯，避免重複）。
"""
import json
from pathlib import Path

import pandas as pd

PARAMS_PATH = Path(__file__).parent / 'data' / 'benefit_params.json'

WEEKDAY, WEEKEND = '平日', '假日'
DAILY_METHODS = {'A': '總延滯相減', 'B': '平均延滯差×事後通過量'}
SCALE_METHODS = {'1': '只計實測時段', '2': '時數放大至全日'}
COMBOS = [(d, s) for d in DAILY_METHODS for s in SCALE_METHODS]
TT_TOTAL_COL, TT_AVG_COL = '總加權旅行時間', '加權平均旅行時間'


def load_params(path: Path = PARAMS_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def param_values(cfg: dict) -> dict[str, float]:
    return {k: v['value'] for k, v in cfg['params'].items()}


# ── 時段 ────────────────────────────────────────────────────────────────────

def _span(period: str) -> tuple[float, float]:
    """'HH:MM~HH:MM' → (起, 迄) 小時。"""
    t1, t2 = period.split('~')
    to_h = lambda t: int(t.split(':')[0]) + int(t.split(':')[1]) / 60
    return to_h(t1), to_h(t2)


def period_hours(period: str) -> float:
    s, e = _span(period)
    return e - s


def _overlap_hours(a: str, b: str) -> float:
    (s1, e1), (s2, e2) = _span(a), _span(b)
    return max(0.0, min(e1, e2) - max(s1, s2))


def overlapping_pairs(periods: list[str]) -> list[tuple[str, str]]:
    """回傳互相重疊的時段組合（重疊會重複計算延滯）。"""
    return [(a, b) for i, a in enumerate(periods) for b in periods[i + 1:]
            if _overlap_hours(a, b) > 0]


def is_peak(period: str, peak_windows: list[str]) -> bool:
    """時段有一半以上落在尖峰窗內即視為尖峰。"""
    inside = sum(_overlap_hours(period, w) for w in peak_windows)
    return inside >= period_hours(period) / 2


def represented_hours(periods: list[str], peak_flags: dict[str, bool],
                      covered_hours: float) -> dict[str, float]:
    """時數放大法的代表時數。

    尖峰時段只代表自己；離峰時段依長度比例分攤「每日計算時數 − 尖峰時數」。
    沒有離峰時段、或剩餘時數不比離峰時段本身長時，不放大（代表時數 = 時段時數）。
    """
    hours = {p: period_hours(p) for p in periods}
    off = [p for p in periods if not peak_flags[p]]
    remaining = covered_hours - sum(hours[p] for p in periods if peak_flags[p])
    off_total = sum(hours[p] for p in off)
    out = dict(hours)
    if off and remaining > off_total:
        for p in off:
            out[p] = remaining * hours[p] / off_total
    return out


# ── 日期分配 ────────────────────────────────────────────────────────────────

def preset_dates(preset: dict, weekend: bool) -> dict[str, tuple[list, list]]:
    """已存日期分配 → {時段: (事前日期, 事後日期)}；只取符合日期類型的日期。"""
    out: dict[str, tuple[list, list]] = {p: ([], []) for p in preset['periods']}
    for r in preset['rows']:
        d = pd.Timestamp(r['日期'])
        if r['時段'] not in out or (d.weekday() >= 5) != weekend:
            continue
        if r['事前']:
            out[r['時段']][0].append(d)
        if r['事後']:
            out[r['時段']][1].append(d)
    return out


def period_means(df: pd.DataFrame, period: str, before: list, after: list,
                 entity: str) -> dict[str, float]:
    """事前／事後的平均每日總停等延滯（車-秒）與通過量（輛）。"""
    pdf = df[df['時段'] == period]

    def _mean(metric, dates):
        rows = pdf[(pdf['指標'] == metric) & pdf['日期'].isin(set(dates))]
        return float(rows[entity].mean()) if entity in rows.columns and not rows.empty else float('nan')

    return {
        'D_before': _mean('總停等延滯', before), 'D_after': _mean('總停等延滯', after),
        'V_before': _mean('通過量', before),     'V_after': _mean('通過量', after),
    }


def period_tt_means(df: pd.DataFrame, period: str, before: list, after: list) -> dict[str, float]:
    """事前／事後的平均每日總加權旅行時間 T（車-秒）與加權流量 W = T ÷ 加權平均旅行時間（輛）。
    只用兩欄都有值的日期；系統層級（不分計算對象）。"""
    rows = df[(df['時段'] == period) & df[TT_TOTAL_COL].notna() & (df[TT_AVG_COL] > 0)]

    def _means(dates):
        r = rows[rows['日期'].isin(set(dates))]
        if r.empty:
            return float('nan'), float('nan')
        return float(r[TT_TOTAL_COL].mean()), float((r[TT_TOTAL_COL] / r[TT_AVG_COL]).mean())

    (tb, wb), (ta, wa) = _means(before), _means(after)
    return {'T_before': tb, 'T_after': ta, 'W_before': wb, 'W_after': wa}


def _tt_as_delay(r: dict) -> dict[str, float]:
    """讓旅行時間沿用 daily_saving／daily_baseline：T 對應 D、W 對應 V。"""
    return {'D_before': r['T_before'], 'D_after': r['T_after'],
            'V_before': r['W_before'], 'V_after': r['W_after']}


def daily_saving(m: dict[str, float], method: str) -> float:
    """每日效益值 ΔD（車-秒/日），正值 = AI 號誌減少延滯。"""
    if method == 'A':
        return m['D_before'] - m['D_after']
    if not (m['V_before'] and m['V_after']):
        return float('nan')
    return (m['D_before'] / m['V_before'] - m['D_after'] / m['V_after']) * m['V_after']


def daily_baseline(m: dict[str, float], method: str) -> float:
    """改善率的分母：事前時制下的每日延滯（車-秒/日），與 daily_saving 同口徑。"""
    if method == 'A':
        return m['D_before']
    if not (m['V_before'] and m['V_after']):
        return float('nan')
    return m['D_before'] / m['V_before'] * m['V_after']


# ── 全年化與貨幣化 ──────────────────────────────────────────────────────────

def build_detail(items: list[dict], params: dict[str, float]) -> pd.DataFrame:
    """items：[{日期類型, 時段, 尖峰, D_before, D_after, V_before, V_after, 事前日數, 事後日數}]，
    可另含 T_before, T_after, W_before, W_after（period_tt_means）。

    回傳各時段明細，含兩種每日效益值與基準、代表時數、年天數及四種組合的全年車-秒與基準車-秒；
    有 T 時另含旅行時間的 ΔT、基準T、年旅行車秒、年旅行基準。
    """
    detail = pd.DataFrame(items)
    if detail.empty:
        return detail
    detail['時數'] = detail['時段'].map(period_hours)
    detail['代表時數'] = 0.0
    for dtype, g in detail.groupby('日期類型'):
        rep = represented_hours(list(g['時段']), dict(zip(g['時段'], g['尖峰'])), params['covered_hours'])
        detail.loc[g.index, '代表時數'] = g['時段'].map(rep)
    detail['年天數'] = detail['日期類型'].map(
        {WEEKDAY: params['days_weekday'], WEEKEND: params['days_weekend']})
    for d in DAILY_METHODS:
        recs = detail.to_dict('records')
        detail[f'ΔD_{d}'] = [daily_saving(r, d) for r in recs]
        detail[f'基準_{d}'] = [daily_baseline(r, d) for r in recs]
        if 'T_before' in detail:
            detail[f'ΔT_{d}'] = [daily_saving(_tt_as_delay(r), d) for r in recs]
            detail[f'基準T_{d}'] = [daily_baseline(_tt_as_delay(r), d) for r in recs]
    for d, s in COMBOS:
        scale = 1.0 if s == '1' else detail['代表時數'] / detail['時數']
        detail[f'年車秒_{d}{s}'] = detail[f'ΔD_{d}'] * scale * detail['年天數']
        detail[f'年基準_{d}{s}'] = detail[f'基準_{d}'] * scale * detail['年天數']
        if 'T_before' in detail:
            detail[f'年旅行車秒_{d}{s}'] = detail[f'ΔT_{d}'] * scale * detail['年天數']
            detail[f'年旅行基準_{d}{s}'] = detail[f'基準T_{d}'] * scale * detail['年天數']
    return detail


def monetize(vehicle_seconds: float, params: dict[str, float]) -> dict[str, float]:
    """全年車-秒 → 車-小時、人-分鐘、時間價值、節油、減碳、合計（除人-分鐘外與 Excel 範本同順序）。"""
    hours = vehicle_seconds / 3600
    fuel = hours * params['idle_fuel_rate']
    co2 = fuel * params['co2_per_litre'] / 1000
    out = {
        '全年效益值(車-秒)':    vehicle_seconds,
        '全年效益值(車-小時)':  hours,
        '節省停等時間(人-分鐘)': hours * params['occupancy'] * 60,
        '時間價值節省(元)':     hours * params['time_value'],
        '油耗節省(公升)':       fuel,
        '油耗節省(元)':         fuel * params['fuel_price'],
        'CO2減少量(公噸)':      co2,
        'CO2貨幣化效益(元)':    co2 * params['co2_cost'],
    }
    out['全年貨幣化效益(元)'] = out['時間價值節省(元)'] + out['油耗節省(元)'] + out['CO2貨幣化效益(元)']
    return out


def combo_label(d: str, s: str) -> str:
    return f'{d}{s}：{DAILY_METHODS[d]}／{SCALE_METHODS[s]}'


def summarize(detail: pd.DataFrame, params: dict[str, float]) -> tuple[pd.DataFrame, str]:
    """四種組合的全年效益表（列 = 組合，含改善率）及效益最高的組合代碼（如 'B2'）。

    有缺值（事前或事後無資料）的時段不計入。明細有旅行時間時另含減少旅行時間（車-秒、人-分鐘）
    與旅行時間改善率，只加總有旅行時間的時段，不計入貨幣化效益。
    """
    rows = {}
    for d, s in COMBOS:
        ok = detail[[f'年車秒_{d}{s}', f'年基準_{d}{s}']].dropna() if not detail.empty else None
        total = float(ok[f'年車秒_{d}{s}'].sum()) if ok is not None else 0.0
        base = float(ok[f'年基準_{d}{s}'].sum()) if ok is not None else 0.0
        rows[f'{d}{s}'] = {'組合': combo_label(d, s), **monetize(total, params),
                           '改善率': total / base if base else float('nan')}
        if f'年旅行車秒_{d}{s}' in detail:
            tt = detail[[f'年旅行車秒_{d}{s}', f'年旅行基準_{d}{s}']].dropna()
            t_tot, t_base = float(tt[f'年旅行車秒_{d}{s}'].sum()), float(tt[f'年旅行基準_{d}{s}'].sum())
            nan = float('nan')
            rows[f'{d}{s}'].update({
                '減少旅行時間(車-秒)':   t_tot if len(tt) else nan,
                '減少旅行時間(人-分鐘)': t_tot / 3600 * params['occupancy'] * 60 if len(tt) else nan,
                '旅行時間改善率':        t_tot / t_base if t_base else nan,
            })
    table = pd.DataFrame.from_dict(rows, orient='index')
    best = table['全年貨幣化效益(元)'].idxmax()
    return table, best
