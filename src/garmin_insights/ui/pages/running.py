"""Running & race mode."""

from __future__ import annotations

from datetime import date, timedelta

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html

from garmin_insights.analytics import frame
from garmin_insights.analytics import sports as sp
from garmin_insights.analytics.frame import get_bundle
from garmin_insights.recommend.race import active_race, build_plan, fmt_time
from garmin_insights.storage import db
from garmin_insights.sync.fit import load_samples
from garmin_insights.ui import theme as T
from garmin_insights.ui.components.common import kv, page_header
from garmin_insights.ui.components.widgets import graph, section

dash.register_page(__name__, path="/running", title="Running & race")

DIST_OPTS = [{"label": k, "value": v} for k, v in sp.RACE_DISTANCES.items()]


def layout(**_):
    return html.Div([page_header("Running & race", "Fitness markers, predictions and an adaptive race plan"),
                     html.Div(_race_form(), id="rc-form-wrap"), html.Div(id="run-race"), dcc.Loading(html.Div(id="run-body"), type="circle", delay_show=400)])


def _parse_time(s: str | None) -> float | None:
    if not s:
        return None
    try:
        parts = [float(x) for x in s.strip().split(":")]
    except ValueError:
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec if sec > 0 else None


def _race_form():
    return section("Add a race", html.Div([
        dcc.Input(id="rc-name", placeholder="Race name", className="form-control", style={"width": "200px"}),
        dcc.DatePickerSingle(id="rc-date", min_date_allowed=date.today().isoformat(),
                             date=(date.today() + timedelta(weeks=10)).isoformat(), display_format="D MMM YYYY"),
        dcc.Dropdown(id="rc-dist", options=DIST_OPTS, value=10000, clearable=False, style={"width": "170px"}),
        dcc.Input(id="rc-goal", placeholder="Goal time h:mm:ss (optional)", className="form-control",
                  style={"width": "220px"}),
        dcc.Dropdown(id="rc-terrain", options=["flat", "hilly", "trail"], value="flat", clearable=False,
                     style={"width": "110px"}),
        html.Button("Create plan", id="rc-save", className="btn-primary-s"),
    ], className="d-flex gap-2 flex-wrap align-items-center"),
        info_text="Phases: Base → Build → Peak → 2-week taper (cut volume ~40–60% while keeping intensity and "
                  "frequency — Bosquet et al. 2007 meta-analysis). Weekly targets adapt to your recent volume and "
                  "daily readiness.", evidence="moderate")


@callback(Output("run-race", "children"), Output("rc-form-wrap", "style"), Input("data-version", "data"), Input("theme", "data"),
          Input("rc-save", "n_clicks"), Input({"type": "rc-del", "id": dash.ALL}, "n_clicks"),
          State("rc-name", "value"), State("rc-date", "date"), State("rc-dist", "value"), State("rc-goal", "value"),
          State("rc-terrain", "value"))
def race_panel(_v, theme, save, dels, name, d, dist, goal, terrain):
    trig = dash.ctx.triggered_id
    if trig == "rc-save" and save and d:
        db.execute("UPDATE race SET active=0")
        db.upsert("race", [{"name": name or "My race", "date": d[:10], "distance_m": float(dist),
                            "goal_s": _parse_time(goal), "terrain": terrain, "active": 1}])
    if isinstance(trig, dict) and trig.get("type") == "rc-del" and any(dels):
        db.execute("UPDATE race SET active=0 WHERE id=?", (trig["id"],))
    race = active_race()
    if not race:
        return "", {}
    b = get_bundle()
    p = build_plan(b, race)
    comp_names = {"volume": "Weekly volume", "long_run": "Long run", "consistency": "Consistency (≥3 runs/wk)",
                  "pace": "Predicted vs goal pace"}
    f = T.fig(theme, 180, hovermode="closest", showlegend=False, margin=dict(l=150, r=16, t=8, b=24))
    f.add_trace(go.Bar(y=[comp_names[k] for k in p.components][::-1], x=[v * 100 for v in p.components.values()][::-1],
                       orientation="h", marker=dict(color=T.series(theme, 0), cornerradius=4),
                       hovertemplate="%{y}: %{x:.0f}%<extra></extra>"))
    f.update_xaxes(range=[0, 100], title="% of target")
    f.update_yaxes(showgrid=False)
    phases = ["Base", "Build", "Peak", "Taper", "Race week"]
    timeline = html.Div([html.Span(ph, className="pill me-1" + (" fw-bold" if ph == p.phase else ""),
                                   style={"background": "var(--accent)", "color": "#fff"} if ph == p.phase else {})
                         for ph in phases], className="mb-2")
    return html.Div([
        section(f"⚑ {race['name']} · {pd.Timestamp(race['date']):%a %d %b %Y}", html.Div([
            html.Div([
                timeline,
                kv([("Weeks out", f"{max(p.weeks_out, 0):.1f}"),
                    ("Distance", f"{race['distance_m'] / 1000:g} km"),
                    ("Goal", fmt_time(race.get("goal_s"))),
                    ("Predicted", f"{fmt_time(p.predicted_s)} ({p.predicted_src})"),
                    ("Riegel from recent runs", fmt_time(p.riegel_pred_s)),
                    ("Race readiness", f"{p.readiness_pct:.0f}%"),
                    ("Current weekly km (4-wk avg)", f"{p.weekly_km_now:.1f}"),
                    ("Longest run (4 wk)", f"{p.longest_4w_km:.1f} km")]),
            ]),
            html.Div([html.H4("This week", style={"fontSize": "14px"}),
                      kv([("Target volume", f"{p.week_plan['weekly_km']} km"),
                          ("Long run", f"{p.week_plan['long_run_km']} km"),
                          ("Strength", p.week_plan["strength"])]),
                      html.Ul([html.Li(s) for s in p.week_plan["key_sessions"]], className="mt-2"),
                      *[html.Div(w, className="flag") for w in p.warnings]]),
            graph(f),
        ], className="grid g3"), right=html.Button("End race plan", id={"type": "rc-del", "id": race["id"]},
                                                     className="btn-ghost"),
            info_text="Race readiness weighs weekly volume (30%), longest recent run (25%), consistency (20%) and "
                      "predicted-vs-goal pace (25%) against distance-specific targets for recreational runners. "
                      "Predictions: Garmin's race predictor when available, otherwise Riegel's formula "
                      "(T2 = T1·(D2/D1)^1.06) from your best recent run ≥3 km.", evidence="heuristic"),
    ]), {"display": "none"}


@callback(Output("run-body", "children"), Input("data-version", "data"), Input("theme", "data"))
def render(_v, theme):
    b = get_bundle()
    if b.empty:
        return section("Runs", html.Div("No data yet — sync your watch.", className="muted"))
    runs = b.acts[b.acts["sport_group"] == "run"] if not b.acts.empty else pd.DataFrame()
    D = b.daily
    out = []
    age, sex = b.profile.get("age"), b.profile.get("sex")
    med = sp.vo2max_median_for_age(age, sex) if age else None
    v = D["vo2max"].dropna()
    if len(v):
        f = T.fig(theme, 240, showlegend=True)
        f.add_trace(go.Scatter(x=v.index, y=v.values, name="Your VO2max (Garmin estimate)", mode="lines",
                               line=dict(width=2, color=T.series(theme, 0)), hovertemplate="%{y:.1f}<extra>VO2max</extra>"))
        if med:
            f.add_hline(y=med, line=dict(dash="dot", color=T.chrome(theme)["muted"], width=1),
                        annotation_text=f"median for age {age:.0f} ≈ {med:.1f}", annotation_position="bottom right",
                        annotation_font=dict(size=10, color=T.chrome(theme)["muted"]))
        f.update_yaxes(title="mL/kg/min")
        out.append(section("VO2max", graph(f),
                           info_text="Cardiorespiratory fitness is one of the strongest predictors of longevity with no "
                                     "observed upper limit (Mandsager 2018). Reference: FRIEND registry treadmill median, "
                                     "interpolated between the verified 20–29 and 70–79 decade values (approximation)."
                                     + ("" if b.flags.get("sex_set") else " Set your sex in Settings for the right reference."),
                           evidence="strong"))
    preds = sp.riegel_predictions(runs, pd.Timestamp.now()) if not runs.empty else {}
    gp = db.query("SELECT * FROM race_prediction ORDER BY date DESC LIMIT 1")
    rows = []
    for name, key in [("5K", "t5k"), ("10K", "t10k"), ("Half marathon", "thalf"), ("Marathon", "tmarathon")]:
        rows.append(html.Tr([html.Td(name), html.Td(fmt_time(gp[0][key]) if gp and gp[0].get(key) else "–"),
                             html.Td(fmt_time(preds.get(name)))]))
    out.append(section("Race time predictions", html.Table(
        [html.Thead(html.Tr([html.Th(""), html.Th("Garmin"), html.Th("Riegel (best run, 90 d)")])), html.Tbody(rows)],
        className="table-clean"),
        info_text="Garmin's predictor needs the optional Garmin Connect login. Riegel extrapolates your best recent "
                  "run; accuracy drops for distances far from the source run.", evidence="moderate"))
    if not runs.empty:
        wk = sp.weekly_run_km(runs)
        g = T.fig(theme, 220, showlegend=False)
        g.add_trace(go.Bar(x=wk.index, y=wk.values, marker=dict(color=T.series(theme, 0), cornerradius=4),
                           hovertemplate="%{y:.1f} km<extra>week of %{x|%d %b}</extra>"))
        g.update_yaxes(title="km / week")
        effs = []
        for x in runs.itertuples():
            r = sp.ef_decoupling(load_samples(x.activity_id))
            if r:
                effs.append({"date": x.date, "ef": r["ef"], "dec": r["decoupling"]})
        e = pd.DataFrame(effs)
        parts = [section("Weekly running distance", graph(g))]
        if not e.empty:
            h = T.fig(theme, 220, showlegend=False)
            h.add_trace(go.Scatter(x=e["date"], y=e["ef"], mode="lines+markers", line=dict(width=2, color=T.series(theme, 0)),
                                   marker=dict(size=8), customdata=e["dec"],
                                   hovertemplate="EF %{y:.2f} · decoupling %{customdata:+.1f}%<extra></extra>"))
            h.update_yaxes(title="m/min per bpm")
            parts.append(section("Aerobic efficiency (runs ≥ 20 min)", graph(h),
                                 info_text="Rising efficiency factor at similar effort = better aerobic fitness.",
                                 evidence="heuristic"))
        out.append(html.Div(parts, className="grid g2"))
        econ = db.query_df("SELECT date, metric, value FROM running_metric")
        if not econ.empty:
            out.append(section("Running economy & tolerance (Garmin)", html.Div(
                f"{len(econ)} data points from Garmin Connect.", className="muted")))
    else:
        out.append(section("Runs", html.Div("No runs yet.", className="muted")))
    return html.Div(out)


_ = frame
