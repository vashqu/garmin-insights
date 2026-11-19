"""Reusable UI pieces: status cards, info popovers, baseline trend charts, empty states."""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html

from garmin_insights.analytics.metrics import M
from garmin_insights.ui import theme as T

_ids = itertools.count()


def info(text: str, evidence: str | None = None) -> html.Span:
    """ⓘ with a hover tooltip explaining the metric and its evidence level."""
    import dash_bootstrap_components as dbc

    i = f"info-{next(_ids)}"
    body = [html.Div(text)]
    if evidence:
        body.append(html.Div(f"Evidence: {evidence}", className="info-ev"))
    return html.Span([html.Span("ⓘ", id=i, className="info-dot"),
                      dbc.Tooltip(body, target=i, placement="bottom", className="info-tip")])


def status_badge(st: str | None, small: bool = False, label: str | None = None) -> html.Span:
    role, icon, default = T.STATUS_MAP.get(st or "nodata", T.STATUS_MAP["nodata"])
    label = label or default
    return html.Span([html.Span(icon, className="st-icon"), html.Span(label, className="st-label")],
                     className=f"status-badge st-{role or 'none'}" + (" small" if small else ""))


def sparkline(values: pd.Series, theme: str | None, status: str | None = None, height: int = 44) -> dcc.Graph:
    f = go.Figure()
    v = values.dropna()
    col = T.series(theme, 0)
    if len(v):
        f.add_trace(go.Scatter(x=v.index, y=v.values, mode="lines", line=dict(width=2, color=col, shape="spline",
                                                                              smoothing=0.6),
                               hovertemplate="%{x|%d %b}: %{y:.1f}<extra></extra>"))
        f.add_trace(go.Scatter(x=[v.index[-1]], y=[v.values[-1]], mode="markers",
                               marker=dict(size=8, color=T.status_color(status) or col,
                                           line=dict(width=2, color=T.chrome(theme)["surface"])),
                               hoverinfo="skip"))
    f.update_layout(height=height, margin=dict(l=0, r=4, t=4, b=0), showlegend=False,
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(visible=False), yaxis=dict(visible=False), hovermode="x")
    return dcc.Graph(figure=f, config={"displayModeBar": False, "staticPlot": False}, className="spark")


def metric_card(key: str, value, status: str | None, theme: str | None, spark: pd.Series | None = None,
                sub: str | None = None, label: str | None = None, href: str | None = None) -> html.Div:
    m = M.get(key)
    lab = label or (m.label if m else key)
    val = "–"
    if value is not None and not (isinstance(value, float) and np.isnan(value)):
        val = (m.fmt if m else "{}").format(value)
    head = [html.Span(lab, className="mc-label")]
    if m and m.why:
        head.append(info(m.why, m.evidence))
    body = [html.Div(head, className="mc-head"),
            html.Div([html.Span(val, className="mc-value"), html.Span(m.unit if m and val != "–" else "",
                                                                       className="mc-unit")]),
            status_badge(status, small=True)]
    if sub:
        body.append(html.Div(sub, className="mc-sub"))
    if spark is not None:
        body.append(sparkline(spark, theme, status))
    card = html.Div(body, className="metric-card")
    return dcc.Link(card, href=href, className="mc-link") if href else card


def section(title: str, children, right=None, info_text: str | None = None, evidence: str | None = None,
            className: str = "") -> html.Div:
    head = [html.H3([title] + ([info(info_text, evidence)] if info_text else []), className="sec-title")]
    if right is not None:
        head.append(html.Div(right, className="sec-right"))
    return html.Div([html.Div(head, className="sec-head"), html.Div(children, className="sec-body")],
                    className="section " + className)


def graph(fig: go.Figure, id: str | None = None, className: str = "") -> dcc.Graph:
    kw = {"id": id} if id else {}
    return dcc.Graph(figure=fig, config=T.GRAPH_CONFIG, className="chart " + className, **kw)


def empty_state(title: str, text: str = "", action=None) -> html.Div:
    return html.Div([html.Div(title, className="empty-title"), html.Div(text, className="empty-text"),
                     action or ""], className="empty-state")


def baseline_chart(df: pd.DataFrame, value: str, theme: str | None, label: str, unit: str = "",
                   med: str | None = None, scale: str | None = None, status: str | None = None,
                   band_k: float = 0.75, height: int = 240, lo: str | None = None, hi: str | None = None,
                   kind: str = "line", fmt: str = ".1f", band_label: str = "Your normal range") -> go.Figure:
    """Trend vs personal baseline band. Off-norm points are marked with the status colour + icon in hover."""
    f = T.fig(theme, height)
    c = T.chrome(theme)
    d = df[[x for x in (value, med, scale, status, lo, hi) if x and x in df]].copy()
    if d[value].dropna().empty:
        return T.empty_fig(theme, "No data in this period", height)
    if lo and hi and lo in d and hi in d:
        blo, bhi = d[lo], d[hi]
    elif med and scale and med in d:
        blo, bhi = d[med] - band_k * d[scale], d[med] + band_k * d[scale]
    else:
        blo = bhi = None
    if blo is not None:
        f.add_trace(go.Scatter(x=d.index, y=bhi, mode="lines", line=dict(width=0), hoverinfo="skip",
                               showlegend=False, connectgaps=False))
        f.add_trace(go.Scatter(x=d.index, y=blo, mode="lines", line=dict(width=0), fill="tonexty",
                               fillcolor=c["band"], name=band_label, hoverinfo="skip", connectgaps=False))
    col = T.series(theme, 0)
    if kind == "bar":
        f.add_trace(go.Bar(x=d.index, y=d[value], name=label, marker=dict(color=col, cornerradius=4),
                           hovertemplate=f"%{{y:{fmt}}} {unit}<extra>{label}</extra>"))
    else:
        f.add_trace(go.Scatter(x=d.index, y=d[value], mode="lines+markers", name=label, connectgaps=True,
                               line=dict(width=2, color=col), marker=dict(size=5, color=col),
                               hovertemplate=f"%{{y:{fmt}}} {unit}<extra>{label}</extra>"))
    if status and status in d:
        off = d[d[status].isin(["amber", "red"])]
        if len(off):
            f.add_trace(go.Scatter(
                x=off.index, y=off[value], mode="markers", name="Off your norm",
                marker=dict(size=10, color=[T.status_color(s) for s in off[status]],
                            symbol=["triangle-up" if s == "amber" else "square" for s in off[status]],
                            line=dict(width=2, color=c["surface"])),
                customdata=[T.STATUS_MAP[s][2] for s in off[status]],
                hovertemplate="%{customdata}<extra></extra>"))
    f.update_yaxes(title=dict(text=unit, font=dict(size=11, color=c["muted"])))
    return f
