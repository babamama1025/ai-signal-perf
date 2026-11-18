import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_loader as dl  # noqa: E402


def test_missing_file_is_empty(tmp_path):
    assert dl.load_ai_mode(tmp_path / 'none.csv').empty


def test_grid(tmp_path):
    """A：07:00~07:30 定時、07:30~08:00 動態；B 只在第二天有資料 → 第一天為未知列；迄可為 24:00:00。"""
    p = tmp_path / 'm.csv'
    p.write_text('日期,路口,起,迄,模式\n'
                 '2026-07-01,1 甲,07:00:00,07:30:00,T\n'
                 '2026-07-01,1 甲,07:30:00,08:00:00,A\n'
                 '2026-07-01,1 甲,23:00:00,24:00:00,A\n'
                 '2026-07-02,2 乙,07:00:00,07:10:00,A\n', encoding='utf-8-sig')
    d = dl.load_ai_mode(p)
    day = pd.Timestamp('2026-07-01')
    ai, state = dl.ai_mode_grid(d, day, 60)
    assert list(ai.index) == ['1 甲', '2 乙'] and len(ai.columns) == 24
    assert state.loc['1 甲', '07'] == dl.MODE_MIXED and ai.loc['1 甲', '07'] == 30
    assert state.loc['1 甲', '23'] == dl.MODE_AI and ai.loc['1 甲', '23'] == 60
    assert state.loc['1 甲', '06'] == dl.MODE_UNKNOWN and np.isnan(ai.loc['1 甲', '06'])
    assert (state.loc['2 乙'] == dl.MODE_UNKNOWN).all()
    ai, state = dl.ai_mode_grid(d, day, 15)
    assert state.loc['1 甲', '07:00'] == dl.MODE_TOD and ai.loc['1 甲', '07:00'] == 0
    assert state.loc['1 甲', '07:45'] == dl.MODE_AI and ai.loc['1 甲', '07:45'] == 15
