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
) -> go.Figure:
    """事前 vs 事後群組長條圖。"""
    df = comparison_df.dropna(subset=['事前平均', '事後平均'])
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
        title=f"{period}  旅行時間（秒）",
        barmode='group',
        height=420,
        margin=dict(t=50, b=100, l=40, r=20),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        xaxis_tickangle=-35,
        yaxis_title='秒 (s)',
    )
    return fig


_FLAG_MARKERS = {
    '較差': dict(color='#C00000', symbol='triangle-up', name='明顯較差'),
    '較好': dict(color='#2E7D32', symbol='triangle-down', name='明顯較好'),
}


def make_daily_trend_chart(
    daily: pd.DataFrame,
    group_means: dict[str, float],
    title: str,
    closed_dates: set | None = None,
) -> go.Figure:
    """每日趨勢圖：事前／事後各一條線、同組平均虛線、異常日標記、AI 關閉日灰底。
    daily 為 comparison_logic.analyze_daily 回傳的每日表（資料異常日不畫點）。"""
    if daily['數值'].notna().sum() == 0:
        return _empty_fig(f"無資料：{title}")

    plot_df = daily.copy()
    plot_df.loc[plot_df['標記'] == '資料異常', '數值'] = float('nan')
    colors = {'事前': COLOR_BEFORE, '事後': COLOR_AFTER}
    names  = {'事前': '事前（定時）', '事後': '事後（AI）'}

    fig = go.Figure()
    for d in sorted(closed_dates or []):
        fig.add_vrect(x0=d - pd.Timedelta(hours=12), x1=d + pd.Timedelta(hours=12),
                      fillcolor='#BBBBBB', opacity=0.25, line_width=0, layer='below')

    for label in ('事前', '事後'):
        g = plot_df[plot_df['組別'] == label]
        if g.empty:
            continue
        fig.add_trace(go.Scatter(
            x=g['日期'], y=g['數值'], mode='lines+markers', name=names[label],
            line=dict(color=colors[label], width=2), marker=dict(size=8),
            customdata=g['差%'] * 100,
            hovertemplate='%{x|%Y/%m/%d (%a)}<br>%{y:,.1f}<br>與同組平均差 %{customdata:+.1f}%<extra>' + label + '</extra>',
        ))
        mean = group_means.get(label, float('nan'))
        if not pd.isna(mean):
            fig.add_shape(type='line', x0=g['日期'].min(), x1=g['日期'].max(), y0=mean, y1=mean,
                          line=dict(color=colors[label], width=1.5, dash='dash'))

    for flag, spec in _FLAG_MARKERS.items():
        f = plot_df[plot_df['標記'] == flag]
        if f.empty:
            continue
        fig.add_trace(go.Scatter(
            x=f['日期'], y=f['數值'], mode='markers', name=spec['name'],
            marker=dict(color=spec['color'], symbol=spec['symbol'], size=14,
                        line=dict(color='white', width=2)),
            hovertemplate='%{x|%Y/%m/%d}<br>%{y:,.1f}<extra>' + spec['name'] + '</extra>',
        ))

    fig.update_layout(
        title=title,
        height=360,
        margin=dict(t=50, b=40, l=50, r=20),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        hovermode='closest',
        xaxis=dict(tickformat='%m/%d'),
    )
    return fig


def make_daily_box_chart(daily: pd.DataFrame, title: str) -> go.Figure:
    """事前／事後每日數值分布箱形圖（資料異常日不納入）。"""
    valid = daily[daily['標記'] != '資料異常']
    if valid['數值'].notna().sum() == 0:
        return _empty_fig(f"無資料：{title}")
    fig = go.Figure()
    for label, color in (('事前', COLOR_BEFORE), ('事後', COLOR_AFTER)):
        g = valid[valid['組別'] == label]
        fig.add_trace(go.Box(
            y=g['數值'], name=label, marker_color=color, boxpoints='all', jitter=0.3,
            pointpos=0, customdata=g['日期'].dt.strftime('%Y/%m/%d'),
            hovertemplate='%{customdata}<br>%{y:,.1f}<extra>' + label + '</extra>',
        ))
    fig.update_layout(title=title, height=360, showlegend=False,
                      margin=dict(t=50, b=40, l=50, r=20))
    return fig


def _empty_fig(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, xref='paper', yref='paper', x=0.5, y=0.5,
                       showarrow=False, font=dict(size=14))
    fig.update_layout(height=250)
    return fig
