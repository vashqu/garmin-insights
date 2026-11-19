"""Sleep: stages, duration vs need, regularity (SRI), timing, night detail."""

from __future__ import annotations

from datetime import timedelta

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

dash.register_page(__name__, path="/sleep", title="Sleep")


def layout(**_):
    return html.Div([page_header("Sleep", "Duration vs your need, regularity, stages and timing",
                                 range_selector("sleep-range", 30)),
                     dcc.Loading(html.Div(id="sleep-body"), type="circle", delay_show=400)])


def _clock_ticks():
    vals = list(range(0, 24 * 60 + 1, 120))
    labels = [f"{((18 * 60 + v) // 60) % 24:02d}:00" for v in vals]
    return vals, labels


@callback(Output("sleep-body", "children"), Input("data-version", "data"), Input("theme", "data"),
          Input("sleep-range", "value"))
def render(_v, theme, days):
    b = get_bundle()
    if b.empty or b.daily["sleep_h"].dropna().empty:
        return empty_state("No sleep data yet", "Wear your watch overnight and sync.")
    D = window(b.daily, days)
    last = b.daily["sleep_h"].last_valid_index()
    r = b.daily.loc[last]
    c = T.chrome(theme)

    cards = html.Div([
        metric_card("sleep_h", r["sleep_h"], r["sleep_h_status"], theme, D["sleep_h"],
                    f"need ≈ {r['sleep_need_h']:.1f} h · {last:%a %d %b}"),
        metric_card("sleep_score", r["sleep_score"], r["sleep_score_status"], theme, D["sleep_score"]),
        metric_card("sri7", r["sri7"], r["sri7_status"], theme, D["sri7"],
                    f"30-night SRI {r['sri30']:.0f}" if r["sri30"] == r["sri30"] else None),
        metric_card("sleep_debt7", r["sleep_debt7"], r["sleep_debt7_status"], theme, D["sleep_debt7"]),
    ], className="grid g4 mb-3")

    # stages
    st = T.fig(theme, 280, barmode="stack")
    for i, (col, lab) in enumerate([("sl_deep_s", "Deep"), ("sl_light_s", "Light"), ("sl_rem_s", "REM"),
                                    ("sl_awake_s", "Awake")]):
        st.add_trace(go.Bar(x=D.index, y=D[col] / 3600, name=lab, marker=dict(color=T.series(theme, i),
                                                                              line=dict(width=1, color=c["surface"])),
                            hovertemplate=f"{lab}: %{{y:.1f}} h<extra></extra>"))
    st.add_trace(go.Scatter(x=D.index, y=D["sleep_need_h"], name="Your need", mode="lines",
                            line=dict(width=2, color=c["ink2"], dash="dot"),
                            hovertemplate="Need: %{y:.1f} h<extra></extra>"))
    st.update_yaxes(title="hours")
    st.update_layout(bargap=0.2)

    # timing
    tm = T.fig(theme, 260)
    vals, labels = _clock_ticks()
    tm.add_trace(go.Scatter(x=D.index, y=D["bedtime_min"], mode="markers+lines", name="Bedtime",
                            line=dict(width=1, color=T.series(theme, 0)), marker=dict(size=8),
                            customdata=pd.to_datetime(D["sl_start_local"]).dt.strftime("%H:%M"),
                            hovertemplate="Bed %{customdata}<extra></extra>"))
    tm.add_trace(go.Scatter(x=D.index, y=D["wake_min"] + 6 * 60, mode="markers+lines", name="Wake",
                            line=dict(width=1, color=T.series(theme, 1)), marker=dict(size=8),
                            customdata=pd.to_datetime(D["sl_end_local"]).dt.strftime("%H:%M"),
                            hovertemplate="Wake %{customdata}<extra></extra>"))
    tm.update_yaxes(tickvals=vals, ticktext=labels, autorange="reversed")

    score = baseline_chart(D, "sleep_score", theme, "Sleep score", "", "sleep_score_med", "sleep_score_scale",
                           "sleep_score_status", fmt=".0f")
    sri = baseline_chart(D, "sri7", theme, "SRI (7 nights)", "SRI", "sri7_med", "sri7_scale", "sri7_status",
                         fmt=".0f")
    resp = baseline_chart(D, "resp_sleep", theme, "Sleep respiration", "brpm", "resp_sleep_med",
                          "resp_sleep_scale", "resp_sleep_status")

    nights = [d for d in b.daily.index[b.daily["sleep_h"].notna()]][::-1]
    return html.Div([
        cards,
        section("Sleep stages vs your need", graph(st, "sleep-stages"),
                info_text=M["sleep_h"].why, evidence="strong"),
        html.Div([
            section("Bed & wake times", graph(tm), info_text=M["bedtime_sd7"].why, evidence="moderate"),
            section("Sleep regularity index", graph(sri), info_text=M["sri7"].why, evidence="strong"),
        ], className="grid g2"),
        html.Div([section("Sleep score", graph(score)),
                  section("Breathing rate during sleep", graph(resp), info_text=M["resp_sleep"].why)],
                 className="grid g2"),
        section("Night detail", [
            dcc.Dropdown(id="sleep-night", options=[{"label": d.strftime("%a %d %b %Y"), "value": d.strftime("%Y-%m-%d")}
                                                    for d in nights],
                         value=nights[0].strftime("%Y-%m-%d") if nights else None, clearable=False,
                         style={"maxWidth": "260px"}),
            html.Div(id="sleep-night-body")]),
    ])


@callback(Output("sleep-night", "value"), Input("sleep-stages", "clickData"), prevent_initial_call=True)
def pick_night(click):
    if not click:
        return dash.no_update
    return str(click["points"][0]["x"])[:10]


@callback(Output("sleep-night-body", "children"), Input("sleep-night", "value"), Input("theme", "data"))
def night(day, theme):
    if not day:
        return ""
    b = get_bundle()
    r = b.day(day)
    if r.empty or r.get("sl_start_local") is None:
        return html.Div("No sleep record.", className="muted")
    s, e = pd.Timestamp(r["sl_start_local"]), pd.Timestamp(r["sl_end_local"])
    lo, hi = int((s - timedelta(minutes=30)).timestamp() * 1000), int((e + timedelta(minutes=30)).timestamp() * 1000)
    hr = db.query_df("SELECT ts, value FROM intraday WHERE metric='hr' AND ts BETWEEN ? AND ? ORDER BY ts", (lo, hi))
    bbv = db.query_df("SELECT ts, value FROM intraday WHERE metric='bb' AND ts BETWEEN ? AND ? ORDER BY ts", (lo, hi))
    from plotly.subplots import make_subplots

    f = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.6, 0.4])
    f.update_layout(**T.base_layout(theme, 320, showlegend=False))
    if not hr.empty:
        f.add_trace(go.Scatter(x=pd.to_datetime(hr["ts"], unit="ms"), y=hr["value"], mode="lines",
                               line=dict(width=2, color=T.series(theme, 7)), name="Heart rate",
                               hovertemplate="%{y} bpm<extra>HR</extra>"), 1, 1)
    if not bbv.empty:
        f.add_trace(go.Scatter(x=pd.to_datetime(bbv["ts"], unit="ms"), y=bbv["value"], mode="lines",
                               line=dict(width=2, color=T.series(theme, 2), shape="hv"), name="Body Battery",
                               hovertemplate="%{y}<extra>Body Battery</extra>"), 2, 1)
    for row_ in (1, 2):
        f.add_vrect(x0=s, x1=e, fillcolor=T.chrome(theme)["band"], line_width=0, row=row_, col=1)
    f.update_yaxes(title_text="HR bpm", row=1, col=1, gridcolor=T.chrome(theme)["grid"])
    f.update_yaxes(title_text="Body Battery", row=2, col=1, gridcolor=T.chrome(theme)["grid"])
    stages = [("Deep", r["sl_deep_s"]), ("Light", r["sl_light_s"]), ("REM", r["sl_rem_s"]), ("Awake", r["sl_awake_s"])]
    tot = sum((v or 0) for _, v in stages) or 1
    return html.Div([
        html.Div([html.Span(f"{s:%H:%M} → {e:%H:%M}", className="pill me-2")] +
                 [html.Span(f"{n} {v / 60:.0f} min ({100 * v / tot:.0f}%)", className="pill me-2")
                  for n, v in stages if v == v and v],
                 className="my-2"),
        html.Div(r.get("sl_feedback") and str(r.get("sl_feedback")).replace("_", " ").lower() or "",
                 className="muted small"),
        graph(f),
    ])
