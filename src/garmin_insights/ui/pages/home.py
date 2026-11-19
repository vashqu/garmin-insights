"""Today (briefing-first morning check-in)."""

from __future__ import annotations

from datetime import datetime, timedelta

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Input, Output, State, callback, ctx, dcc, html

from garmin_insights import config
from garmin_insights.analytics.frame import get_bundle
from garmin_insights.analytics.metrics import M
from garmin_insights.recommend import briefing
from garmin_insights.recommend.race import fmt_time
from garmin_insights.storage import journal
from garmin_insights.ui import theme as T
from garmin_insights.ui.components.widgets import empty_state, graph, info, metric_card, section, status_badge

dash.register_page(__name__, path="/", title="Today")

CARDS = [("hrv_ln7", "hrv_ln7_status", "/recovery"), ("rhr", "rhr_status", "/recovery"),
         ("sleep_h", "sleep_h_status", "/sleep"), ("sri7", "sri7_status", "/sleep"),
         ("stress_avg", "stress_avg_status", "/stress"), ("bb_max", "bb_max_status", "/stress"),
         ("resp_sleep", "resp_sleep_status", "/recovery"), ("steps", "steps_status", "/longevity"),
         ("spo2", "spo2_status", "/recovery"), ("skin_temp", "skin_temp_status", "/recovery")]


def layout(day: str | None = None, **_):
    return html.Div([
        dcc.Store(id="home-day", data=day),
        dcc.Loading(html.Div(id="home-body"), type="circle", delay_show=400),
    ])


def _greeting() -> str:
    h = datetime.now().hour
    return "Good morning" if h < 12 else ("Good afternoon" if h < 18 else "Good evening")


def _datenav(d: pd.Timestamp, latest: pd.Timestamp) -> html.Div:
    prev_d = (d - timedelta(days=1)).strftime("%Y-%m-%d")
    nxt = d + timedelta(days=1)
    items = [dcc.Link("‹", href=f"/?day={prev_d}", className="btn-ghost"),
             html.Span(d.strftime("%A %d %B %Y"), className="px-2")]
    if nxt <= latest:
        items.append(dcc.Link("›", href=f"/?day={nxt.strftime('%Y-%m-%d')}" if nxt < latest else "/",
                              className="btn-ghost"))
    if d != latest:
        items.append(dcc.Link("Today", href="/", className="btn-ghost"))
    return html.Div(items, className="datenav")


def _readiness_fig(row: pd.Series, theme: str) -> go.Figure:
    comps = [("hrv", "HRV"), ("rhr", "Resting HR"), ("sleep", "Sleep vs need"), ("sri", "Regularity"),
             ("stress", "Stress (yday)"), ("bb", "Body Battery"), ("mods", "Flags")]
    xs, ys = [], []
    for k, lab in comps:
        v = row.get("rc_" + k)
        if v is not None and not (isinstance(v, float) and np.isnan(v)) and (k != "mods" or v != 0):
            xs.append(lab)
            ys.append(float(v))
    f = T.fig(theme, 190, margin=dict(l=90, r=16, t=8, b=24), hovermode="closest", showlegend=False)
    if not xs:
        return T.empty_fig(theme, "Not enough data yet", 190)
    neg = T.SERIES[T.mode(theme)][7]
    pos = T.SERIES[T.mode(theme)][0]
    f.add_trace(go.Bar(y=xs[::-1], x=ys[::-1], orientation="h",
                       marker=dict(color=[pos if v >= 0 else neg for v in ys[::-1]], cornerradius=4),
                       hovertemplate="%{y}: %{x:+.1f} points<extra></extra>"))
    f.update_xaxes(zeroline=True, zerolinecolor=T.chrome(theme)["axis"], title="points vs 50 (your normal)",
                   title_font=dict(size=11))
    f.update_yaxes(showgrid=False)
    return f


def _load_fig(D: pd.DataFrame, d: pd.Timestamp, theme: str) -> go.Figure:
    x = D.loc[d - timedelta(days=60):d]
    f = T.fig(theme, 190, margin=dict(l=36, r=60, t=8, b=24))
    for i, (c, lab) in enumerate([("fitness", "Fitness (42d)"), ("fatigue", "Fatigue (7d)")]):
        f.add_trace(go.Scatter(x=x.index, y=x[c], name=lab, mode="lines", line=dict(width=2, color=T.series(theme, i)),
                               hovertemplate=f"{lab}: %{{y:.1f}}<extra></extra>"))
        f.add_annotation(x=x.index[-1], y=x[c].iloc[-1], text=lab.split(" ")[0], showarrow=False, xanchor="left",
                         xshift=6, font=dict(size=11, color=T.chrome(theme)["ink2"]))
    f.update_layout(showlegend=False)
    f.update_xaxes(range=[x.index[0], x.index[-1] + timedelta(days=6)])
    return f


def _forecast_fig(D: pd.DataFrame, d: pd.Timestamp, theme: str):
    from garmin_insights.analytics import forecast as fc

    fr = fc.cached_forecast("readiness")
    if fr is None or fr.empty:
        return None
    x = D.loc[d - timedelta(days=21):d, "readiness"].dropna()
    f = T.fig(theme, 190, margin=dict(l=36, r=16, t=8, b=24), showlegend=False)
    c = T.series(theme, 0)
    f.add_trace(go.Scatter(x=fr.index, y=fr["q90"], mode="lines", line=dict(width=0), hoverinfo="skip"))
    f.add_trace(go.Scatter(x=fr.index, y=fr["q10"], mode="lines", line=dict(width=0), fill="tonexty",
                           fillcolor=T.chrome(theme)["band"], hoverinfo="skip"))
    f.add_trace(go.Scatter(x=x.index, y=x.values, mode="lines", line=dict(width=2, color=c),
                           hovertemplate="%{y:.0f}<extra>Readiness</extra>"))
    f.add_trace(go.Scatter(x=fr.index, y=fr["q50"], mode="lines", line=dict(width=2, color=c, dash="dot"),
                           hovertemplate="%{y:.0f}<extra>Forecast (median)</extra>"))
    f.update_yaxes(range=[0, 100])
    return f


def _recs(recs) -> html.Div:
    out = []
    for r in recs:
        ev_rows = [html.Tr([html.Td(k.replace("_", " "), className="muted"), html.Td(str(v))])
                   for k, v in r.evidence.items() if v is not None]
        out.append(html.Div([
            html.Div(r.icon, className="rec-icon"),
            html.Div([html.Div([r.title, html.Span(r.evidence_level, className="ev-level")], className="rec-title"),
                      html.Div(r.action, className="rec-action")]),
            html.Details([html.Summary("Why?"), html.Div(r.reason),
                          html.Table(ev_rows, className="ev-table") if ev_rows else ""]),
        ], className="rec"))
    return html.Div(out)


def _tags(day: str) -> html.Div:
    vals = journal.day_values(day)
    btns = []
    for t in journal.tags():
        v = vals.get(t["tag"], 0) or 0
        if t["kind"] == "count":
            label = f"{t['label'].split(' (')[0]}: {int(v)}" if v else t["label"].split(" (")[0]
        else:
            label = t["label"]
        btns.append(html.Button(label, id={"type": "qtag", "tag": t["tag"], "kind": t["kind"]},
                                className="tag-btn on" if v else "tag-btn", n_clicks=0))
    return html.Div(btns, className="tagrow", id="qtag-row")


@callback(Output("home-body", "children"), Input("data-version", "data"), Input("theme", "data"),
          Input("home-day", "data"))
def render(_ver, theme, day):
    b = get_bundle()
    if b.empty:
        return empty_state("No data yet", "Your first sync is running — this page fills in automatically. "
                                          "Check Settings if nothing appears after a few minutes.")
    latest = b.latest_date()
    d = pd.Timestamp(day).normalize() if day else latest
    m = briefing.morning_fast(b, d)
    _row, D = m.facts, b.daily
    r = b.day(d)
    yday = (d - timedelta(days=1)).strftime("%Y-%m-%d")

    # flags
    flags = []
    if r.get("illness_level") in ("watch", "likely"):
        flags.append(html.Div([html.Span("⚠"), html.Div([html.B("Resting HR acutely elevated. "),
                                                        "Signals like this often precede illness or reflect "
                                                        "high strain — keep today easy."])],
                              className="flag critical" if r.get("illness_level") == "likely" else "flag"))
    if (r.get("sleep_debt7") or 0) >= 4:
        flags.append(html.Div([html.Span("☾"), html.Div(f"Sleep debt {r.get('sleep_debt7'):.1f} h over 7 nights.")],
                              className="flag"))
    if not b.flags.get("sex_set"):
        flags.append(html.Div([html.Span("ⓘ"), html.Div(["Set your sex in ", dcc.Link("Settings", href="/settings"),
                                                         " for accurate training load and VO2max comparisons."])],
                              className="flag"))

    # cards
    cards = []
    for key, st, href in CARDS:
        if key == "hrv_ln7" and not b.flags.get("hrv_available"):
            continue
        if key == "spo2" and not b.flags.get("spo2_available"):
            continue
        if key == "skin_temp" and not b.flags.get("skin_temp_available"):
            continue
        src = r
        partial = key in ("steps", "stress_avg") and d == latest and d.date() == datetime.now().date()
        if partial:  # today's totals are incomplete → judge yesterday's full day
            src = b.day(d - timedelta(days=1))
        val = src.get(key)
        sub = None
        if key == "sleep_h" and r.get("sleep_need_h"):
            sub = f"need ≈ {r.get('sleep_need_h'):.1f} h"
        elif key + "_med" in src and src.get(key + "_med") == src.get(key + "_med"):
            sub = f"your normal {M[key].fmt.format(src.get(key + '_med'))}"
        if partial:
            sub = "yesterday · " + (sub or "")
        end = d - timedelta(days=1) if partial else d
        spark = D.loc[end - timedelta(days=13):end, key] if key in D else None
        cards.append(metric_card(key, val, src.get(st), theme, spark, sub, href=href))

    score = r.get("readiness")
    ready_block = html.Div([
        html.Div(["Readiness", info(M["readiness"].why, "heuristic")], className="muted small"),
        html.Div(f"{score:.0f}" if score == score and score is not None else "–", className="ready-num"),
        status_badge(r.get("readiness_status")),
        html.Div("vs your own norms · 0–100", className="ready-sub"),
    ])

    race = m.extra.get("race")
    race_card = None
    if race:
        from garmin_insights.recommend.race import build_plan

        p = build_plan(b, race, d.date())
        race_card = section(f"⚑ {race['name']}", html.Div([
            html.Div([html.Span(f"{max(p.weeks_out, 0):.0f} weeks out · {p.phase}", className="pill")]),
            html.Div(className="kv mt-2", children=[
                html.Span("Race readiness", className="k"), html.Span(f"{p.readiness_pct:.0f}%"),
                html.Span("Predicted", className="k"), html.Span(fmt_time(p.predicted_s)),
                html.Span("Goal", className="k"), html.Span(fmt_time(race.get("goal_s"))),
                html.Span("This week", className="k"), html.Span(f"{p.week_plan['weekly_km']} km"),
            ]),
            dcc.Link("Open race plan →", href="/running", className="small"),
        ]))

    y_acts = b.acts[b.acts["date"] == d - timedelta(days=1)] if not b.acts.empty else pd.DataFrame()
    acts_list = [html.Div([dcc.Link(f"{a['type_key'].replace('_', ' ').title()}", href=f"/activity/{a['activity_id']}"),
                           html.Span(f" · {round((a['duration_s'] or 0) / 60)} min"
                                     + (f" · {a['distance_m'] / 1000:.1f} km" if (a['distance_m'] or 0) > 100 else "")
                                     + (f" · avg HR {a['avg_hr']:.0f}" if a['avg_hr'] == a['avg_hr'] and a['avg_hr'] else ""),
                                     className="muted")]) for _, a in y_acts.iterrows()]
    strong_y = b.sets[b.sets["date"] == yday] if not b.sets.empty else pd.DataFrame()
    if len(strong_y):
        acts_list.append(html.Div([html.Span(f"Strong: {strong_y['workout_name'].iloc[0].strip()} · "),
                                   html.Span(f"{len(strong_y)} sets, {strong_y['exercise'].nunique()} exercises",
                                             className="muted")]))

    fc = _forecast_fig(D, d, theme) if d == latest else None
    return html.Div([
        html.Div([html.Div([html.H1(f"{_greeting()}", className="greeting") if d == latest else
                            html.H1(d.strftime("%A"), className="greeting")]),
                  _datenav(d, latest)], className="d-flex justify-content-between align-items-center mb-3"),
        html.Div([
            html.Div([
                section("Briefing", [html.Div(m.text, id="briefing-text", className="briefing"),
                                     html.Div("Writing your briefing with the local model…", id="briefing-src",
                                              className="briefing-src")]),
                html.Div(flags),
                section("What to do today", _recs(m.recs[:4]) if m.recs else
                        html.Div("No specific recommendations — keep doing what you're doing.", className="muted")),
            ]),
            html.Div([section("", ready_block), race_card or "",
                      section("Yesterday", acts_list or html.Div("No workouts logged.", className="muted"))]),
        ], className="hero"),
        html.Div(cards, className="grid g4 mb-3"),
        html.Div([
            section("Readiness breakdown", graph(_readiness_fig(r, theme)),
                    info_text="Each bar is how much a component moved today's score above (blue) or below (red) "
                              "50, which represents your personal normal. Weights: HRV 35%, resting HR 20%, sleep "
                              "vs need 15%, regularity 10%, stress 10%, Body Battery 10% — missing inputs are "
                              "re-weighted.", evidence="heuristic"),
            section("Fitness & fatigue (60 days)", graph(_load_fig(D, d, theme)),
                    info_text="Banister impulse-response style: fitness = 42-day, fatigue = 7-day exponentially "
                              "weighted HR-based training load (TRIMP). Fatigue above fitness means you're carrying "
                              "fresh load.", evidence="moderate"),
            section("Readiness forecast (7 days)", graph(fc) if fc is not None else
                    html.Div("Forecast appears once ~6 weeks of readiness history exist.", className="muted small"),
                    info_text="Chronos-Bolt-tiny (open-source time-series foundation model, runs locally). "
                              "Band = 10–90% range.", evidence="heuristic"),
        ], className="grid g3"),
        section(f"Journal · {(d - timedelta(days=1)).strftime('%A %d %b')}", [
            html.Div("Tap what applied yesterday — it feeds your personal 'what affects you' analysis.",
                     className="muted small mb-2"),
            _tags(yday), dcc.Store(id="qtag-day", data=yday)]),
        html.Div("Not medical advice. Metrics are compared with your own history; evidence levels are shown "
                 "next to each recommendation.", className="footer-note"),
    ])


@callback(Output("briefing-text", "children"), Output("briefing-src", "children"),
          Input("home-body", "children"), State("home-day", "data"), prevent_initial_call=True)
def llm_briefing(_children, day):
    b = get_bundle()
    if b.empty:
        return dash.no_update, ""
    d = pd.Timestamp(day).normalize() if day else b.latest_date()
    m = briefing.morning(b, d)
    if m.source in ("llm", "cached"):
        model = config.Settings.load().llm_briefing_model
        return m.text, f"Written by {model} (local) · every number checked against your data"
    return dash.no_update, f"Template text · local model unavailable ({m.source})"


@callback(Output({"type": "qtag", "tag": ALL, "kind": ALL}, "className"),
          Output({"type": "qtag", "tag": ALL, "kind": ALL}, "children"),
          Input({"type": "qtag", "tag": ALL, "kind": ALL}, "n_clicks"), State("qtag-day", "data"),
          prevent_initial_call=True)
def toggle_tag(_clicks, day):
    trig = ctx.triggered_id
    if trig and ctx.triggered and ctx.triggered[0].get("value"):
        vals = journal.day_values(day)
        cur = vals.get(trig["tag"], 0) or 0
        if trig["kind"] == "count":
            new = cur + 1 if cur < 6 else 0
        else:
            new = 0 if cur else 1
        journal.set_value(day, trig["tag"], new)
    vals = journal.day_values(day)
    classes, labels = [], []
    for t in [x["id"] for x in ctx.outputs_list[0]]:
        tag = next((x for x in journal.tags() if x["tag"] == t["tag"]), None)
        v = vals.get(t["tag"], 0) or 0
        classes.append("tag-btn on" if v else "tag-btn")
        lab = tag["label"] if tag else t["tag"]
        if t["kind"] == "count":
            lab = f"{lab.split(' (')[0]}: {int(v)}" if v else lab.split(" (")[0]
        labels.append(lab)
    return classes, labels
