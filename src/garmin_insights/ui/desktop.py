"""Launch the Dash server on localhost and show it in a native macOS window (pywebview)."""

from __future__ import annotations

import fcntl
import logging
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from garmin_insights import config

log = logging.getLogger(__name__)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _single_instance():
    f = open(config.data_dir() / "app.lock", "w")  # noqa: SIM115 - held for process lifetime
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return f


def _wait(url: str, timeout: float = 30) -> bool:
    t = time.time()
    while time.time() - t < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    return False


def _warmup() -> None:
    """Start Ollama and pre-build the analytics bundle so the first page renders fast."""
    try:
        from garmin_insights.analytics.frame import get_bundle
        from garmin_insights.recommend import llm

        get_bundle()
        if config.Settings.load().llm_enabled:
            llm.ensure_ollama()
    except Exception as e:  # noqa: BLE001
        log.warning("warmup: %s", e)


def _window(url: str) -> int:
    import webview

    pref = config.Settings.load().theme
    bg = "#0d0d0d" if pref == "dark" else "#f9f9f7"  # avoids a white flash before the page loads in dark mode
    webview.create_window(config.APP_NAME, url, width=1360, height=900, min_size=(1024, 700),
                          background_color=bg, text_select=True)
    webview.start()
    return 0


def launch(browser: bool = False, sync: bool = True, port: int = 0, server_only: bool = False) -> int:
    import os
    import signal

    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    url_file = config.data_dir() / "app.url"
    lock = _single_instance()
    if lock is None:
        # Another instance holds the lock: attach a window to it instead of silently exiting.
        try:
            existing = url_file.read_text().strip()
        except OSError:
            existing = ""
        if existing and _wait(existing, timeout=3):
            log.info("already running at %s — opening a window onto it", existing)
            if browser:
                webbrowser.open(existing)
                return 0
            return 0 if server_only else _window(existing)
        print("Garmin Insights is already running (or a stale lock exists).", file=sys.stderr)
        return 1
    from garmin_insights.ui.app import create_app

    app = create_app()
    port = port or _free_port()
    url = f"http://127.0.0.1:{port}/"
    th = threading.Thread(target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
                          daemon=True, name="dash")
    th.start()
    threading.Thread(target=_warmup, daemon=True).start()
    if sync:
        from garmin_insights.sync.engine import run_sync_background

        run_sync_background(auto=True)
    if not _wait(url):
        print("Server failed to start", file=sys.stderr)
        return 1
    url_file.write_text(url)
    if browser or server_only:
        if browser:
            webbrowser.open(url)
        print(f"Serving on {url}", flush=True)
        try:
            while th.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0
    try:
        return _window(url)
    finally:
        url_file.unlink(missing_ok=True)
