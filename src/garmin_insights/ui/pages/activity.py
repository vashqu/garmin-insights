"""Activity detail: synced time-series, HR zones, sport-specific panels (run efficiency, Strong sets, swim lengths)."""

from __future__ import annotations

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html
from plotly.subplots import make_subplots

from garmin_insights.analytics import sports as sp
from garmin_insights.analytics.frame import get_bundle
from garmin_insights.storage import db
from garmin_insights.sync.fit import load_samples
from garmin_insights.ui import theme as T
from garmin_insights.ui.components.common import fmt_min, kv
from garmin_insights.ui.components.widgets import empty_state, graph, section

dash.register_page(__name__, path_template="/activity/<activity_id>", title="Activity")


def layout(activity_id=None, **_):
    return html.Div([dcc.Store(id="act-id", data=activity_id), dcc.Loading(html.Div(id="act-body"), type="circle")])


def _pace(speed_ms):
    if not speed_ms or speed_ms <= 0:
        return None
    s = 1000 / speed_ms
    return f"{int(s // 60)}:{int(s % 60):02d} /km"


@callback(Output("act-body", "children"), Input("act-id", "data"), Input("theme", "data"))
def render(aid, theme):
    b = get_bundle()
    try:
        aid = int(aid)
    except (TypeError, ValueError):
        return empty_state("Unknown activity")
    a = b.acts[b.acts["activity_id"] == aid]
    if a.empty:
        return empty_state("Activity not found")
    a = a.iloc[0]
    sg = a["sport_group"]
    s = load_samples(aid)
    extras = (db.get_state("activity_extras", {}) or {}).get(str(aid), {})
    c = T.chrome(theme)
    facts = [("Start", pd.Timestamp(a["start_local"]).strftime("%a %d %b %Y, %H:%M")),
             ("Duration", fmt_min((a["duration_s"] or 0) / 60))]
    if (a["distance_m"] or 0) > 100:
        facts.append(("Distance", f"{a['distance_m'] / 1000:.2f} km"))
        if sg == "run":
            facts.append(("Avg pace", _pace(a["avg_speed"])))
    if a["avg_hr"] == a["avg_hr"] and a["avg_hr"]:
        facts.append(("Heart rate", f"avg {a['avg_hr']:.0f} · max {a['max_hr']:.0f} bpm"))
    facts.append(("Training load", f"TRIMP {a['trimp']:.0f}" + (f" · Garmin load {a['garmin_load']:.0f}"
                                                                  if a["garmin_load"] == a["garmin_load"] and a["garmin_load"] else "")))
    if a["aerobic_te"] == a["aerobic_te"] and a["aerobic_te"]:
        facts.append(("Training effect", f"aerobic {a['aerobic_te']:.1f} · anaerobic {a['anaerobic_te']:.1f}"))
    if extras.get("startStress") is not None and extras.get("endStress") is not None:
        facts.append(("Stress before → after", f"{extras['startStress']:.0f} → {extras['endStress']:.0f}"))
    parts = [html.H2(a["type_key"].replace("_", " ").title(), style={"fontSize": "20px"}),
             html.Div(a["name"] or "", className="muted mb-3")]
    side = [section("Summary", kv(facts))]

    # time series
    series = [("hr", "Heart rate", "bpm", 7)]
    if sg == "run":
        series += [("pace", "Pace", "min/km", 0), ("cadence", "Cadence", "spm", 2), ("alt", "Elevation", "m", 4)]
    elif sg in ("walk", "bike"):
        series += [("speed_kmh", "Speed", "km/h", 0), ("alt", "Elevation", "m", 4)]
    if not s.empty:
        if "speed" in s:
            s["pace"] = (1000 / s["speed"].where(s["speed"] > 0.8)) / 60
            s["speed_kmh"] = s["speed"] * 3.6
        if "pace" in s:
            s["pace"] = s["pace"].rolling(10, min_periods=1, center=True).median()
        avail = [x for x in series if x[0] in s and s[x[0]].notna().sum() > 10]
        if avail:
            f = make_subplots(rows=len(avail), cols=1, shared_xaxes=True, vertical_spacing=0.04)
            f.update_layout(**T.base_layout(theme, 150 * len(avail) + 40, showlegend=False))
            if "ts" in s:
                try:  # FIT timestamps are UTC → shift to local wall clock using the activity's offset
                    off = pd.Timestamp(a["start_local"]) - pd.Timestamp(str(a["start_gmt"]).rstrip("Z"))
                except (TypeError, ValueError):
                    off = pd.Timedelta(0)
                x = s["ts"] + off
            else:
                x = s["t"]
            for i, (col, lab, unit, slot) in enumerate(avail, start=1):
                f.add_trace(go.Scatter(x=x, y=s[col], mode="lines", line=dict(width=1.6, color=T.series(theme, slot)),
                                       name=lab, hovertemplate=f"%{{y:.1f}} {unit}<extra>{lab}</extra>"), i, 1)
                f.update_yaxes(title_text=f"{lab} ({unit})", row=i, col=1, gridcolor=c["grid"],
                               title_font=dict(size=11),
                               autorange="reversed" if col == "pace" else True)
            f.update_xaxes(showspikes=True, spikemode="across", spikethickness=1, spikecolor=c["axis"])
            f.update_traces(xaxis=f"x{len(avail)}")
            parts.append(section("Over time", graph(f)))
    else:
        parts.append(section("Over time", html.Div("No detailed samples for this activity.", className="muted")))

    zones = [a.get(f"hr_z{i}") or 0 for i in range(1, 6)]
    if sum(zones) > 0:
        z = T.fig(theme, 200, hovermode="closest", showlegend=False, margin=dict(l=70, r=16, t=8, b=30))
        ramp = [T.SEQ_BLUE[i] for i in (3, 5, 7, 9, 11)]
        z.add_trace(go.Bar(y=[f"Zone {i}" for i in range(1, 6)], x=[v / 60 for v in zones], orientation="h",
                           marker=dict(color=ramp, cornerradius=4), hovertemplate="%{y}: %{x:.0f} min<extra></extra>"))
        z.update_xaxes(title="minutes")
        z.update_yaxes(showgrid=False)
        side.append(section("Time in HR zones", graph(z)))

    if sg == "run" and not s.empty:
        r = sp.ef_decoupling(s)
        if r:
            side.append(section("Aerobic efficiency", kv([
                ("Efficiency factor", f"{r['ef']:.2f} m/min per bpm"),
                ("Decoupling (Pa:HR)", f"{r['decoupling']:+.1f}%"),
                ("Pace steadiness", "steady" if r["steady"] else f"variable (CV {r['pace_cv']:.0f}%)"),
            ]), info_text="Efficiency factor = speed ÷ heart rate; decoupling compares it between the first and second "
                          "half. <5% on a steady aerobic run is a coaching rule of thumb for good aerobic endurance "
                          "(heuristic; sensitive to heat, hills and pacing).", evidence="heuristic"))
    if sg == "strength":
        st = b.sets[b.sets["matched_activity_id"] == aid] if not b.sets.empty else pd.DataFrame()
        if len(st):
            rows = [html.Tr([html.Td(x.exercise), html.Td(x.set_idx), html.Td(x.set_type),
                             html.Td(f"{x.weight_kg:g} kg" if x.weight_kg else "BW"), html.Td(f"{x.reps:.0f}" if x.reps == x.reps else "")])
                    for x in st.itertuples()]
            parts.append(section(f"Strong log · {st['workout_name'].iloc[0].strip()}",
                                 html.Table([html.Thead(html.Tr([html.Th(h) for h in ("Exercise", "Set", "Type", "Weight", "Reps")])),
                                             html.Tbody(rows)], className="table-clean")))
        else:
            parts.append(section("Strong log", html.Div("No Strong workout matched this session (±30 min).",
                                                        className="muted")))
    if sg == "swim":
        ln = db.query_df("SELECT * FROM swim_length WHERE activity_id=? ORDER BY idx", (aid,))
        if not ln.empty:
            g = T.fig(theme, 220, hovermode="closest", showlegend=False)
            g.add_trace(go.Bar(x=list(range(1, len(ln) + 1)), y=ln["swolf"], marker=dict(color=T.series(theme, 0), cornerradius=3),
                               customdata=ln[["stroke", "time_s", "strokes"]].values,
                               hovertemplate="Length %{x}: SWOLF %{y:.0f}<br>%{customdata[0]} · %{customdata[1]:.0f}s · "
                                             "%{customdata[2]:.0f} strokes<extra></extra>"))
            g.update_yaxes(title="SWOLF")
            parts.append(section("SWOLF per length", graph(g),
                                 info_text="SWOLF = seconds + strokes per length. Only comparable within the same pool "
                                           "length and stroke.", evidence="moderate"))
    return html.Div([dcc.Link("← Training", href="/training", className="small"),
                     html.Div([html.Div(parts), html.Div(side)], className="hero")])
