import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benefit_logic as bl  # noqa: E402

# 節能減碳效益計算表「大竹二期」工作表的參數（油價為 2023Q2 桃園均價）
EXCEL_PARAMS = {
    'days_weekday': 260, 'days_weekend': 104, 'covered_hours': 16,
    'time_value': 5.93 * 60, 'idle_fuel_rate': 1.54, 'fuel_price': 30.49,
    'co2_per_litre': 2.263, 'co2_cost': 585,
}


def _item(dtype, period, d_b, d_a, v_b=1000.0, v_a=1000.0, peak=True):
    return {'日期類型': dtype, '時段': period, '尖峰': peak,
            'D_before': d_b, 'D_after': d_a, 'V_before': v_b, 'V_after': v_a,
            '事前日數': 5, '事後日數': 5}


def test_reproduces_excel_dazhu_phase2():
    """平日全天 ΔD × 260 ＋ 假日昏峰 ΔD × 104，A1 組合應與 Excel 結果一致。"""
    detail = bl.build_detail([
        _item(bl.WEEKDAY, '07:00~18:30', 2144614, 1678078),
        _item(bl.WEEKEND, '16:00~18:30', 533201, 419900),
    ], EXCEL_PARAMS)
    table, _ = bl.summarize(detail, EXCEL_PARAMS)
    r = table.loc['A1']
    assert r['全年效益值(車-秒)'] == 133082664
    assert r['全年效益值(車-小時)'] == pytest.approx(36967.4, abs=0.05)
    assert r['時間價值節省(元)'] == pytest.approx(13153003, abs=1)
    assert r['油耗節省(公升)'] == pytest.approx(56929.8, abs=0.05)
    assert r['油耗節省(元)'] == pytest.approx(1735790, abs=1)
    assert r['CO2減少量(公噸)'] == pytest.approx(128.83, abs=0.005)
    assert r['CO2貨幣化效益(元)'] == pytest.approx(75367, abs=1)
    assert r['全年貨幣化效益(元)'] == pytest.approx(14964160, abs=1)
    # 改善率 = 效益值 ÷ 原時制（範本 H 欄為各時段 F/D）
    assert r['改善率'] == pytest.approx(133082664 / (260 * 2144614 + 104 * 533201))


def test_improvement_rate_method_b():
    # 每車 100 → 75 秒、事後 1200 輛：B 的基準 = 100 × 1200，改善率 25%
    detail = bl.build_detail([_item(bl.WEEKDAY, '07:00~09:00', 100000, 90000, 1000, 1200)], EXCEL_PARAMS)
    table, _ = bl.summarize(detail, EXCEL_PARAMS)
    assert table.loc['B1', '改善率'] == pytest.approx(0.25)
    assert table.loc['A1', '改善率'] == pytest.approx(0.10)


def test_daily_saving_methods():
    m = {'D_before': 100000.0, 'D_after': 90000.0, 'V_before': 1000.0, 'V_after': 1200.0}
    assert bl.daily_saving(m, 'A') == 10000.0
    # 每車 100 → 75 秒，以事後 1200 輛計
    assert bl.daily_saving(m, 'B') == pytest.approx(25 * 1200)


def test_peak_classification():
    w = ['07:00~09:00', '17:00~19:00']
    assert bl.is_peak('07:00~09:00', w)
    assert bl.is_peak('16:00~19:00', w)       # 2/3 在尖峰窗內
    assert bl.is_peak('17:30~18:30', w)
    assert not bl.is_peak('12:00~14:00', w)
    assert not bl.is_peak('06:30~20:30', w)   # 全天時段只有 4/14 在尖峰窗內


def test_represented_hours():
    periods = ['07:00~09:00', '12:00~14:00', '17:00~19:00']
    flags = {'07:00~09:00': True, '12:00~14:00': False, '17:00~19:00': True}
    rep = bl.represented_hours(periods, flags, 16)
    assert rep == {'07:00~09:00': 2, '12:00~14:00': 12, '17:00~19:00': 2}
    # 沒有離峰時段：不放大
    assert bl.represented_hours(['07:00~09:00'], {'07:00~09:00': True}, 16) == {'07:00~09:00': 2}


def test_scale_method_2_and_best_combo():
    detail = bl.build_detail([
        _item(bl.WEEKDAY, '07:00~09:00', 1000, 400, peak=True),
        _item(bl.WEEKDAY, '12:00~14:00', 1000, 900, peak=False),
    ], EXCEL_PARAMS)
    table, best = bl.summarize(detail, EXCEL_PARAMS)
    assert table.loc['A1', '全年效益值(車-秒)'] == (600 + 100) * 260
    assert table.loc['A2', '全年效益值(車-秒)'] == (600 + 100 * 14 / 2) * 260
    assert best == 'A2'  # 通過量相同時 A 與 B 相等，取先出現者


def test_overlapping_pairs():
    assert bl.overlapping_pairs(['07:00~09:00', '06:30~20:30', '20:30~21:00']) == [('07:00~09:00', '06:30~20:30')]


def test_preset_dates_filters_day_type():
    preset = {'periods': ['07:00~09:00'], 'rows': [
        {'日期': '2026/06/01', '時段': '07:00~09:00', '事前': True, '事後': False},   # 一
        {'日期': '2026/06/06', '時段': '07:00~09:00', '事前': True, '事後': False},   # 六
        {'日期': '2026/07/01', '時段': '07:00~09:00', '事前': False, '事後': True},
    ]}
    before, after = bl.preset_dates(preset, weekend=False)['07:00~09:00']
    assert before == [pd.Timestamp('2026-06-01')]
    assert after == [pd.Timestamp('2026-07-01')]
