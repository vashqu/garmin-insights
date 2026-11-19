"""Command line entry point: ``garmin-app [run|sync|login|import-strong|recompute|demo-db]``."""

from __future__ import annotations

import argparse
import getpass
import json
import sys


def main(argv: list[str] | None = None) -> int:
    from garmin_insights import config

    ap = argparse.ArgumentParser(prog="garmin-app", description=config.APP_NAME)
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="open the desktop app (default)")
    r.add_argument("--browser", action="store_true", help="open in the browser instead of a native window")
    r.add_argument("--no-sync", action="store_true", help="don't sync on launch")
    r.add_argument("--port", type=int, default=0)
    r.add_argument("--server-only", action="store_true", help="run the local server without opening a window")
    s = sub.add_parser("sync", help="sync Garmin data now")
    s.add_argument("--days", type=int, default=None, help="only the last N days")
    s.add_argument("--full", action="store_true", help="re-fetch the whole backfill window")
    sub.add_parser("login", help="log in to Garmin Connect (unofficial fallback library)")
    i = sub.add_parser("import-strong", help="import a Strong CSV export")
    i.add_argument("path", nargs="?", help="CSV file (default: the configured watch folder)")
    sub.add_parser("recompute", help="recompute analytics from stored data")
    args = ap.parse_args(argv)
    config.setup_logging()

    if args.cmd in (None, "run"):
        from garmin_insights.ui.desktop import launch

        return launch(browser=getattr(args, "browser", False), sync=not getattr(args, "no_sync", False),
                      port=getattr(args, "port", 0), server_only=getattr(args, "server_only", False))
    if args.cmd == "sync":
        from garmin_insights.sync.engine import PROGRESS, SyncEngine

        res = SyncEngine().run(full=args.full, days=args.days)
        print(json.dumps(res, indent=1))
        if PROGRESS.errors:
            print(f"{len(PROGRESS.errors)} non-fatal errors (see log)", file=sys.stderr)
        return 0
    if args.cmd == "login":
        from garmin_insights.sources.unofficial import UnofficialClient

        st = config.Settings.load()
        email = input(f"Garmin email [{st.garmin_email}]: ").strip() or st.garmin_email
        pw = getpass.getpass("Garmin password: ")
        u = UnofficialClient()
        res = u.login_start(email, pw)
        if res["status"] == "mfa":
            code = input("MFA code: ").strip()
            res = u.login_finish(res["state"], code)
        st.garmin_email = email
        st.save()
        print("Logged in." if res["status"] == "ok" else res)
        return 0
    if args.cmd == "import-strong":
        from garmin_insights.sources import strong

        st = config.Settings.load()
        if args.path:
            with open(args.path, encoding="utf-8", errors="replace") as f:
                print(strong.import_text(f.read(), st.strong_weight_unit))
        else:
            print(strong.import_folder(st.strong_folder, st.strong_weight_unit))
        unm = strong.unmapped_exercises()
        if unm:
            print("Unmapped exercises (map them in the Muscle page):", ", ".join(unm))
        return 0
    if args.cmd == "recompute":
        from garmin_insights.analytics import derive

        print(derive.recompute())
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
