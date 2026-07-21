# -*- coding: utf-8 -*-
"""時序數據載入模組（含快取與舊 ID 正規化）"""
from __future__ import annotations
import numpy as np
import pandas as pd
import streamlit as st
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data'

_TRAVELTIME_FILES = {
    '桃園四期(大湳)': DATA_DIR / 'traveltime_4.parquet',
}
_PERF_FILES = {
    '桃園四期(大湳)': DATA_DIR / 'perf_summary_4.csv',
}


@st.cache_data(show_spinner='載入旅行時間資料…')
def load_traveltime(site: str) -> pd.DataFrame | None:
    """載入旅行時間 parquet，篩選 cartype==0，並將舊路段 ID 正規化為新 ID。"""
    from ts_config_4 import OLD_TO_NEW_ID
    path = _TRAVELTIME_FILES.get(site)
    if path is None or not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df[df['cartype'] == 0].copy()
    df['time'] = pd.to_datetime(df['time'])
    df['roadid'] = df['roadid'].replace(OLD_TO_NEW_ID)
    df['traveltime'] = df['traveltime'].where(df['traveltime'] > 0, np.nan)
    return df


@st.cache_data(show_spinner='載入績效摘要資料…')
def load_perf_summary(site: str) -> pd.DataFrame | None:
    """載入 perf_summary CSV，日期欄轉為 datetime。"""
    path = _PERF_FILES.get(site)
    if path is None or not path.exists():
        return None
    df = pd.read_csv(path, encoding='utf-8-sig', dtype=str)
    df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
    return df


def compute_path_tt(
    df_tt: pd.DataFrame,
    path_ids: list[str],
    dates: list[pd.Timestamp],
    time_start: str,
    time_end: str,
    agg_minutes: int,
) -> pd.DataFrame:
    """
    計算指定路徑在多個日期的旅行時間時序。
    回傳 DataFrame：index = 時間字串(HH:MM)，columns = 日期標籤(MM/DD)。
    任一路段缺資料時，該時間點路徑旅行時間為 NaN。
    """
    results: dict[str, pd.Series] = {}
    rule = f'{agg_minutes}min'

    for dt in dates:
        dt_ts = pd.Timestamp(dt)
        day_mask = df_tt['time'].dt.date == dt_ts.date()
        seg_mask = df_tt['roadid'].isin(path_ids)
        day_df   = df_tt[day_mask & seg_mask].copy()
        if day_df.empty:
            continue

        t_start = pd.Timestamp(f'{dt_ts.date()} {time_start}')
        t_end   = pd.Timestamp(f'{dt_ts.date()} {time_end}')
        day_df  = day_df[(day_df['time'] >= t_start) & (day_df['time'] <= t_end)]
        if day_df.empty:
            continue

        pivot = day_df.pivot_table(
            index='time', columns='roadid', values='traveltime', aggfunc='mean'
        )
        for seg in path_ids:
            if seg not in pivot.columns:
                pivot[seg] = np.nan
        pivot = pivot[path_ids]

        resampled = pivot.resample(rule).mean()
        # 全部路段都有資料才加總，否則 NaN
        path_tt = resampled.sum(axis=1, min_count=len(path_ids))
        path_tt = path_tt.where(path_tt > 0)

        path_tt.index = [t.strftime('%H:%M') for t in path_tt.index.time]
        label = dt_ts.strftime('%m/%d')
        results[label] = path_tt

    if not results:
        return pd.DataFrame()

    result_df = pd.DataFrame(results)
    result_df.index.name = '時間'
    return result_df


def get_perf_trend(
    df_perf: pd.DataFrame,
    metric: str,
    columns: list[str],
    periods: list[str],
) -> pd.DataFrame:
    """
    從 perf_summary 取出指定指標、欄位、時段的多日趨勢。
    回傳 DataFrame：index = 日期，columns = MultiIndex(時段, 欄位)。
    """
    rows = []
    df_metric = df_perf[df_perf['指標'] == metric].copy()
    for period in periods:
        df_p = df_metric[df_metric['時段'] == period]
        for col in columns:
            if col not in df_p.columns:
                continue
            series = pd.to_numeric(df_p.set_index('日期')[col], errors='coerce').dropna()
            series.name = (period, col)
            rows.append(series)
    if not rows:
        return pd.DataFrame()
    result = pd.concat(rows, axis=1)
    result.index = pd.to_datetime(result.index, errors='coerce')
    result = result[result.index.notna()].sort_index()
    return result
