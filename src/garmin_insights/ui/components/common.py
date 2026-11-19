"""Shared page helpers."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
from dash import dcc, html

RANGES = [{"label": "30 d", "value": 30}, {"label": "90 d", "value": 90}, {"label": "1 y", "value": 365},
          {"label": "All", "value": 3650}]


def range_selector(id: str, value: int = 90) -> dcc.RadioItems:
    return dcc.RadioItems(id=id, options=RANGES, value=value, inline=True, className="range-sel",
                          inputClassName="me-1", labelClassName="me-3 small")


def window(D: pd.DataFrame, days: int, end=None) -> pd.DataFrame:
    if D.empty:
        return D
    end = pd.Timestamp(end) if end is not None else D.index.max()
    start = max(D.index.min(), end - timedelta(days=int(days) - 1))
    return D.loc[start:end]


def page_header(title: str, subtitle: str = "", right=None) -> html.Div:
    return html.Div([html.Div([html.H2(title, className="mb-0", style={"fontSize": "20px"}),
                               html.Div(subtitle, className="muted small")]),
                     html.Div(right) if right is not None else ""],
                    className="d-flex justify-content-between align-items-end mb-3")


def kv(pairs: list[tuple[str, object]]) -> html.Div:
    kids = []
    for k, v in pairs:
        kids += [html.Span(k, className="k"), html.Span("–" if v is None else v)]
    return html.Div(kids, className="kv")


def fmt_min(m) -> str:
    if m is None or m != m:
        return "–"
    m = int(round(m))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m} min"


def nz(v, default=None):
    try:
        if v is None or v != v:
            return default
    except TypeError:
        return v
    return v
