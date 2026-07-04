"""Plotly 圖表工廠模組"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

COLOR_BEFORE = '#4472C4'
COLOR_AFTER = '#ED7D31'
COLOR_IMPROVE = '#70AD47'
COLOR_WORSEN = '#FF0000'


def make_metric_bar_chart(
    comparison_df: pd.DataFrame,
    metric_name: str,
    period: str,
    top_n: int = 20,
) -> go.Figure:
    """事前 vs 事後群組長條圖（取前 top_n 欄）。"""
    df = comparison_df.dropna(subset=['事前平均', '事後平均']).head(top_n)
    if df.empty:
        return _empty_fig(f"無資料：{period} {metric_name}")

    is_volume = (metric_name == '通過量')
    fmt = '.0f' if is_volume else '.1f'

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name='事前（定時）',
        x=df['欄位'],
        y=df['事前平均'],
        marker_color=COLOR_BEFORE,
        texttemplate=f'%{{y:{fmt}}}',
        textposition='outside',
    ))
    fig.add_trace(go.Bar(
        name='事後（AI）',
        x=df['欄位'],
        y=df['事後平均'],
        marker_color=COLOR_AFTER,
        texttemplate=f'%{{y:{fmt}}}',
        textposition='outside',
    ))
    fig.update_layout(
        title=f"{period}  {metric_name}",
        barmode='group',
        height=420,
        margin=dict(t=50, b=80, l=40, r=20),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        xaxis_tickangle=-30,
    )
    return fig


def make_improvement_overview_chart(
    all_results: dict[str, dict[str, pd.DataFrame]],
) -> go.Figure:
    """各時段 × 各指標的系統層級改善率概覽橫向群組長條圖。"""
    metrics = ['總停等延滯', '通過量', '平均停等延滯']
    periods = list(all_results.keys())
    if not periods:
        return _empty_fig("無資料")

    colors = [COLOR_BEFORE, COLOR_AFTER, COLOR_IMPROVE]
    fig = go.Figure()

    for metric, color in zip(metrics, colors):
        pct_vals = []
        for period in periods:
            df = all_results[period].get(metric, pd.DataFrame())
            if df.empty:
                pct_vals.append(float('nan'))
                continue
            row = df[df['欄位'] == '系統']
            pct_vals.append(row['改善%'].values[0] * 100 if not row.empty else float('nan'))

        text_vals = [f"{v:+.1f}%" if not pd.isna(v) else '—' for v in pct_vals]
        bar_colors = [COLOR_IMPROVE if (not pd.isna(v) and v > 0) else COLOR_WORSEN
                      for v in pct_vals]
        fig.add_trace(go.Bar(
            name=metric,
            x=periods,
            y=pct_vals,
            marker_color=bar_colors,
            text=text_vals,
            textposition='outside',
            opacity=0.85,
        ))

    fig.update_layout(
        title='各時段系統層級改善率概覽（正值=改善）',
        barmode='group',
        height=380,
        yaxis_tickformat='.1f',
        yaxis_title='改善率 (%)',
        margin=dict(t=50, b=60, l=60, r=20),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        xaxis_tickangle=-20,
    )
    fig.add_hline(y=0, line_dash='dash', line_color='gray', line_width=1)
    return fig


def make_travel_time_chart(tt_df: pd.DataFrame, period: str) -> go.Figure:
    """旅行時間廊道事前 vs 事後比較圖。"""
    df = tt_df.dropna(subset=['事前平均', '事後平均'])
    if df.empty:
        return _empty_fig(f"無旅行時間資料：{period}")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name='事前（定時）',
        x=df['欄位'],
        y=df['事前平均'],
        marker_color=COLOR_BEFORE,
        texttemplate='%{y:.0f}',
        textposition='outside',
    ))
    fig.add_trace(go.Bar(
        name='事後（AI）',
        x=df['欄位'],
        y=df['事後平均'],
        marker_color=COLOR_AFTER,
        texttemplate='%{y:.0f}',
        textposition='outside',
    ))
    fig.update_layout(
        title=f"{period}  旅行時間廊道（秒）",
        barmode='group',
        height=420,
        margin=dict(t=50, b=100, l=40, r=20),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        xaxis_tickangle=-35,
        yaxis_title='秒 (s)',
    )
    return fig


def _empty_fig(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, xref='paper', yref='paper', x=0.5, y=0.5,
                       showarrow=False, font=dict(size=14))
    fig.update_layout(height=250)
    return fig
