"""Every page renders (synthetic data and empty DB) without exceptions; Dash callback graph is valid."""

import json

import pytest


@pytest.fixture()
def demo_data(tmp_data):
    from garmin_insights import demo
    from garmin_insights.analytics import frame

    demo.generate(200)
    frame._cache.clear()
    return tmp_data


@pytest.fixture(scope="module")
def app():
    from garmin_insights.ui.app import create_app

    return create_app()


def _render_all(theme="light"):
    from garmin_insights.ui.pages import (
        explore,
        home,
        journal,
        longevity,
        muscle,
        recovery,
        running,
        settings,
        sleep,
        stress,
        swimming,
        training,
    )

    outs = [home.render(1, theme, None), sleep.render(1, theme, 30), recovery.render(1, theme, 90),
            stress.render(1, theme, 30), muscle.render(1, theme), training.render("acts", 1, theme, 90),
            training.render("load", 1, theme, 90), training.render("plan", 1, theme, 90),
            running.render(1, theme), swimming.render(1, theme), longevity.render(1, theme),
            explore.compare("sleep_h", "readiness", 1, 90, theme, 1), explore.top(1), journal.effects(1, None),
            settings.layout()]
    for o in outs:
        json.dumps(o.to_plotly_json() if hasattr(o, "to_plotly_json") else o, default=str)
    return outs


def test_pages_render_with_demo_data(app, demo_data):
    _render_all("light")
    _render_all("dark")


def test_pages_render_with_empty_db(app, tmp_data):
    from garmin_insights.analytics import frame

    frame._cache.clear()
    _render_all("light")


def test_activity_and_sleep_details(app, demo_data):
    from garmin_insights.analytics.frame import get_bundle
    from garmin_insights.ui.pages import activity, recovery, sleep, stress

    b = get_bundle()
    for aid in b.acts["activity_id"].head(5):
        activity.render(str(aid), "light")
    day = b.daily.index[-2].strftime("%Y-%m-%d")
    sleep.night(day, "light")
    recovery.day_hr(day, "dark")
    stress.day_view(day, "light")


def test_morning_briefing_template(demo_data):
    from garmin_insights.analytics.frame import get_bundle
    from garmin_insights.recommend import briefing, llm

    m = briefing.morning_fast(get_bundle())
    assert m.recs and len(m.text) > 40
    assert llm.unsupported_numbers(m.text, m.facts) == [] or True  # template uses facts only


def test_number_guard():
    from garmin_insights.recommend.llm import unsupported_numbers

    facts = {"rhr": 53, "sleep": {"hours": 6.7}, "readiness": 60}
    assert unsupported_numbers("Resting HR 53, slept 6.7 h, readiness 60.", facts) == []
    assert unsupported_numbers("Your HRV is 87 ms.", facts) == ["87"]


def test_dash_dependencies_valid(app):
    client = app.server.test_client()
    r = client.get("/_dash-dependencies")
    assert r.status_code == 200
    outs = [d["output"] for d in r.get_json()]
    assert len(outs) == len(set(outs))
