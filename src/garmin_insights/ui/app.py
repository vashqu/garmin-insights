"""Dash application shell: sidebar navigation, top bar with sync status, global stores and callbacks."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html

from garmin_insights import config
from garmin_insights.analytics import frame
from garmin_insights.storage import db
from garmin_insights.sync import engine

HERE = Path(__file__).parent
PAGES = ["home", "sleep", "recovery", "stress", "muscle", "training", "activity", "running", "swimming",
         "longevity", "journal", "explore", "settings"]

NAV = [
    ("/", "☀", "Today"),
    ("/sleep", "☾", "Sleep"),
    ("/recovery", "♥", "Recovery"),
    ("/stress", "≈", "Stress & energy"),
    None,
    ("/muscle", "◆", "Muscle"),
    ("/training", "▤", "Training"),
    ("/running", "⚑", "Running & race"),
    ("/swimming", "∿", "Swimming"),
    ("/longevity", "✚", "Longevity"),
    None,
    ("/journal", "✎", "Journal"),
    ("/explore", "⌕", "Explore & ask"),
    ("/settings", "⚙", "Settings"),
]


def _nav():
    items = []
    for n in NAV:
        if n is None:
            items.append(html.Div(className="nav-sep"))
            continue
        href, icon, label = n
        items.append(dcc.Link([html.Span(icon, className="nav-icon"), label], href=href, className="nav-item",
                              id={"type": "nav", "href": href}))
    return items


def create_app() -> dash.Dash:
    app = dash.Dash(
        __name__, use_pages=True, pages_folder="", assets_folder=str(HERE / "assets"),
        external_stylesheets=[dbc.themes.BOOTSTRAP], suppress_callback_exceptions=True,
        title=config.APP_NAME, update_title=None,
    )
    import importlib

    for name in PAGES:  # explicit imports: one module identity per page (no duplicate registration)
        mod = importlib.import_module(f"garmin_insights.ui.pages.{name}")
        if mod.__name__ in dash.page_registry:
            dash.page_registry[mod.__name__]["layout"] = mod.layout
    app.layout = html.Div([
        dcc.Location(id="url"),
        dcc.Store(id="theme", storage_type="memory"),
        dcc.Store(id="data-version", data=frame.data_version()),
        dcc.Store(id="sync-was-running", data=False),
        dcc.Interval(id="sync-poll", interval=1500, n_intervals=0),
        html.Div([
            html.Div([html.Div([config.APP_NAME, html.Small("Garmin · Strong · science-based")], className="brand"),
                      *_nav()], className="sidebar"),
            html.Div([
                html.Div([
                    html.Div(id="page-title", className="title"),
                    html.Div([html.Span(id="sync-status"),
                              dbc.Progress(id="sync-progress", value=0, style={"width": "120px"},
                                           className="d-none"),
                              html.Button("Sync now", id="sync-btn", className="btn-ghost")], className="syncbar"),
                ], className="topbar"),
                html.Div(dash.page_container, className="content"),
            ], className="main"),
        ], className="shell"),
    ])
    _register_callbacks(app)
    return app


def _register_callbacks(app: dash.Dash) -> None:
    # Theme: follow macOS appearance (prefers-color-scheme) inside the webview.
    app.clientside_callback(
        """function(_) {
            const m = window.matchMedia('(prefers-color-scheme: dark)');
            if (!window.__giThemeHooked) {
                window.__giThemeHooked = true;
                m.addEventListener('change', () => {
                    dash_clientside.set_props('theme', {data: m.matches ? 'dark' : 'light'});
                });
            }
            return m.matches ? 'dark' : 'light';
        }""",
        Output("theme", "data"), Input("url", "pathname"),
    )

    @app.callback(Output({"type": "nav", "href": dash.ALL}, "className"), Output("page-title", "children"),
                  Input("url", "pathname"))
    def _active(path):
        out, title = [], ""
        for n in NAV:
            if n is None:
                continue
            href, _, label = n
            active = (path == href) or (href != "/" and (path or "").startswith(href))
            out.append("nav-item active" if active else "nav-item")
            if active:
                title = label
        if (path or "").startswith("/activity"):
            title = "Activity"
        return out, title

    @app.callback(Output("sync-btn", "disabled"), Input("sync-btn", "n_clicks"), prevent_initial_call=True)
    def _sync(_n):
        engine.run_sync_background()
        return True

    @app.callback(Output("sync-status", "children"), Output("sync-progress", "value"),
                  Output("sync-progress", "className"), Output("sync-btn", "disabled", allow_duplicate=True),
                  Output("data-version", "data"), Output("sync-was-running", "data"),
                  Input("sync-poll", "n_intervals"), State("sync-was-running", "data"),
                  State("data-version", "data"), prevent_initial_call="initial_duplicate")
    def _poll(_n, was_running, ver):
        p = engine.PROGRESS
        cur_ver = frame.data_version()
        new_ver = cur_ver if cur_ver != ver else dash.no_update
        if p.running:
            pct = int(100 * p.done / p.total) if p.total else 5
            return f"Syncing · {p.step}…", pct, "", True, new_ver, True
        last = db.get_state("last_sync_at")
        if p.message and not p.running and p.errors:
            txt = f"⚠ {p.message}"
        elif last:
            t = datetime.fromisoformat(last)
            ago = (datetime.now() - t).total_seconds() / 60
            when = "just now" if ago < 1 else (f"{int(ago)} min ago" if ago < 60 else t.strftime("%d %b %H:%M"))
            txt = f"Synced {when}" + (f" · {len(p.errors)} warnings" if p.errors else "")
        else:
            txt = "Not synced yet"
        if was_running:  # sync just finished → refresh pages
            frame.bump_version()
            new_ver = frame.data_version()
        return txt, 0, "d-none", False, new_ver, False
