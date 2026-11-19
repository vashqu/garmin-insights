"""Colour tokens (validated reference palette from the dataviz method) and Plotly templates per mode."""

from __future__ import annotations

import plotly.graph_objects as go

SERIES = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"],
}
SEQ_BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf",
            "#1c5cab", "#184f95", "#104281", "#0d366b"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
STATUS_MAP = {"green": ("good", "●", "Normal"), "amber": ("warning", "▲", "Off your norm"),
              "red": ("critical", "■", "Well off your norm"), "calibrating": (None, "○", "Calibrating"),
              "nodata": (None, "–", "No data")}
CHROME = {
    "light": {"surface": "#fcfcfb", "page": "#f9f9f7", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
              "grid": "#e1e0d9", "axis": "#c3c2b7", "band": "rgba(42,120,214,0.12)", "neutral": "#f0efec"},
    "dark": {"surface": "#1a1a19", "page": "#0d0d0d", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
             "grid": "#2c2c2a", "axis": "#383835", "band": "rgba(57,135,229,0.18)", "neutral": "#383835"},
}
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def mode(theme: str | None) -> str:
    return "dark" if theme == "dark" else "light"


def series(theme: str | None, i: int) -> str:
    return SERIES[mode(theme)][i % 8]


def chrome(theme: str | None) -> dict:
    return CHROME[mode(theme)]


def status_color(st: str | None) -> str | None:
    role = STATUS_MAP.get(st or "", (None,))[0]
    return STATUS.get(role) if role else None


def base_layout(theme: str | None, height: int = 260, **kw) -> dict:
    c = chrome(theme)
    lay = dict(
        height=height, margin=dict(l=44, r=16, t=28, b=32),
        paper_bgcolor=c["surface"], plot_bgcolor=c["surface"],
        font=dict(family=FONT, size=12, color=c["ink2"]),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=c["surface"], bordercolor=c["axis"], font=dict(color=c["ink"], family=FONT)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(color=c["ink2"]),
                    bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(showgrid=False, linecolor=c["axis"], tickcolor=c["axis"], tickfont=dict(color=c["muted"]),
                   showspikes=True, spikemode="across", spikethickness=1, spikecolor=c["axis"], spikedash="solid"),
        yaxis=dict(gridcolor=c["grid"], gridwidth=1, zeroline=False, linecolor=c["axis"],
                   tickfont=dict(color=c["muted"])),
        colorway=SERIES[mode(theme)],
        bargap=0.25,
    )
    lay.update(kw)
    return lay


def fig(theme: str | None, height: int = 260, **kw) -> go.Figure:
    f = go.Figure()
    f.update_layout(**base_layout(theme, height, **kw))
    return f


def empty_fig(theme: str | None, msg: str = "No data yet", height: int = 200) -> go.Figure:
    f = fig(theme, height)
    f.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False), hovermode=False)
    f.add_annotation(text=msg, showarrow=False, font=dict(color=chrome(theme)["muted"], size=13),
                     xref="paper", yref="paper", x=0.5, y=0.5)
    return f


GRAPH_CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
                "displayModeBar": "hover", "responsive": True}
