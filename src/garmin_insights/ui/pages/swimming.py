"""Swimming: pace per 100 m, SWOLF (same pool/stroke only), weekly distance, CSS."""

from __future__ import annotations

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

from garmin_insights import config
from garmin_insights.analytics import sports as sp
from garmin_insights.analytics.frame import get_bundle
from garmin_insights.storage import db
from garmin_insights.ui import theme as T
from garmin_insights.ui.components.common import kv, page_header
from garmin_insights.ui.components.widgets import empty_state, graph, section

dash.register_page(__name__, path="/swimming", title="Swimming")


def layout(**_):
    return html.Div([page_header("Swimming", "Pace, efficiency (SWOLF) and Critical Swim Speed"),
                     dcc.Loading(html.Div(id="swim-body"), type="circle", delay_show=400)])


def _p100(sec):
    if sec is None or sec != sec:
        return "–"
    return f"{int(sec // 60)}:{int(sec % 60):02d} /100 m"


@callback(Output("swim-body", "children"), Input("data-version", "data"), Input("theme", "data"))
def render(_v, theme):
    b = get_bundle()
    s = config.Settings.load()
    css = sp.css_from_test(s.css_test_400_s, s.css_test_200_s)
    css_block = section("Critical Swim Speed (CSS)", kv([
        ("CSS", _p100(100 / css) if css else "not set"),
        ("How", "Swim an all-out 400 m, rest fully, then an all-out 200 m; enter both times in Settings."),
    ]), info_text="CSS = 200 / (T400 − T200). Reliable across strokes (typical error ≤0.04 m/s; Scott, Burden & "
                  "Dekerle). Use it as a pacing anchor: threshold sets at CSS pace, aerobic sets a few seconds "
                  "slower per 100 m.", evidence="strong")
    swims = b.acts[b.acts["sport_group"] == "swim"] if not b.acts.empty else pd.DataFrame()
    if swims.empty:
        return html.Div([empty_state("No swims yet", "Record a Pool Swim or Open Water Swim on your watch and it "
                                                     "will show up here after the next sync."), css_block])
    swims = swims.assign(p100=swims["moving_s"].fillna(swims["duration_s"]) / swims["distance_m"] * 100)
    f = T.fig(theme, 240, showlegend=False)
    f.add_trace(go.Scatter(x=swims["date"], y=swims["p100"], mode="lines+markers", line=dict(width=2, color=T.series(theme, 0)),
                           marker=dict(size=8), customdata=swims["distance_m"],
                           hovertemplate="%{y:.0f} s/100 m · %{customdata:.0f} m<extra></extra>"))
    if css:
        f.add_hline(y=100 / css, line=dict(dash="dot", color=T.chrome(theme)["muted"]), annotation_text="CSS pace")
    f.update_yaxes(title="seconds per 100 m", autorange="reversed")
    ln = db.query_df("SELECT l.*, a.date FROM swim_length l JOIN activity a USING(activity_id)")
    parts = [section("Pace per 100 m", graph(f))]
    if not ln.empty:
        g = ln.groupby(["date", "distance_m", "stroke"])["swolf"].mean().reset_index()
        h = T.fig(theme, 240)
        for i, ((pool, stroke), grp) in enumerate(g.groupby(["distance_m", "stroke"])):
            h.add_trace(go.Scatter(x=pd.to_datetime(grp["date"]), y=grp["swolf"], mode="lines+markers",
                                   name=f"{stroke.lower()} · {pool:g} m pool", line=dict(width=2, color=T.series(theme, i))))
        h.update_yaxes(title="avg SWOLF")
        parts.append(section("SWOLF by pool length & stroke", graph(h),
                             info_text="Lower is more efficient — but only comparable within the same pool length and "
                                       "stroke.", evidence="moderate"))
    wk = swims.assign(week=swims["date"].dt.to_period("W-SUN").dt.start_time).groupby("week")["distance_m"].sum()
    k = T.fig(theme, 200, showlegend=False)
    k.add_trace(go.Bar(x=wk.index, y=wk.values, marker=dict(color=T.series(theme, 0), cornerradius=4),
                       hovertemplate="%{y:.0f} m<extra></extra>"))
    parts.append(section("Weekly distance", graph(k)))
    return html.Div([html.Div(parts[:2], className="grid g2"), *parts[2:], css_block])
