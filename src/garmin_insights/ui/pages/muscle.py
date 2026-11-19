"""Muscle: weekly hard sets per muscle (fractional), freshness, e1RM progression, exercise mapping, Strong import."""

from __future__ import annotations

import base64
from datetime import timedelta

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html

from garmin_insights import config
from garmin_insights.analytics import frame
from garmin_insights.analytics import muscle as mu
from garmin_insights.analytics.frame import get_bundle
from garmin_insights.sources import strong
from garmin_insights.storage import db
from garmin_insights.ui import theme as T
from garmin_insights.ui.components.common import page_header
from garmin_insights.ui.components.widgets import empty_state, graph, section, status_badge

dash.register_page(__name__, path="/muscle", title="Muscle")

SETS_WHY = ("Weekly hard sets per muscle, counted fractionally: a set counts 1 for the primary mover and 0.5 for "
            "secondary muscles (Pelland et al. meta-regression). Hypertrophy shows a graded dose-response to weekly "
            "sets (Schoenfeld 2017); 10–20 sets/muscle/week is a common evidence-informed range — more is not "
            "always better if recovery suffers. Warm-ups excluded; if you log RPE in Strong, only RPE ≥ 7 counts.")


def layout(**_):
    return html.Div([page_header("Muscle", "From your Strong log · fractional hard sets, freshness and strength"),
                     dcc.Loading(html.Div(id="mus-body"), type="circle", delay_show=400),
                     section("Import Strong export", [
                         html.Div(id="strong-status", className="muted small mb-2"),
                         dcc.Upload(id="strong-upload", children=html.Div(["Drop a Strong CSV here or ",
                                                                           html.A("choose a file")]),
                                    className="section text-center", style={"borderStyle": "dashed"}),
                     ])])


def _muscle_order(ms: pd.DataFrame) -> list[str]:
    order = list(mu.MUSCLE_LABELS)
    return [m for m in order if m in set(ms["muscle"])]


@callback(Output("mus-body", "children"), Input("data-version", "data"), Input("theme", "data"))
def render(_v, theme):
    b = get_bundle()
    if b.sets.empty:
        return empty_state("No Strong data yet",
                           f"Export from Strong (Settings → Export data → CSV) into {config.Settings.load().strong_folder} "
                           "or drop the file below.")
    ms = b.msets
    today = pd.Timestamp.now().normalize()
    r7 = mu.rolling7_sets(ms, today)
    muscles = _muscle_order(ms)
    c = T.chrome(theme)

    # this week bars with 10–20 band
    vals = [float(r7.get(m, 0)) for m in muscles]
    bar = T.fig(theme, 30 * len(muscles) + 70, hovermode="closest", showlegend=False,
                margin=dict(l=100, r=24, t=10, b=30))
    bar.add_vrect(x0=mu.SETS_LO, x1=mu.SETS_HI, fillcolor=c["band"], line_width=0,
                  annotation_text="10–20 sets", annotation_position="top left",
                  annotation_font=dict(size=10, color=c["muted"]))
    bar.add_trace(go.Bar(y=[mu.MUSCLE_LABELS[m] for m in muscles][::-1], x=vals[::-1], orientation="h",
                         marker=dict(color=T.series(theme, 0), cornerradius=4),
                         hovertemplate="%{y}: %{x:.1f} hard sets (last 7 days)<extra></extra>"))
    bar.update_xaxes(title="hard sets, last 7 days")
    bar.update_yaxes(showgrid=False)

    # weekly heatmap (sequential)
    wk = mu.weekly_sets(ms)
    wk = wk[[m for m in muscles if m in wk]].tail(16)
    heat = T.fig(theme, 30 * len(muscles) + 80, hovermode="closest", margin=dict(l=100, r=24, t=10, b=40))
    heat.add_trace(go.Heatmap(z=wk.T.values, x=[d.strftime("%d %b") for d in wk.index],
                              y=[mu.MUSCLE_LABELS[m] for m in wk.columns], zmin=0, zmax=20,
                              colorscale=[[i / (len(T.SEQ_BLUE) - 1), col] for i, col in enumerate(T.SEQ_BLUE)],
                              xgap=2, ygap=2, colorbar=dict(title="sets", thickness=10),
                              hovertemplate="%{y} · week of %{x}: %{z:.1f} sets<extra></extra>"))
    heat.update_yaxes(autorange="reversed", showgrid=False)

    # freshness table
    fr = mu.freshness(ms, b.sets)
    fr_rows = [html.Tr([html.Td(mu.MUSCLE_LABELS.get(x.muscle, x.muscle)),
                        html.Td(f"{x.hours / 24:.1f} d" if x.hours >= 24 else f"{x.hours:.0f} h"),
                        html.Td(status_badge("green" if x.fresh else "amber", small=True,
                                             label="Ready" if x.fresh else "Recovering"))])
               for x in fr.sort_values("hours", ascending=False).itertuples()]
    fresh_tbl = html.Table([html.Thead(html.Tr([html.Th("Muscle"), html.Th("Since last hard set"), html.Th("")])),
                            html.Tbody(fr_rows)], className="table-clean")

    # exercise selector
    ex_counts = b.sets.groupby("exercise")["reps"].size().sort_values(ascending=False)
    unm = strong.unmapped_exercises()
    weeks = b.sets.assign(d=pd.to_datetime(b.sets["date"]))
    n_workouts = weeks["workout_start"].nunique()
    span_w = max(1, (weeks["d"].max() - weeks["d"].min()).days / 7)
    summary = html.Div([
        html.Span(f"{n_workouts} workouts", className="pill me-2"),
        html.Span(f"{n_workouts / span_w:.1f} sessions/week", className="pill me-2"),
        html.Span(f"since {weeks['d'].min():%d %b %Y}", className="pill me-2"),
        html.Span(f"{b.sets['matched_activity_id'].notna().groupby(b.sets['workout_start']).max().sum()} "
                  f"matched to watch sessions", className="pill"),
    ], className="mb-3")
    return html.Div([
        summary,
        html.Div([section("This week (rolling 7 days)", graph(bar), info_text=SETS_WHY, evidence="strong"),
                  section("Recovery status per muscle", fresh_tbl,
                          info_text="Hours since each muscle was last trained as a primary mover. ~48 h is a common "
                                    "minimum between hard sessions for the same muscle.", evidence="moderate")],
                 className="grid g2"),
        section("Weekly hard sets per muscle (16 weeks)", graph(heat), info_text=SETS_WHY, evidence="strong"),
        section("Exercise progression", [
            dcc.Dropdown(id="mus-ex", options=[{"label": f"{e} ({n} sets)", "value": e} for e, n in ex_counts.items()],
                         value=ex_counts.index[0], clearable=False, style={"maxWidth": "380px"}),
            html.Div(id="mus-ex-body")],
                info_text="Estimated 1RM uses the Epley formula (weight × (1 + reps/30)) on sets of 1–10 reps. "
                          "It's an estimate — useful for trends, not a max test.", evidence="moderate"),
        section("Exercise → muscle mapping", [
            html.Div(f"{len(unm)} exercise(s) need mapping: {', '.join(unm)}" if unm else
                     "All logged exercises are mapped. You can adjust any mapping below.", className="muted small mb-2"),
            html.Div([
                dcc.Dropdown(id="map-ex", options=sorted(set(b.sets["exercise"])), value=(unm or [None])[0],
                             placeholder="Exercise", style={"minWidth": "260px"}),
                dcc.Dropdown(id="map-primary", options=[{"label": v, "value": k} for k, v in mu.MUSCLE_LABELS.items()],
                             multi=True, placeholder="Primary muscles", style={"minWidth": "260px"}),
                dcc.Dropdown(id="map-secondary", options=[{"label": v, "value": k} for k, v in mu.MUSCLE_LABELS.items()],
                             multi=True, placeholder="Secondary muscles", style={"minWidth": "260px"}),
                html.Button("Save", id="map-save", className="btn-primary-s"),
            ], className="d-flex gap-2 flex-wrap"),
            html.Div(id="map-msg", className="small mt-2")]),
    ])


@callback(Output("mus-ex-body", "children"), Input("mus-ex", "value"), Input("theme", "data"))
def exercise(ex, theme):
    if not ex:
        return ""
    b = get_bundle()
    e1 = mu.e1rm_history(b.sets)
    e1 = e1[e1["exercise"] == ex]
    vol = mu.volume_by_exercise(b.sets)
    vol = vol[vol["exercise"] == ex] if not vol.empty else vol
    out = []
    if not e1.empty:
        f = T.fig(theme, 240, showlegend=False)
        f.add_trace(go.Scatter(x=e1["date"], y=e1["e1rm"], mode="lines+markers", line=dict(width=2, color=T.series(theme, 0)),
                               marker=dict(size=7), customdata=e1[["weight_kg", "reps"]].values,
                               hovertemplate="e1RM %{y:.1f} kg<br>best set %{customdata[0]:.1f} kg × %{customdata[1]:.0f}"
                                             "<extra></extra>"))
        prs = e1[e1["is_pr"]]
        if len(prs):
            f.add_trace(go.Scatter(x=prs["date"], y=prs["e1rm"], mode="markers", name="PR",
                                   marker=dict(size=11, symbol="star", color=T.series(theme, 3),
                                               line=dict(width=1, color=T.chrome(theme)["surface"])),
                                   hovertemplate="PR %{y:.1f} kg<extra></extra>"))
        f.update_yaxes(title="estimated 1RM (kg)")
        out.append(graph(f))
    if not vol.empty:
        g = T.fig(theme, 200, showlegend=False)
        y = vol["volume"] if vol["volume"].sum() > 0 else vol["reps"]
        g.add_trace(go.Bar(x=vol["date"], y=y, marker=dict(color=T.series(theme, 0), cornerradius=4),
                           customdata=vol[["sets", "reps"]].values,
                           hovertemplate=("%{y:,.0f} kg volume" if vol["volume"].sum() > 0 else "%{y} reps")
                                         + "<br>%{customdata[0]} sets · %{customdata[1]:.0f} reps<extra></extra>"))
        g.update_yaxes(title="volume (kg·reps)" if vol["volume"].sum() > 0 else "reps")
        out.append(graph(g))
    m = db.query("SELECT * FROM exercise_map WHERE exercise=?", (ex,))
    if m:
        out.append(html.Div(f"Counts toward: {m[0]['primary_muscles'].replace(',', ', ')}"
                            + (f" (+½ {m[0]['secondary_muscles'].replace(',', ', ')})" if m[0]['secondary_muscles'] else ""),
                            className="muted small"))
    return html.Div(out) if out else html.Div("Bodyweight-only — no load data to chart.", className="muted")


@callback(Output("map-primary", "value"), Output("map-secondary", "value"), Input("map-ex", "value"))
def load_map(ex):
    if not ex:
        return [], []
    m = db.query("SELECT * FROM exercise_map WHERE exercise=?", (ex,))
    if not m:
        return [], []
    return [x for x in (m[0]["primary_muscles"] or "").split(",") if x], \
        [x for x in (m[0]["secondary_muscles"] or "").split(",") if x]


@callback(Output("map-msg", "children"), Input("map-save", "n_clicks"), State("map-ex", "value"),
          State("map-primary", "value"), State("map-secondary", "value"), prevent_initial_call=True)
def save_map(_n, ex, prim, sec):
    if not ex or not prim:
        return "Pick an exercise and at least one primary muscle."
    db.upsert("exercise_map", [{"exercise": ex, "primary_muscles": ",".join(prim), "secondary_muscles": ",".join(sec or []),
                                "user_defined": 1}])
    frame.bump_version()
    return f"Saved mapping for {ex}. Charts update on next refresh."


@callback(Output("strong-status", "children"), Input("strong-upload", "contents"), State("strong-upload", "filename"))
def upload(contents, filename):
    s = config.Settings.load()
    base = f"Watch folder: {s.strong_folder} — new exports there are imported on every launch/sync."
    if not contents:
        last = db.query("SELECT MAX(workout_start) m, COUNT(*) n FROM strength_set")[0]
        return f"{base} · {last['n']} sets imported, latest workout {str(last['m'])[:10]}."
    _, data = contents.split(",", 1)
    text = base64.b64decode(data).decode("utf-8", errors="replace")
    try:
        res = strong.import_text(text, s.strong_weight_unit)
    except Exception as e:  # noqa: BLE001
        return f"Import failed for {filename}: {e}"
    frame.bump_version()
    return f"Imported {filename}: {res['sets']} sets from {res['workouts']} workouts " \
           f"({res['matched_workouts']} matched to watch sessions). Refresh the page to see them."


_ = timedelta
