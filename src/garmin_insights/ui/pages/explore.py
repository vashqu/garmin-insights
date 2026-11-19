"""Explore: compare any two metrics (stacked, shared time axis — never dual-axis), scatter + Spearman, and chat."""

from __future__ import annotations

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html
from plotly.subplots import make_subplots

from garmin_insights import config
from garmin_insights.analytics.correlations import lagged_spearman, top_associations
from garmin_insights.analytics.frame import get_bundle
from garmin_insights.recommend import llm, tools
from garmin_insights.ui import theme as T
from garmin_insights.ui.components.common import page_header, range_selector, window
from garmin_insights.ui.components.widgets import graph, section

dash.register_page(__name__, path="/explore", title="Explore & ask")

OPTS = [{"label": f"{v} ({k})", "value": k} for k, v in tools.METRICS.items()] + \
    [{"label": "minutes of strength", "value": "min_strength"}, {"label": "minutes of running", "value": "min_run"},
     {"label": "minutes of stretching", "value": "min_stretch"}, {"label": "last workout end hour", "value": "last_workout_end_h"}]
EXAMPLES = ["How did my resting HR change over the last 4 weeks?", "Which muscles did I train least this month?",
            "Compare my sleep in October vs November.", "What was my best week for steps?"]


def layout(**_):
    return html.Div([
        page_header("Explore & ask", "Compare metrics and ask questions about your data"),
        section("Compare two metrics", [
            html.Div([
                dcc.Dropdown(id="ex-a", options=OPTS, value="sleep_h", clearable=False, style={"minWidth": "260px"}),
                dcc.Dropdown(id="ex-b", options=OPTS, value="readiness", clearable=False, style={"minWidth": "260px"}),
                html.Div([html.Span("B shifted by ", className="small muted me-2"),
                          dcc.Slider(id="ex-lag", min=0, max=2, step=1, value=0, marks={0: "same day", 1: "+1 day",
                                                                                         2: "+2 days"})],
                         style={"minWidth": "260px"}),
                range_selector("ex-range", 90),
            ], className="d-flex gap-3 flex-wrap align-items-center"),
            html.Div(id="ex-body", className="mt-2")]),
        section("Ask your data", [
            html.Div([html.Button(e, id={"type": "ex-q", "i": i}, className="tag-btn") for i, e in enumerate(EXAMPLES)],
                     className="tagrow mb-2"),
            html.Div(id="chat-log", className="chat-box"),
            dcc.Store(id="chat-hist", data=[]),
            html.Div([dcc.Input(id="chat-in", placeholder="Ask anything about your sleep, training, muscles…",
                                className="form-control", debounce=True, n_submit=0),
                      html.Button("Ask", id="chat-send", className="btn-primary-s")], className="d-flex gap-2 mt-2"),
            dcc.Loading(html.Div(id="chat-status", className="muted small mt-1"), type="dot"),
        ], info_text="Runs Qwen3 4B locally via Ollama. It fetches numbers with read-only tools; numbers in the answer "
                     "that don't appear in the fetched data are flagged."),
        section("Strongest patterns in your data", html.Div(id="ex-top"),
                info_text="Lagged Spearman correlations between behaviours and next-day outcomes with |ρ| ≥ 0.3, "
                          "n ≥ 20, p < 0.05. Correlation, not causation."),
    ])


@callback(Output("ex-body", "children"), Input("ex-a", "value"), Input("ex-b", "value"), Input("ex-lag", "value"),
          Input("ex-range", "value"), Input("theme", "data"), Input("data-version", "data"))
def compare(a, bm, lag, days, theme, _v):
    b = get_bundle()
    D = window(b.daily, days)
    if a not in D or bm not in D:
        return ""
    la = dict(o for o in [(x["value"], x["label"]) for x in OPTS])
    f = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08)
    f.update_layout(**T.base_layout(theme, 360, showlegend=False))
    for i, (m, slot) in enumerate([(a, 0), (bm, 1)], start=1):
        f.add_trace(go.Scatter(x=D.index, y=D[m], mode="lines+markers", line=dict(width=2, color=T.series(theme, slot)),
                               marker=dict(size=4), connectgaps=True, name=la[m],
                               hovertemplate=f"%{{y:.1f}}<extra>{la[m].split(' (')[0]}</extra>"), i, 1)
        f.update_yaxes(title_text=la[m].split(" (")[0], row=i, col=1, gridcolor=T.chrome(theme)["grid"],
                       title_font=dict(size=11))
    x, y = D[a], D[bm].shift(-lag)
    m = pd.concat([x, y], axis=1).dropna()
    s = T.fig(theme, 300, hovermode="closest", showlegend=False)
    stats = lagged_spearman(D[a], D[bm], lag)
    if len(m) >= 3:
        s.add_trace(go.Scatter(x=m.iloc[:, 0], y=m.iloc[:, 1], mode="markers",
                               marker=dict(size=9, color=T.series(theme, 0), line=dict(width=2, color=T.chrome(theme)["surface"])),
                               customdata=[d.strftime("%d %b") for d in m.index],
                               hovertemplate="%{customdata}: %{x:.1f} → %{y:.1f}<extra></extra>"))
        if len(m) >= 5 and m.iloc[:, 0].nunique() > 1:
            k, c0 = np.polyfit(m.iloc[:, 0], m.iloc[:, 1], 1)
            xs = np.linspace(m.iloc[:, 0].min(), m.iloc[:, 0].max(), 20)
            s.add_trace(go.Scatter(x=xs, y=k * xs + c0, mode="lines", line=dict(width=2, color=T.chrome(theme)["muted"],
                                                                                 dash="dot"), hoverinfo="skip"))
    s.update_xaxes(title=la[a].split(" (")[0])
    s.update_yaxes(title=la[bm].split(" (")[0] + (f" (+{lag} d)" if lag else ""))
    rho = stats["rho"]
    txt = (f"Spearman ρ = {rho:+.2f} (n = {stats['n']}, p = {stats['p']:.3f})" if rho is not None
           else f"Not enough paired days (n = {stats['n']}).")
    strength = "" if rho is None else (" — weak" if abs(rho) < 0.3 else (" — moderate" if abs(rho) < 0.5 else " — strong"))
    return html.Div([html.Div([graph(f), html.Div([graph(s), html.Div(txt + strength, className="small muted")])],
                              className="grid g2")])


@callback(Output("ex-top", "children"), Input("data-version", "data"))
def top(_v):
    b = get_bundle()
    t = top_associations(b.daily)
    if t.empty:
        return html.Div("No strong patterns yet — they appear as history grows.", className="muted")
    return html.Table([html.Thead(html.Tr([html.Th(h) for h in ("Behaviour", "Outcome", "ρ", "n")])),
                       html.Tbody([html.Tr([html.Td(x.input), html.Td(x.outcome), html.Td(f"{x.rho:+.2f}"), html.Td(x.n)])
                                   for x in t.itertuples()])], className="table-clean")


@callback(Output("chat-in", "value"), Input({"type": "ex-q", "i": dash.ALL}, "n_clicks"), prevent_initial_call=True)
def example(_n):
    t = dash.ctx.triggered_id
    if not t or not any(_n):
        return dash.no_update
    return EXAMPLES[t["i"]]


@callback(Output("chat-log", "children"), Output("chat-hist", "data"), Output("chat-status", "children"),
          Input("chat-send", "n_clicks"), Input("chat-in", "n_submit"), State("chat-in", "value"),
          State("chat-hist", "data"), prevent_initial_call=True)
def ask(_n, _s, q, hist):
    hist = hist or []
    if not q or not q.strip():
        return dash.no_update, dash.no_update, ""
    s = config.Settings.load()
    res = llm.chat(q.strip(), [{"role": h["role"], "content": h["content"]} for h in hist], s.llm_chat_model, tools)
    hist = hist + [{"role": "user", "content": q.strip()},
                   {"role": "assistant", "content": res["answer"], "tools": res.get("tool_log", []),
                    "status": res["status"], "unverified": res.get("unverified", [])}]
    log = []
    for h in hist:
        if h["role"] == "user":
            log.append(html.Div(h["content"], className="msg user"))
        else:
            extra = []
            if h.get("tools"):
                extra.append(html.Details([html.Summary(f"data used ({len(h['tools'])} lookups)"),
                                           html.Ul([html.Li(f"{t['tool']}({', '.join(f'{k}={v}' for k, v in (t['args'] or {}).items())})")
                                                    for t in h["tools"]], className="small")], className="small mt-1"))
            if h.get("unverified"):
                extra.append(html.Div(f"⚠ couldn't verify: {', '.join(h['unverified'][:6])}", className="small mt-1"))
            log.append(html.Div([h["content"], *extra], className="msg bot"))
    return log, hist[-12:], f"Answered by {s.llm_chat_model} (local)"
