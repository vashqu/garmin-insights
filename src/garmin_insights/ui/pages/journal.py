"""Journal: tag any day, manage tags, and see personal 'what affects you' associations."""

from __future__ import annotations

from datetime import date, timedelta

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Input, Output, State, callback, ctx, dcc, html

from garmin_insights.analytics import frame
from garmin_insights.analytics.correlations import tag_effects
from garmin_insights.analytics.frame import get_bundle
from garmin_insights.storage import db, journal
from garmin_insights.ui import theme as T
from garmin_insights.ui.components.common import page_header
from garmin_insights.ui.components.widgets import graph, section

dash.register_page(__name__, path="/journal", title="Journal")


def layout(**_):
    y = (date.today() - timedelta(days=1)).isoformat()
    return html.Div([
        page_header("Journal", "Lifestyle & diet tags → your personal associations with sleep and recovery"),
        html.Div([
            section("Log a day", [
                dcc.DatePickerSingle(id="jr-date", date=y, max_date_allowed=date.today().isoformat(),
                                     display_format="ddd D MMM YYYY"),
                html.Div(id="jr-tags", className="mt-3"),
                html.Div([dcc.Input(id="jr-new", placeholder="New custom tag (e.g. 'Meditation')",
                                    className="form-control", style={"maxWidth": "300px"}),
                          dcc.Dropdown(id="jr-new-kind", options=[{"label": "yes/no", "value": "bool"},
                                                                  {"label": "count", "value": "count"}],
                                       value="bool", clearable=False, style={"width": "120px"}),
                          html.Button("Add tag", id="jr-add", className="btn-ghost")],
                         className="d-flex gap-2 mt-3"),
            ]),
            section("Last 8 weeks", html.Div(id="jr-cal")),
        ], className="grid g2"),
        section("What affects you", html.Div(id="jr-effects"),
                info_text="For each tag: median next-night/next-day outcome on tagged vs untagged days (among days "
                          "you logged), with a bootstrap 90% interval. Shown as 'clear' only with ≥10 days in each "
                          "group and an interval that excludes zero. These are personal associations, not proof of "
                          "cause.", evidence="moderate"),
    ])


def _tag_buttons(day: str):
    vals = journal.day_values(day)
    groups = {}
    for t in journal.tags():
        groups.setdefault(t["category"], []).append(t)
    out = []
    for cat, ts in groups.items():
        btns = []
        for t in ts:
            v = vals.get(t["tag"], 0) or 0
            lab = t["label"] if t["kind"] == "bool" else (f"{t['label'].split(' (')[0]}: {int(v)}" if v else
                                                           t["label"].split(" (")[0])
            btns.append(html.Button(lab, id={"type": "jr-tag", "tag": t["tag"], "kind": t["kind"]}, n_clicks=0,
                                    className="tag-btn on" if v else "tag-btn"))
        out.append(html.Div([html.Div(cat, className="muted small mb-1"), html.Div(btns, className="tagrow")],
                            className="mb-2"))
    return out


@callback(Output("jr-tags", "children"), Input("jr-date", "date"), Input({"type": "jr-tag", "tag": ALL, "kind": ALL},
                                                                          "n_clicks"),
          Input("jr-add", "n_clicks"), State("jr-new", "value"), State("jr-new-kind", "value"))
def tags(day, _clicks, add, new, kind):
    day = (day or date.today().isoformat())[:10]
    trig = ctx.triggered_id
    if isinstance(trig, dict) and ctx.triggered and ctx.triggered[0].get("value"):
        cur = journal.day_values(day).get(trig["tag"], 0) or 0
        new_v = (cur + 1 if cur < 8 else 0) if trig["kind"] == "count" else (0 if cur else 1)
        journal.set_value(day, trig["tag"], new_v)
        frame.bump_version()
    if trig == "jr-add" and new:
        journal.add_custom(new.strip(), kind or "bool")
    return _tag_buttons(day)


@callback(Output("jr-cal", "children"), Input("jr-tags", "children"), Input("theme", "data"))
def calendar(_c, theme):
    j = db.query_df("SELECT date, COUNT(*) n FROM journal GROUP BY date")
    end = pd.Timestamp(date.today())
    days = pd.date_range(end - timedelta(days=55), end, freq="D")
    n = j.set_index(pd.to_datetime(j["date"]))["n"].reindex(days).fillna(0) if not j.empty else pd.Series(0, index=days)
    weeks = [(d - days[0]).days // 7 for d in days]
    dows = [d.weekday() for d in days]
    f = T.fig(theme, 200, hovermode="closest", margin=dict(l=40, r=10, t=10, b=20))
    f.add_trace(go.Heatmap(x=weeks, y=dows, z=n.values, xgap=3, ygap=3, showscale=False, zmin=0, zmax=4,
                           colorscale=[[0, T.chrome(theme)["neutral"]], [1, T.SERIES[T.mode(theme)][0]]],
                           customdata=[d.strftime("%a %d %b") for d in days],
                           hovertemplate="%{customdata}: %{z} tags<extra></extra>"))
    f.update_yaxes(tickvals=list(range(7)), ticktext=["M", "T", "W", "T", "F", "S", "S"], autorange="reversed",
                   showgrid=False)
    f.update_xaxes(visible=False)
    logged = int((n > 0).sum())
    return html.Div([graph(f), html.Div(f"{logged} of the last 56 days logged. Associations need ~10+ tagged and "
                                        f"10+ untagged logged days.", className="muted small")])


@callback(Output("jr-effects", "children"), Input("data-version", "data"), Input("jr-tags", "children"))
def effects(_v, _c):
    b = get_bundle()
    t = tag_effects(b.daily, b.journal)
    if t.empty:
        return html.Div("Log a few weeks of tags to see what affects your sleep and recovery.", className="muted")
    labels = {x["tag"]: x["label"] for x in journal.tags(active_only=False)}
    t = t.sort_values(["significant", "n_on"], ascending=[False, False])
    rows = []
    for x in t.itertuples():
        sig = "clear" if x.significant else ("needs more data" if min(x.n_on, x.n_off) < 10 else "no clear effect")
        rows.append(html.Tr([html.Td(labels.get(x.tag, x.tag)), html.Td(x.outcome_label),
                             html.Td(f"{x.median_on:.1f} vs {x.median_off:.1f}"),
                             html.Td(f"{x.diff:+.1f} [{x.ci_lo:+.1f}, {x.ci_hi:+.1f}]"),
                             html.Td(f"{x.n_on}/{x.n_off}"), html.Td(sig, className="fw-semibold" if x.significant else "muted")]))
    return html.Table([html.Thead(html.Tr([html.Th(h) for h in ("Tag", "Outcome", "Tagged vs not (median)",
                                                                 "Difference [90% CI]", "n days", "")])),
                       html.Tbody(rows)], className="table-clean")
