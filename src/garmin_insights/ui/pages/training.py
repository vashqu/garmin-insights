"""Training: activity list, load (fitness/fatigue/form, monotony, ACWR), totals, what-if planner."""

from __future__ import annotations

from datetime import date, timedelta

import dash
import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html
from plotly.subplots import make_subplots

from garmin_insights.analytics import load as ld
from garmin_insights.analytics.frame import get_bundle
from garmin_insights.storage import db
from garmin_insights.ui import theme as T
from garmin_insights.ui.components.common import fmt_min, page_header, range_selector, window
from garmin_insights.ui.components.widgets import empty_state, graph, section

dash.register_page(__name__, path="/training", title="Training")

SPORTS = ["run", "strength", "swim", "stretch", "walk", "breathwork", "bike", "other"]
INTENSITY_HRR = {"easy": 0.45, "moderate": 0.62, "hard": 0.78}


def layout(**_):
    return html.Div([
        page_header("Training", "Workouts, training load and planning", range_selector("tr-range", 90)),
        dbc.Tabs([
            dbc.Tab(label="Activities", tab_id="acts"),
            dbc.Tab(label="Load", tab_id="load"),
            dbc.Tab(label="What-if planner", tab_id="plan"),
        ], id="tr-tabs", active_tab="acts", className="mb-3"),
        dcc.Loading(html.Div(id="tr-body"), type="circle", delay_show=400),
    ])


@callback(Output("tr-body", "children"), Input("tr-tabs", "active_tab"), Input("data-version", "data"),
          Input("theme", "data"), Input("tr-range", "value"))
def render(tab, _v, theme, days):
    b = get_bundle()
    if b.empty:
        return empty_state("No data yet")
    if tab == "load":
        return _load(b, theme, days)
    if tab == "plan":
        return _plan_layout(b)
    return _acts(b, days)


def _acts(b, days):
    if b.acts.empty:
        return empty_state("No activities yet")
    a = b.acts[b.acts["date"] >= pd.Timestamp.now().normalize() - timedelta(days=days)].sort_values(
        "start_local", ascending=False)
    rows = []
    for x in a.itertuples():
        rows.append(html.Tr([
            html.Td(pd.Timestamp(x.start_local).strftime("%a %d %b %H:%M")),
            html.Td(dcc.Link(x.type_key.replace("_", " ").title(), href=f"/activity/{x.activity_id}")),
            html.Td(fmt_min((x.duration_s or 0) / 60)),
            html.Td(f"{x.distance_m / 1000:.2f} km" if (x.distance_m or 0) > 100 else ""),
            html.Td(f"{x.avg_hr:.0f}" if x.avg_hr == x.avg_hr and x.avg_hr else ""),
            html.Td(f"{x.trimp:.0f}"),
            html.Td(f"{x.aerobic_te:.1f} / {x.anaerobic_te:.1f}" if x.aerobic_te == x.aerobic_te and x.aerobic_te else ""),
        ]))
    return section(f"{len(a)} activities", html.Table([
        html.Thead(html.Tr([html.Th(h) for h in ("When", "Type", "Duration", "Distance", "Avg HR", "TRIMP",
                                                 "Training effect (aer/anaer)")])),
        html.Tbody(rows)], className="table-clean"))


def _load(b, theme, days):
    D = window(b.daily, max(days, 42))
    c = T.chrome(theme)
    f = T.fig(theme, 300)
    for i, (col, lab) in enumerate([("fitness", "Fitness (42-day)"), ("fatigue", "Fatigue (7-day)"),
                                    ("form", "Form (fitness − fatigue)")]):
        f.add_trace(go.Scatter(x=D.index, y=D[col], name=lab, mode="lines", line=dict(width=2, color=T.series(theme, i)),
                               hovertemplate=f"%{{y:.1f}}<extra>{lab}</extra>"))
    f.add_trace(go.Bar(x=D.index, y=D["load"], name="Daily load (TRIMP)", marker=dict(color=c["grid"], cornerradius=3),
                       hovertemplate="%{y:.0f}<extra>Daily TRIMP</extra>"))
    f.add_hline(y=0, line=dict(color=c["axis"], width=1))

    g = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                      subplot_titles=("Monotony (7-day mean / SD)", "Uncoupled acute:chronic ratio"))
    g.update_layout(**T.base_layout(theme, 340, showlegend=False))
    g.add_trace(go.Scatter(x=D.index, y=D["monotony"], mode="lines", line=dict(width=2, color=T.series(theme, 0)),
                           hovertemplate="%{y:.2f}<extra>Monotony</extra>"), 1, 1)
    g.add_hline(y=2, line=dict(dash="dot", color=c["muted"], width=1), row=1, col=1)
    g.add_trace(go.Scatter(x=D.index, y=D["acwr_uncoupled"], mode="lines", line=dict(width=2, color=T.series(theme, 0)),
                           hovertemplate="%{y:.2f}<extra>ACWR (uncoupled)</extra>"), 2, 1)
    g.add_hrect(y0=0.8, y1=1.3, fillcolor=c["band"], line_width=0, row=2, col=1)
    for an in g.layout.annotations:
        an.font = dict(size=12, color=c["ink2"])
    for i in (1, 2):
        g.update_yaxes(gridcolor=c["grid"], row=i, col=1)

    # weekly minutes by sport (stacked, fixed slot order)
    acts = b.acts[b.acts["date"] >= D.index.min()] if not b.acts.empty else pd.DataFrame()
    h = T.fig(theme, 280, barmode="stack")
    if not acts.empty:
        w = acts.assign(week=acts["date"].dt.to_period("W-SUN").dt.start_time,
                        mins=acts["duration_s"] / 60).pivot_table(index="week", columns="sport_group", values="mins",
                                                                  aggfunc="sum", fill_value=0)
        for sp in [s for s in SPORTS if s in w]:
            h.add_trace(go.Bar(x=w.index, y=w[sp], name=sp.title(),
                               marker=dict(color=T.series(theme, SPORTS.index(sp)), line=dict(width=1, color=c["surface"])),
                               hovertemplate=f"{sp.title()}: %{{y:.0f}} min<extra></extra>"))
        h.update_yaxes(title="minutes / week")
    totals = []
    if not acts.empty:
        m = acts.assign(month=acts["date"].dt.to_period("M").astype(str)).groupby(["month", "sport_group"]).agg(
            n=("activity_id", "count"), mins=("duration_s", lambda s: s.sum() / 60),
            km=("distance_m", lambda s: s.sum() / 1000)).reset_index().sort_values("month", ascending=False)
        totals = html.Table([html.Thead(html.Tr([html.Th(x) for x in ("Month", "Sport", "Sessions", "Time", "Distance")])),
                             html.Tbody([html.Tr([html.Td(r.month), html.Td(r.sport_group.title()), html.Td(r.n),
                                                  html.Td(fmt_min(r.mins)),
                                                  html.Td(f"{r.km:.1f} km" if r.km > 0.1 else "")])
                                         for r in m.itertuples()])], className="table-clean")
    return html.Div([
        section("Fitness, fatigue & form", graph(f),
                info_text="Daily load = Banister TRIMP from average heart rate (no RPE logging). Fitness and fatigue "
                          "are 42- and 7-day exponentially weighted loads; form = fitness − fatigue. Strength "
                          "sessions are under-counted by HR-based load — muscle volume is tracked on the Muscle page.",
                evidence="moderate"),
        section("Load warning signals", graph(g),
                info_text="Monotony > 2 with high load was linked to overreaching/illness (Foster 1998). The "
                          "acute:chronic ratio excludes the acute week from the chronic baseline to avoid the "
                          "mathematical-coupling flaw (Lolli et al.). Both are screening signals, not predictions.",
                evidence="moderate"),
        section("Minutes per week by sport", graph(h)),
        section("Monthly totals", totals or html.Div("No activities.", className="muted")),
    ])


# ------------------------------------------------------------------ what-if

def _plan_layout(b):
    today = date.today()
    return html.Div([
        section("Plan sessions", html.Div([
            dcc.DatePickerSingle(id="pl-date", date=(today + timedelta(days=1)).isoformat(),
                                 min_date_allowed=today.isoformat(), display_format="ddd D MMM"),
            dcc.Dropdown(id="pl-sport", options=[s for s in SPORTS if s != "other"], value="strength",
                         clearable=False, style={"width": "150px"}),
            dcc.Input(id="pl-min", type="number", value=60, min=5, max=300, step=5, className="form-control",
                      style={"width": "90px"}),
            dcc.Dropdown(id="pl-int", options=list(INTENSITY_HRR), value="moderate", clearable=False,
                         style={"width": "140px"}),
            html.Button("Add", id="pl-add", className="btn-primary-s"),
            html.Button("Clear plan", id="pl-clear", className="btn-ghost"),
        ], className="d-flex gap-2 align-items-center flex-wrap"),
            info_text="Estimated load per planned session uses your own history: the median TRIMP per minute of "
                      "past sessions of that sport, scaled by intensity."),
        html.Div(id="pl-body"),
    ])


def _estimate_trimp(b, sport: str, minutes: float, intensity: str) -> float:
    a = b.acts[b.acts["sport_group"] == sport] if not b.acts.empty else pd.DataFrame()
    prof = b.profile
    base = ld.trimp(minutes, None, 55, prof["max_hr"], prof["sex"], sport)
    hrr = INTENSITY_HRR[intensity]
    model = ld.trimp(minutes, 55 + hrr * (prof["max_hr"] - 55), 55, prof["max_hr"], prof["sex"], sport)
    if len(a) >= 3:
        per_min = (a["trimp"] / (a["duration_s"] / 60)).median()
        scale = {"easy": 0.7, "moderate": 1.0, "hard": 1.4}[intensity]
        return float(per_min * minutes * scale)
    return float(model or base)


@callback(Output("pl-body", "children"), Input("pl-add", "n_clicks"), Input("pl-clear", "n_clicks"),
          Input("theme", "data"), State("pl-date", "date"), State("pl-sport", "value"), State("pl-min", "value"),
          State("pl-int", "value"))
def plan(add, clear, theme, d, sport, mins, inten):
    trig = dash.ctx.triggered_id
    if trig == "pl-add" and d and mins:
        db.upsert("plan_session", [{"date": d[:10], "sport": sport, "duration_min": float(mins), "intensity": inten}])
    if trig == "pl-clear":
        db.execute("DELETE FROM plan_session")
    db.execute("DELETE FROM plan_session WHERE date < ?", (date.today().isoformat(),))
    b = get_bundle()
    ps = db.query_df("SELECT * FROM plan_session ORDER BY date")
    D = b.daily
    today = pd.Timestamp(date.today())
    horizon = pd.date_range(today + timedelta(days=1), periods=14, freq="D")
    planned = pd.Series(0.0, index=horizon)
    rows = []
    for p in ps.itertuples():
        t = _estimate_trimp(b, p.sport, p.duration_min, p.intensity)
        dd = pd.Timestamp(p.date)
        if dd in planned.index:
            planned[dd] += t
        rows.append(html.Tr([html.Td(dd.strftime("%a %d %b")), html.Td(p.sport.title()), html.Td(f"{p.duration_min:.0f} min"),
                             html.Td(p.intensity), html.Td(f"≈ {t:.0f}")]))
    hist = D["load"].loc[:today].fillna(0)
    full = pd.concat([hist, planned])
    L = ld.load_frame(full)
    view = L.loc[today - timedelta(days=28):]
    f = T.fig(theme, 300)
    for i, (col, lab) in enumerate([("fitness", "Fitness"), ("fatigue", "Fatigue"), ("form", "Form")]):
        f.add_trace(go.Scatter(x=view.index, y=view[col], name=lab, mode="lines", line=dict(width=2, color=T.series(theme, i)),
                               hovertemplate=f"%{{y:.1f}}<extra>{lab}</extra>"))
    f.add_trace(go.Bar(x=planned.index, y=planned.values, name="Planned load", marker=dict(color=T.series(theme, 3),
                                                                                          cornerradius=3),
                       hovertemplate="%{y:.0f}<extra>Planned TRIMP</extra>"))
    f.add_vline(x=today, line=dict(dash="dot", color=T.chrome(theme)["muted"], width=1))
    end = L.iloc[-1]
    msgs = []
    if end["monotony"] == end["monotony"] and end["monotony"] > 2:
        msgs.append("Planned week is monotonous (monotony > 2) — mix easy and hard days.")
    if end["acwr_uncoupled"] == end["acwr_uncoupled"] and end["acwr_uncoupled"] > 1.5:
        msgs.append("Planned load is > 1.5× your recent weekly average — consider spreading it out.")
    planned_sets = ps[ps["sport"] == "strength"] if not ps.empty else ps
    return html.Div([
        section("Projection (dotted line = today)", graph(f)),
        html.Div([html.Div(m, className="flag") for m in msgs]),
        section("Planned sessions", html.Table([html.Thead(html.Tr([html.Th(x) for x in
                                                                     ("Date", "Sport", "Duration", "Intensity", "Est. TRIMP")])),
                                                html.Tbody(rows)], className="table-clean") if rows else
                html.Div("Nothing planned yet.", className="muted")),
        html.Div(f"{len(planned_sets)} strength session(s) planned.", className="muted small") if len(planned_sets) else "",
    ])


_ = np
