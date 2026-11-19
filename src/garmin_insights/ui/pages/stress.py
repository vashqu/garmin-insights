"""Stress & energy: stress breakdown, Body Battery range, intraday energy, breathing pacer."""

from __future__ import annotations

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

from garmin_insights.analytics.frame import get_bundle
from garmin_insights.analytics.metrics import M
from garmin_insights.storage import db
from garmin_insights.ui import theme as T
from garmin_insights.ui.components.common import page_header, range_selector, window
from garmin_insights.ui.components.widgets import baseline_chart, empty_state, graph, metric_card, section

dash.register_page(__name__, path="/stress", title="Stress & energy")

PACER = html.Div([
    html.Div(className="pacer-circle"),
    html.Div([html.Span("Breathe in", className="pacer-in"), html.Span("Breathe out", className="pacer-out")],
             className="pacer-label"),
], className="pacer")


def layout(**_):
    return html.Div([page_header("Stress & energy", "Garmin stress, Body Battery and a slow-breathing pacer",
                                 range_selector("stress-range", 30)),
                     dcc.Loading(html.Div(id="stress-body"), type="circle", delay_show=400)])


@callback(Output("stress-body", "children"), Input("data-version", "data"), Input("theme", "data"),
          Input("stress-range", "value"))
def render(_v, theme, days):
    b = get_bundle()
    if b.empty:
        return empty_state("No data yet")
    D = window(b.daily, days)
    last = b.latest_date()
    r = b.daily.loc[last]
    cards = html.Div([
        metric_card("stress_avg", r["stress_avg"], r["stress_avg_status"], theme, D["stress_avg"]),
        metric_card("bb_max", r["bb_max"], r["bb_max_status"], theme, D["bb_max"],
                    f"low {r['bb_min']:.0f}" if r["bb_min"] == r["bb_min"] else None),
    ], className="grid g4 mb-3")
    # stress breakdown (ordinal → single-hue ramp)
    ramp = [T.SEQ_BLUE[3], T.SEQ_BLUE[6], T.SEQ_BLUE[9], T.SEQ_BLUE[12]]
    sb = T.fig(theme, 280, barmode="stack")
    for i, (c, lab) in enumerate([("stress_rest_s", "Rest"), ("stress_low_s", "Low"), ("stress_med_s", "Medium"),
                                  ("stress_high_s", "High")]):
        sb.add_trace(go.Bar(x=D.index, y=D[c] / 3600, name=lab,
                            marker=dict(color=ramp[i], line=dict(width=1, color=T.chrome(theme)["surface"])),
                            hovertemplate=f"{lab}: %{{y:.1f}} h<extra></extra>"))
    sb.update_yaxes(title="hours")
    # body battery range bars
    bb = T.fig(theme, 260, showlegend=False)
    bb.add_trace(go.Bar(x=D.index, y=D["bb_max"] - D["bb_min"], base=D["bb_min"],
                        marker=dict(color=T.series(theme, 2), cornerradius=4),
                        customdata=D[["bb_min", "bb_max", "bb_charged", "bb_drained"]].values,
                        hovertemplate="Low %{customdata[0]:.0f} → high %{customdata[1]:.0f}<br>Charged "
                                      "+%{customdata[2]:.0f} · drained −%{customdata[3]:.0f}<extra></extra>"))
    bb.update_yaxes(range=[0, 100], title="Body Battery")
    avg = baseline_chart(D, "stress_avg", theme, "Avg stress", "", "stress_avg_med", "stress_avg_scale",
                         "stress_avg_status", fmt=".0f")
    days_ = [d for d in b.daily.index[b.daily["bb_max"].notna()]][::-1]
    return html.Div([
        cards,
        html.Div([section("Average stress", graph(avg), info_text=M["stress_avg"].why),
                  section("Body Battery range", graph(bb), info_text=M["bb_max"].why, evidence="heuristic")],
                 className="grid g2"),
        section("Time in stress levels", graph(sb)),
        section("Energy through the day", [
            dcc.Dropdown(id="stress-day", options=[{"label": d.strftime("%a %d %b %Y"), "value": d.strftime("%Y-%m-%d")}
                                                   for d in days_[:120]],
                         value=days_[0].strftime("%Y-%m-%d") if days_ else None, clearable=False,
                         style={"maxWidth": "260px"}),
            html.Div(id="stress-day-body")]),
        section("Slow-breathing pacer (6 breaths/min)", html.Div([
            PACER,
            html.Div([html.P("Follow the circle: 4 s in, 6 s out, for 5–10 minutes. Slow-paced breathing near 6 "
                             "breaths/min modestly reduces stress and raises HRV in meta-analyses (Laborde 2021; "
                             "Shao 2023). Log it on your watch as a Breathwork activity to see its effect here."),
                      ], className="muted")], className="d-flex gap-4 align-items-center"),
                info_text="Moderate evidence for short-term stress and HRV effects; personalised resonance "
                          "frequency adds little over 6/min (Lehrer 2020).", evidence="moderate"),
    ])


@callback(Output("stress-day-body", "children"), Input("stress-day", "value"), Input("theme", "data"))
def day_view(day, theme):
    if not day:
        return ""
    bbv = db.query_df("SELECT ts, value FROM intraday WHERE metric='bb' AND date=? ORDER BY ts", (day,))
    st = db.query_df("SELECT ts, value FROM intraday WHERE metric='stress' AND date=? ORDER BY ts", (day,))
    hr = db.query_df("SELECT ts, value FROM intraday WHERE metric='hr' AND date=? ORDER BY ts", (day,))
    ev = db.query_df("SELECT * FROM bb_event WHERE date=?", (day,))
    from plotly.subplots import make_subplots

    rows = 2 + (1 if not st.empty else 0)
    f = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.06)
    f.update_layout(**T.base_layout(theme, 140 * rows + 40, showlegend=False))
    if not bbv.empty:
        f.add_trace(go.Scatter(x=pd.to_datetime(bbv["ts"], unit="ms"), y=bbv["value"], mode="lines", line=dict(width=2, color=T.series(theme, 2)),
                               hovertemplate="%{y}<extra>Body Battery</extra>"), 1, 1)
    f.add_trace(go.Scatter(x=pd.to_datetime(hr["ts"], unit="ms"), y=hr["value"], mode="lines",
                           line=dict(width=1.5, color=T.series(theme, 7)), hovertemplate="%{y}<extra>HR</extra>"), 2, 1)
    if not st.empty:
        f.add_trace(go.Scatter(x=pd.to_datetime(st["ts"], unit="ms"), y=st["value"], mode="lines",
                               line=dict(width=1.5, color=T.series(theme, 1)),
                               hovertemplate="%{y}<extra>Stress</extra>"), 3, 1)
        f.update_yaxes(title_text="Stress", row=3, col=1)
    f.update_yaxes(title_text="Body Battery", row=1, col=1, range=[0, 100])
    f.update_yaxes(title_text="HR", row=2, col=1)
    for i in range(1, rows + 1):
        f.update_yaxes(gridcolor=T.chrome(theme)["grid"], row=i, col=1)
    b = get_bundle()
    acts = b.acts[b.acts["date"] == pd.Timestamp(day)] if not b.acts.empty else pd.DataFrame()
    for _, a in acts.iterrows():
        s = pd.Timestamp(a["start_local"])
        f.add_vrect(x0=s, x1=s + pd.Timedelta(seconds=a["duration_s"] or 0), fillcolor=T.chrome(theme)["band"],
                    line_width=0)
    note = "" if not ev.empty else html.Div("Stress timeline and Body Battery event explanations need the optional "
                                            "Garmin Connect login (Settings).", className="muted small")
    return html.Div([graph(f), note])
