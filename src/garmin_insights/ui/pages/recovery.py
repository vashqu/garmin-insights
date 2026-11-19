"""Recovery: HRV (if available), resting HR + illness watch, respiration, SpO2/skin temp, all-day HR."""

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

dash.register_page(__name__, path="/recovery", title="Recovery")


def layout(**_):
    return html.Div([page_header("Recovery", "HRV, resting heart rate, breathing and early-warning signals",
                                 range_selector("rec-range", 90)),
                     dcc.Loading(html.Div(id="rec-body"), type="circle", delay_show=400)])


@callback(Output("rec-body", "children"), Input("data-version", "data"), Input("theme", "data"),
          Input("rec-range", "value"))
def render(_v, theme, days):
    b = get_bundle()
    if b.empty:
        return empty_state("No data yet")
    D = window(b.daily, days)
    last = b.latest_date()
    r = b.daily.loc[last]
    parts = []
    cards = [metric_card("rhr", r["rhr"], r["rhr_status"], theme, D["rhr"], f"your normal {r['rhr_med']:.0f} bpm"
                         if r["rhr_med"] == r["rhr_med"] else None),
             metric_card("resp_sleep", r["resp_sleep"], r["resp_sleep_status"], theme, D["resp_sleep"])]
    if b.flags["hrv_available"]:
        cards.insert(0, metric_card("hrv_ln7", r["hrv_ln7"], r.get("hrv_ln7_status"), theme, D["hrv_ln7"],
                                    f"last night {r['hrv_night']:.0f} ms" if r["hrv_night"] == r["hrv_night"] else None))
    if b.flags["spo2_available"]:
        cards.append(metric_card("spo2", r["spo2"], r["spo2_status"], theme, D["spo2"]))
    if b.flags["skin_temp_available"]:
        cards.append(metric_card("skin_temp", r["skin_temp"], r["skin_temp_status"], theme, D["skin_temp"]))
    parts.append(html.Div(cards, className="grid g4 mb-3"))

    if b.flags["hrv_available"]:
        f = baseline_chart(D, "hrv_ln7", theme, "7-day lnRMSSD", "ln ms", lo="hrv_swc_lo", hi="hrv_swc_hi",
                           status="hrv_ln7_status", fmt=".2f", band_label="Smallest worthwhile change band")
        f.add_trace(go.Scatter(x=D.index, y=D["hrv_ln"], mode="markers", name="Nightly lnRMSSD",
                               marker=dict(size=5, color=T.chrome(theme)["muted"]),
                               hovertemplate="%{y:.2f}<extra>night</extra>"))
        parts.append(section("HRV trend vs smallest worthwhile change", graph(f), info_text=M["hrv_ln7"].why,
                             evidence="strong"))
    else:
        parts.append(section("HRV", html.Div([
            html.P("Your watch hasn't recorded overnight HRV (Garmin returns no HRV data for your account). "
                   "HRV Status needs a compatible watch worn overnight. Until then, readiness is computed from "
                   "resting HR, sleep, regularity, stress and Body Battery (weights re-balanced automatically)."),
        ], className="muted")))

    rhr = baseline_chart(D, "rhr", theme, "Resting HR", "bpm", "rhr_med", "rhr_scale", "rhr_status", fmt=".0f")
    ill = D[D["illness_level"] != "none"]
    if len(ill):
        rhr.add_trace(go.Scatter(x=ill.index, y=ill["rhr"] + 1.5, mode="markers+text", text=["⚠"] * len(ill),
                                 textposition="top center", marker=dict(size=1, color="rgba(0,0,0,0)"),
                                 name="Illness watch", hovertemplate="Illness watch: %{x|%d %b}<extra></extra>"))
    parts.append(section("Resting heart rate & illness watch", graph(rhr),
                         info_text=M["rhr"].why + " ⚠ marks days where a 4-day windowed CuSum of resting-HR "
                         "elevations crossed its threshold (heuristic, inspired by Alavi et al. 2022).",
                         evidence="strong"))
    resp = baseline_chart(D, "resp_sleep", theme, "Sleep respiration", "brpm", "resp_sleep_med", "resp_sleep_scale",
                          "resp_sleep_status")
    extra = [section("Breathing rate during sleep", graph(resp), info_text=M["resp_sleep"].why)]
    if b.flags["spo2_available"]:
        extra.append(section("SpO2", graph(baseline_chart(D, "spo2", theme, "SpO2", "%", "spo2_med", "spo2_scale",
                                                          "spo2_status", fmt=".0f"))))
    else:
        extra.append(section("SpO2 & skin temperature", html.Div(
            "Not recorded by your watch. If your watch supports Pulse Ox, enabling it during sleep (Watch "
            "settings → Health & Wellness → Pulse Ox) adds another early-warning signal — at some battery cost.",
            className="muted")))
    parts.append(html.Div(extra, className="grid g2"))

    days_with_hr = [d for d in b.daily.index[b.daily["rhr"].notna()]][::-1]
    parts.append(section("All-day heart rate", [
        dcc.Dropdown(id="rec-day", options=[{"label": d.strftime("%a %d %b %Y"), "value": d.strftime("%Y-%m-%d")}
                                            for d in days_with_hr[:120]],
                     value=days_with_hr[0].strftime("%Y-%m-%d") if days_with_hr else None, clearable=False,
                     style={"maxWidth": "260px"}),
        html.Div(id="rec-day-body")]))
    return html.Div(parts)


@callback(Output("rec-day-body", "children"), Input("rec-day", "value"), Input("theme", "data"))
def day_hr(day, theme):
    if not day:
        return ""
    hr = db.query_df("SELECT ts, value FROM intraday WHERE metric='hr' AND date=? ORDER BY ts", (day,))
    if hr.empty:
        return html.Div("No intraday heart rate for this day.", className="muted")
    b = get_bundle()
    acts = b.acts[b.acts["date"] == pd.Timestamp(day)] if not b.acts.empty else pd.DataFrame()
    f = T.fig(theme, 260, showlegend=False)
    f.add_trace(go.Scatter(x=pd.to_datetime(hr["ts"], unit="ms"), y=hr["value"], mode="lines",
                           line=dict(width=2, color=T.series(theme, 7)), hovertemplate="%{y} bpm<extra></extra>"))
    for _, a in acts.iterrows():
        s = pd.Timestamp(a["start_local"])
        f.add_vrect(x0=s, x1=s + pd.Timedelta(seconds=a["duration_s"] or 0), fillcolor=T.chrome(theme)["band"],
                    line_width=0, annotation_text=a["type_key"].replace("_", " "), annotation_position="top left",
                    annotation_font=dict(size=10, color=T.chrome(theme)["ink2"]))
    r = b.day(day)
    if r.get("rhr") == r.get("rhr"):
        f.add_hline(y=r["rhr"], line=dict(width=1, dash="dot", color=T.chrome(theme)["muted"]),
                    annotation_text=f"resting {r['rhr']:.0f}", annotation_position="bottom right",
                    annotation_font=dict(size=10, color=T.chrome(theme)["muted"]))
    f.update_yaxes(title="bpm")
    return graph(f)
