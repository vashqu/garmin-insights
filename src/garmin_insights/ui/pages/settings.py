"""Settings: data sources, Strong import, local AI, profile, sync controls."""

from __future__ import annotations

import dash
from dash import Input, Output, State, callback, dcc, html

from garmin_insights import config
from garmin_insights.analytics import derive, frame
from garmin_insights.recommend import llm
from garmin_insights.sources.official import SECRET_NAME, OfficialClient
from garmin_insights.sources.unofficial import UnofficialClient
from garmin_insights.storage import db
from garmin_insights.sync import engine
from garmin_insights.ui.components.common import kv, page_header
from garmin_insights.ui.components.widgets import section

dash.register_page(__name__, path="/settings", title="Settings")


def _secret_source() -> str:
    try:
        import keyring

        if keyring.get_password(config.KEYRING_SERVICE, SECRET_NAME):
            return "macOS Keychain"
    except Exception:  # noqa: BLE001
        pass
    return ".env file" if config.get_secret(SECRET_NAME) else "not set"


def _api_usage() -> str:
    from datetime import datetime, timedelta

    log = db.get_state("api_call_log", []) or []
    if not log:
        return "–"
    day = [e for e in log if datetime.fromisoformat(e["at"]) > datetime.now() - timedelta(hours=24)]
    return (f"last sync {log[-1]['total']} · last 24 h {sum(e['total'] for e in day)} over {len(day)} syncs "
            "(auto-sync on launch at most every 2 h)")


def layout(**_):
    s = config.Settings.load()
    p = db.get_profile()
    st = llm.ollama_status()
    u = UnofficialClient()
    used = db.get_state("sources_used", {}) or {}
    return html.Div([
        page_header("Settings"),
        html.Div([
            section("Garmin Personal API (official)", [
                kv([("API key", _secret_source()), ("Last sync", db.get_state("last_sync_at") or "never"),
                    ("History starts", db.get_state("history_start") or "–"),
                    ("Sources used", ", ".join(f"{k}: {v}" for k, v in used.items()) or "–"),
                    ("API requests", _api_usage())]),
                html.Div([dcc.Input(id="set-key", type="password", placeholder="Paste a new Personal API key",
                                    className="form-control"),
                          html.Button("Save to Keychain & test", id="set-key-save", className="btn-ghost")],
                         className="d-flex gap-2 mt-3"),
                html.Div(id="set-key-msg", className="small mt-1"),
                html.Div("Create keys in Garmin Connect → Personal API Key page. Keys are stored in the macOS Keychain "
                         "and never leave this Mac except to Garmin.", className="muted small mt-2"),
            ]),
            section("Garmin Connect login (optional fallback)", [
                html.Div("Adds data the official API doesn't provide: race predictions, personal records, stress "
                         "timeline, Body Battery events, running economy/tolerance. Uses the community "
                         "garminconnect library — unofficial, may stop working if Garmin blocks it.",
                         className="muted small mb-2"),
                kv([("Status", "logged in (tokens cached)" if u.has_tokens() else "not logged in")]),
                html.Div([dcc.Input(id="gc-email", value=s.garmin_email, placeholder="Email", className="form-control"),
                          dcc.Input(id="gc-pw", type="password", placeholder="Password", className="form-control"),
                          html.Button("Log in", id="gc-login", className="btn-ghost")], className="d-flex gap-2 mt-2"),
                html.Div([dcc.Input(id="gc-mfa", placeholder="MFA code (if asked)", className="form-control",
                                    style={"maxWidth": "200px"}),
                          html.Button("Submit code", id="gc-mfa-btn", className="btn-ghost")],
                         className="d-flex gap-2 mt-2"),
                dcc.Store(id="gc-state"), html.Div(id="gc-msg", className="small mt-1"),
            ]),
        ], className="grid g2"),
        html.Div([
            section("Local AI (Ollama)", [
                kv([("Ollama", "running" if st["running"] else "not running"),
                    ("Installed models", ", ".join(st["models"]) or "–")]),
                html.Div([html.Span("Briefing model", className="small muted"),
                          dcc.Input(id="set-brief", value=s.llm_briefing_model, className="form-control")], className="mt-2"),
                html.Div([html.Span("Chat model", className="small muted"),
                          dcc.Input(id="set-chat", value=s.llm_chat_model, className="form-control")], className="mt-2"),
                dcc.Checklist(id="set-llm-on", options=[{"label": " Use the local model for the morning briefing",
                                                         "value": "on"}],
                              value=["on"] if s.llm_enabled else [], className="mt-2 small"),
                html.Div([html.Button("Start Ollama", id="ol-start", className="btn-ghost"),
                          html.Button("Download models", id="ol-pull", className="btn-ghost")], className="d-flex gap-2 mt-2"),
                dcc.Loading(html.Div(id="ol-msg", className="small mt-1"), type="dot"),
            ]),
            section("Profile", [
                html.Div("Used for training-load coefficients (Banister TRIMP) and VO2max reference values.",
                         className="muted small mb-2"),
                kv([("Age (from Garmin)", p.get("chrono_age") or "–"), ("Max HR (Garmin zones)", p.get("max_hr") or "–")]),
                html.Div([dcc.Dropdown(id="set-sex", options=[{"label": "Male", "value": "male"},
                                                              {"label": "Female", "value": "female"}],
                                       value=s.sex or None, placeholder="Sex", style={"width": "140px"}),
                          dcc.Input(id="set-maxhr", type="number", value=s.max_hr_override or None,
                                    placeholder="Max HR override", className="form-control", style={"width": "170px"}),
                          dcc.Input(id="set-css400", type="number", value=s.css_test_400_s or None,
                                    placeholder="CSS 400 m (s)", className="form-control", style={"width": "150px"}),
                          dcc.Input(id="set-css200", type="number", value=s.css_test_200_s or None,
                                    placeholder="CSS 200 m (s)", className="form-control", style={"width": "150px"})],
                         className="d-flex gap-2 flex-wrap"),
            ]),
        ], className="grid g2"),
        html.Div([
            section("Strong import", [
                html.Div("Watch folder (Google Drive for desktop or any local folder):", className="small muted"),
                dcc.Input(id="set-strong", value=s.strong_folder, className="form-control"),
                dcc.Dropdown(id="set-strong-unit", options=["kg", "lb"], value=s.strong_weight_unit, clearable=False,
                             style={"width": "100px"}, className="mt-2"),
                html.Div("Weight unit used in older Strong exports without a unit in the header.", className="muted small"),
            ]),
            section("Sync & data", [
                html.Div([html.Span("Backfill days on full sync", className="small muted"),
                          dcc.Input(id="set-backfill", type="number", value=s.backfill_days, min=30, max=1500,
                                    className="form-control", style={"width": "120px"})]),
                html.Div([html.Button("Full re-sync", id="set-full", className="btn-ghost"),
                          html.Button("Recompute analytics", id="set-recompute", className="btn-ghost")],
                         className="d-flex gap-2 mt-2"),
                html.Div(id="set-sync-msg", className="small mt-1"),
                html.Div(f"Data folder: {config.data_dir()}", className="muted small mt-2"),
            ]),
        ], className="grid g2"),
        html.Button("Save settings", id="set-save", className="btn-primary-s"),
        html.Span(id="set-save-msg", className="small ms-2"),
    ])


@callback(Output("set-save-msg", "children"), Input("set-save", "n_clicks"),
          State("set-brief", "value"), State("set-chat", "value"), State("set-llm-on", "value"),
          State("set-sex", "value"), State("set-maxhr", "value"), State("set-css400", "value"),
          State("set-css200", "value"), State("set-strong", "value"), State("set-strong-unit", "value"),
          State("set-backfill", "value"), prevent_initial_call=True)
def save(_n, brief, chat, llm_on, sex, maxhr, c400, c200, strong_folder, unit, backfill):
    s = config.Settings.load()
    s.llm_briefing_model, s.llm_chat_model = brief or s.llm_briefing_model, chat or s.llm_chat_model
    s.llm_enabled = bool(llm_on)
    s.sex = sex or ""
    s.max_hr_override = int(maxhr or 0)
    s.css_test_400_s, s.css_test_200_s = float(c400 or 0), float(c200 or 0)
    s.strong_folder, s.strong_weight_unit = strong_folder or s.strong_folder, unit or "kg"
    s.backfill_days = int(backfill or 365)
    s.save()
    frame.bump_version()
    return "Saved."


@callback(Output("set-key-msg", "children"), Input("set-key-save", "n_clicks"), State("set-key", "value"),
          prevent_initial_call=True)
def save_key(_n, key):
    if not key:
        return "Paste a key first."
    try:
        OfficialClient(api_key=key.strip()).token(force=True)
    except Exception as e:  # noqa: BLE001
        return f"Key test failed: {e}"
    config.set_secret(SECRET_NAME, key.strip())
    return "Saved to Keychain and verified with Garmin ✓"


@callback(Output("gc-msg", "children"), Output("gc-state", "data"), Input("gc-login", "n_clicks"),
          State("gc-email", "value"), State("gc-pw", "value"), prevent_initial_call=True)
def gc_login(_n, email, pw):
    if not email or not pw:
        return "Enter email and password.", None
    try:
        res = UnofficialClient().login_start(email, pw)
    except Exception as e:  # noqa: BLE001
        return f"Login failed: {e}", None
    s = config.Settings.load()
    s.garmin_email = email
    s.save()
    if res["status"] == "mfa":
        return "Enter the MFA code Garmin sent you.", res["state"]
    return "Logged in ✓ — extra data arrives with the next sync.", None


@callback(Output("gc-msg", "children", allow_duplicate=True), Input("gc-mfa-btn", "n_clicks"),
          State("gc-state", "data"), State("gc-mfa", "value"), prevent_initial_call=True)
def gc_mfa(_n, state, code):
    if not state or not code:
        return "Log in first, then enter the code."
    try:
        UnofficialClient().login_finish(state, code.strip())
    except Exception as e:  # noqa: BLE001
        return f"MFA failed: {e}"
    return "Logged in ✓ — extra data arrives with the next sync."


@callback(Output("ol-msg", "children"), Input("ol-start", "n_clicks"), Input("ol-pull", "n_clicks"),
          prevent_initial_call=True)
def ollama_actions(_a, _b):
    if dash.ctx.triggered_id == "ol-start":
        return "Ollama is running ✓" if llm.ensure_ollama() else "Couldn't start Ollama — install it with `brew install ollama`."
    s = config.Settings.load()
    if not llm.ensure_ollama():
        return "Install Ollama first: `brew install ollama`."
    ok = [m for m in (s.llm_briefing_model, s.llm_chat_model) if llm.pull_model(m)]
    return f"Models ready: {', '.join(ok)}"


@callback(Output("set-sync-msg", "children"), Input("set-full", "n_clicks"), Input("set-recompute", "n_clicks"),
          prevent_initial_call=True)
def sync_actions(_a, _b):
    if dash.ctx.triggered_id == "set-full":
        return "Full re-sync started (see the top bar)." if engine.run_sync_background(full=True) else "A sync is already running."
    r = derive.recompute()
    return f"Recomputed in {r['seconds']} s."
