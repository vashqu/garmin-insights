"""Longevity scorecard: evidence-based targets for the markers that matter most long-term."""

from __future__ import annotations

from datetime import timedelta

import dash
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

from garmin_insights.analytics import sports as sp
from garmin_insights.analytics.frame import get_bundle
from garmin_insights.ui import theme as T
from garmin_insights.ui.components.common import page_header
from garmin_insights.ui.components.widgets import empty_state, graph, info, section, status_badge

dash.register_page(__name__, path="/longevity", title="Longevity")


def layout(**_):
    return html.Div([page_header("Longevity", "The markers with the strongest links to long-term health"),
                     dcc.Loading(html.Div(id="lon-body"), type="circle", delay_show=400)])


def _row(label, value, target, status, why, evidence):
    return html.Tr([html.Td([label, info(why, evidence)]), html.Td(value, className="fw-semibold"),
                    html.Td(target, className="muted"), html.Td(status_badge(status, small=True, label={"green": "Meets target", "amber": "Close",
                                                                        "red": "Below target"}.get(status)))])


def _st(ok, near):
    return "green" if ok else ("amber" if near else "red")


@callback(Output("lon-body", "children"), Input("data-version", "data"), Input("theme", "data"))
def render(_v, theme):
    b = get_bundle()
    if b.empty:
        return empty_state("No data yet")
    D = b.daily
    last = b.latest_date()
    r = D.loc[last]
    age, sex = b.profile.get("age"), b.profile.get("sex")
    rows = []
    vo2 = r.get("vo2max")
    med = sp.vo2max_median_for_age(age, sex)
    if vo2 == vo2 and vo2 and med:
        rows.append(_row("VO2max", f"{vo2:.1f}", f"≥ median for age (≈{med:.0f}); higher is better",
                         _st(vo2 >= med * 1.1, vo2 >= med),
                         "Cardiorespiratory fitness had the strongest inverse association with all-cause mortality, with "
                         "no upper limit (Mandsager 2018). Raise it with zone-2 volume + 1 weekly interval session.",
                         "strong"))
    s7 = r.get("steps_7d")
    lo, hi = sp.steps_band(age)
    if s7 == s7 and s7:
        rows.append(_row("Steps (7-day avg)", f"{s7:,.0f}", f"{lo:,}–{hi:,}/day", _st(s7 >= lo, s7 >= lo * 0.8),
                         "Mortality benefit plateaus at ~8–10k/day under 60 and ~6–8k at 60+ (Paluch 2022).", "strong"))
    im = r.get("im_week")
    rows.append(_row("Intensity minutes (7 days)", f"{im:.0f}", f"{sp.WHO_MVPA[0]}–{sp.WHO_MVPA[1]} min "
                     "(vigorous counts ×2)", _st(im >= 150, im >= 100),
                     "WHO 2020 guidelines: 150–300 min moderate or 75–150 min vigorous activity per week.", "strong"))
    sd = r.get("strength_days_7d")
    rows.append(_row("Strength days (7 days)", f"{sd:.0f}", "≥ 2 days/week", _st(sd >= 2, sd >= 1),
                     "WHO recommends muscle-strengthening activities on ≥2 days per week; muscle strength and mass are "
                     "independently linked to lower mortality.", "strong"))
    sri = r.get("sri30") if r.get("sri30") == r.get("sri30") else r.get("sri7")
    if sri == sri and sri is not None:
        rows.append(_row("Sleep regularity (SRI)", f"{sri:.0f}", "higher is better (≥ ~80 is regular)",
                         _st(sri >= 80, sri >= 70),
                         "In 60,977 UK Biobank adults, the most regular sleepers had 20–48% lower all-cause mortality "
                         "than the least regular; regularity beat duration as a predictor (Windred 2024).", "strong"))
    sl = D["sleep_h"].loc[last - timedelta(days=29):last].median()
    if sl == sl:
        rows.append(_row("Sleep duration (30-day median)", f"{sl:.1f} h", "7–9 h", _st(7 <= sl <= 9, 6.5 <= sl < 10),
                         "Adults: 7–9 h/night (consensus); athletes may need more (Walsh 2021).", "strong"))
    rhr = D["rhr"].loc[last - timedelta(days=89):last]
    slope = sp.slope_per_30d(rhr)
    if slope is not None:
        rows.append(_row("Resting HR trend (90 d)", f"{rhr.tail(14).median():.0f} bpm ({slope:+.1f}/month)",
                         "stable or falling", _st(slope <= 0.3, slope <= 1.0),
                         "Lower resting HR generally reflects better aerobic fitness; a sustained rise can reflect "
                         "fatigue, stress or illness.", "moderate"))
    fa = r.get("fitness_age")
    if fa == fa and fa and age:
        rows.append(_row("Garmin fitness age", f"{fa:.1f}", f"≤ your age ({age:.0f})", _st(fa <= age - 1, fa <= age + 1),
                         "Garmin's estimate from VO2max, resting HR, BMI and vigorous activity.", "heuristic"))
    st7 = r.get("stretch_min_7d")
    rows.append(_row("Stretching (7 days)", f"{st7:.0f} min", "≥ 5–10 min/week", _st(st7 >= 10, st7 >= 5),
                     "≥5–10 min/week of static stretching improves range of motion (2024 dose review). No reliable "
                     "injury-prevention effect.", "moderate"))
    table = html.Table([html.Thead(html.Tr([html.Th("Marker"), html.Th("You"), html.Th("Evidence-based target"),
                                            html.Th("")])), html.Tbody(rows)], className="table-clean")

    W = D.loc[last - timedelta(days=180):last]
    f = T.fig(theme, 230, showlegend=False)
    wk = W["steps"].resample("W-SUN").mean()
    f.add_hrect(y0=lo, y1=hi, fillcolor=T.chrome(theme)["band"], line_width=0, annotation_text="plateau band",
                annotation_position="top left", annotation_font=dict(size=10, color=T.chrome(theme)["muted"]))
    f.add_trace(go.Bar(x=wk.index, y=wk.values, marker=dict(color=T.series(theme, 0), cornerradius=4),
                       hovertemplate="%{y:,.0f} steps/day<extra>week of %{x|%d %b}</extra>"))
    g = T.fig(theme, 230, showlegend=False)
    imw = (W["im_moderate"].fillna(0) + 2 * W["im_vigorous"].fillna(0)).resample("W-SUN").sum()
    g.add_hrect(y0=150, y1=300, fillcolor=T.chrome(theme)["band"], line_width=0, annotation_text="WHO 150–300",
                annotation_position="top left", annotation_font=dict(size=10, color=T.chrome(theme)["muted"]))
    g.add_trace(go.Bar(x=imw.index, y=imw.values, marker=dict(color=T.series(theme, 0), cornerradius=4),
                       hovertemplate="%{y:.0f} min<extra>week of %{x|%d %b}</extra>"))
    return html.Div([
        section("Scorecard", table, info_text="Each target comes from large cohort studies or official guidelines; "
                                              "hover ⓘ for the source. Associations are observational."),
        html.Div([section("Steps per day (weekly average)", graph(f)),
                  section("Intensity minutes per week", graph(g))], className="grid g2"),
        html.Div("Not medical advice.", className="footer-note"),
    ])
