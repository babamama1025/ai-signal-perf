import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_loader as dl  # noqa: E402


def test_missing_file_is_empty(tmp_path):
    assert dl.load_degradation(tmp_path / 'none.csv').empty


def test_monthly(tmp_path):
    p = tmp_path / 'd.csv'
    p.write_text('日期,星期,降階次數,小時制降階次數,降階小時區間,降階分鐘,主要原因\n'
                 '2026-07-01,三,3,2,09-10、15-16,30.0,系統關閉 2、TC與IPC步階不一致 1\n'
                 '2026-07-02,四,0,0,,0.0,\n'
                 '2026-08-01,六,1,1,17-18,90.0,其他 1\n', encoding='utf-8-sig')
    d = dl.load_degradation(p)
    assert d.loc[1, '主要原因'] == ''
    m = dl.degradation_monthly(d).set_index('月份')
    assert m.loc['2026/07', ['天數', '有降階天數', '降階次數', '小時制降階次數']].tolist() == [2, 1, 3, 2]
    assert m.loc['2026/07', '降階時數'] == 0.5 and m.loc['2026/07', '日均降階分鐘'] == 15.0
    assert m.loc['2026/08', '日均小時制次數'] == 1.0
