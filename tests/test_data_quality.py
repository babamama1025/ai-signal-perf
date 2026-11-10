import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_loader as dl  # noqa: E402


def _notes(tmp_path):
    p = tmp_path / 'q.csv'
    p.write_text('日期,時段,等級,規則,說明\n'
                 '2026-09-22,17:00~19:00,警告,完整度低,資料完整度 流量 64%、停等 57%\n'
                 '2026-09-22,17:00~19:00,警告,通過量異常,-55%\n'
                 '2026-10-13,07:00~09:00,錯誤,無VD資料,區域通過量為 0 或缺值\n'
                 '2026-10-19,07:00~09:00,排除,外部壅塞,平均延滯 +82%\n', encoding='utf-8-sig')
    return dl.quality_notes(dl.load_quality(p))


def test_missing_file_is_empty(tmp_path):
    assert dl.quality_notes(dl.load_quality(tmp_path / 'none.csv')) == {}


def test_notes_and_exclusion(tmp_path):
    notes = _notes(tmp_path)
    warn = notes[(pd.Timestamp('2026-09-22'), '17:00~19:00')]
    err = notes[(pd.Timestamp('2026-10-13'), '07:00~09:00')]
    assert warn['short'] == '⚠️ 完整度低、⚠️ 通過量異常' and not warn['error']
    assert err['error'] and err['text'] == '錯誤：無VD資料（區域通過量為 0 或缺值）'
    none, errors, both = dl.QUALITY_EXCLUDE
    assert [dl.is_excluded(warn, m) for m in (none, errors, both)] == [False, False, True]
    assert [dl.is_excluded(err, m) for m in (none, errors, both)] == [False, True, True]
    assert not dl.is_excluded(None, both)
    drop = notes[(pd.Timestamp('2026-10-19'), '07:00~09:00')]
    assert drop['short'] == '🚧 外部壅塞' and drop['level'] == '排除'
    assert [dl.is_excluded(drop, m) for m in (none, errors, both)] == [False, True, True]


def test_flagged_rows(tmp_path):
    notes = _notes(tmp_path)
    rows = dl.flagged_rows(notes, {'17:00~19:00': ([pd.Timestamp('2026-09-21'), pd.Timestamp('2026-09-22')], [])})
    assert [(r['組別'], r['日期'], r['等級']) for r in rows] == [('事前', '2026/09/22 (週二)', '警告')]
    rows = dl.flagged_rows(notes, {'07:00~09:00': ([pd.Timestamp('2026-10-13')], [pd.Timestamp('2026-10-19')])})
    assert [r['等級'] for r in rows] == ['錯誤', '排除']
    assert dl.flag_counts(rows) == '⛔ 錯誤 1、🚧 排除 1、⚠️ 警告 0'
