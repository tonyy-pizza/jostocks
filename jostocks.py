#!/usr/bin/env python3
r"""jostocks.py - a desktop view over the stocks scan pipeline.

Reads what the scan wrote and lets you move the sizing controls against it.
It never writes to the pipeline's data files: holdings.json,
sized_candidates.json, clustered.json and scored_candidates.json belong to the
pipeline, and this app only reads them. Its own state lives in data/ beside
this file.

Usage:
    python jostocks.py                # open the window
    python jostocks.py --refresh      # open, and refresh if the data is stale
    python jostocks.py --no-window    # refresh only, no UI (same as autorun)
    python jostocks.py --install-autostart
    python jostocks.py --uninstall-autostart
    python jostocks.py --self-test    # check the install without a display

Launch it from launch.bat on Windows, or the Startup-folder shortcut that
install_autostart.ps1 creates.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# This file's folder must be importable as a package root even when the app is
# started from somewhere else - a Startup shortcut sets its own working
# directory, and a .lnk may not set one at all.
PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="jostocks - desktop view over the stocks scan pipeline")
    parser.add_argument("--refresh", action="store_true",
                        help="refresh the pipeline data on startup if it is stale")
    parser.add_argument("--no-window", action="store_true",
                        help="refresh and exit, without opening a window")
    parser.add_argument("--install-autostart", action="store_true",
                        help="add jostocks to the login path and exit")
    parser.add_argument("--uninstall-autostart", action="store_true",
                        help="remove it from the login path and exit")
    parser.add_argument("--self-test", action="store_true",
                        help="report what the app can see, and exit")
    return parser.parse_args(argv)


def _self_test() -> int:
    """Everything checkable without a display."""
    from jos import pipeline as pipe
    from jos import state as state_module
    from jos.datastore import Scan, venue_coverage
    from jos.paths import APP_DATA_DIR, DataPaths, pipeline_data_dir
    from jos.views.webpanel import AVAILABLE as WEB, IMPORT_ERROR as WEB_ERROR
    import platform_support as platform

    print("\n  jostocks self-test")
    print("  " + "-" * 62)
    print(f"  platform:        {platform.platform_name()}")
    print(f"  python:          {sys.executable}")
    print(f"  project:         {PROJECT_DIR}")

    settings = state_module.Settings()
    state_module.init()
    print(f"  app state:       {APP_DATA_DIR}")
    print(f"  settings:        {settings.path.name} "
          f"({'present' if settings.path.is_file() else 'defaults'})")

    ok = pipe.bootstrap(settings.get("pipeline_dir"))
    print(f"  pipeline:        {'found at ' + str(pipe.PIPELINE_DIR) if ok else 'NOT FOUND'}")
    if not ok:
        print(f"                   {pipe.IMPORT_ERROR}")
    else:
        good, fair = pipe.colour_thresholds()
        print(f"  score colours:   green >= {good}, amber >= {fair} "
              f"(from stock_evaluator)")
        print(f"  scan_report:     {pipe.scan_report_script()}")

    data_paths = DataPaths(pipeline_data_dir(settings.get("data_dir")))
    print(f"  pipeline data:   {data_paths.dir}")
    for label, path in (("sized", data_paths.sized),
                        ("clustered", data_paths.clustered),
                        ("scored", data_paths.scored),
                        ("holdings", data_paths.holdings)):
        print(f"    {label:<12}   {'ok' if path.is_file() else 'missing'}")

    scan = Scan(data_paths)
    if scan.ok:
        print(f"  candidates:      {len(scan.candidates)}")
        print(f"  holdings:        {len(scan.holdings)}")
        print(f"  coverage:        {scan.coverage_line()}")
        print(f"  freshness:       {scan.freshness_line()}")
        from jos import sizing as sizing_module
        problems = sizing_module.verify(scan.candidates, scan.params)
        print(f"  sizing check:    "
              f"{'matches the scan' if not problems else f'{len(problems)} MISMATCH(ES)'}")
    else:
        print(f"  candidates:      none - {scan.empty_reason}")

    print(f"  web panels:      {'ok' if WEB else 'unavailable: ' + str(WEB_ERROR)}")
    print(f"  autostart:       {platform.describe_autostart()}")
    print(f"  scan args:       {' '.join(settings.scan_args()) or '(none)'}")
    print()
    return 0


def main(argv=None) -> int:
    args = _parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.install_autostart or args.uninstall_autostart:
        import platform_support as platform
        if args.install_autostart:
            ok, message = platform.install_autostart()
        else:
            ok, message = platform.uninstall_autostart()
        print(message or ("done" if ok else "failed"))
        return 0 if ok else 1

    from jos import state as state_module
    state_module.init()
    settings = state_module.Settings()

    from jos import pipeline as pipe
    pipe.bootstrap(settings.get("pipeline_dir"))

    # ── headless refresh, no UI ─────────────────────────────────────
    if args.no_window:
        from jos import refresh as refresh_module
        from jos.paths import DataPaths, pipeline_data_dir
        data_paths = DataPaths(pipeline_data_dir(settings.get("data_dir")))
        ok, message = refresh_module.refresh(settings, data_paths,
                                             reason="cli", manual=args.refresh)
        print(message)
        return 0 if ok else 1

    # ── the window ──────────────────────────────────────────────────
    from PyQt6.QtWidgets import QApplication

    from jos import theme
    from jos.instance import InstanceServer, send_to_running

    # A running instance gets the message and this launch exits (section 9).
    if send_to_running("show"):
        print("jostocks is already running; raised the existing window.")
        return 0

    application = QApplication(sys.argv[:1])
    application.setApplicationName("jostocks")
    application.setApplicationDisplayName("jostocks")
    application.setOrganizationName("jostocks")

    theme.load_fonts()
    application.setStyleSheet(theme.app_stylesheet())

    from jos.window import MainWindow

    notes = state_module.Notes()
    window = MainWindow(settings, notes)

    server = InstanceServer(parent=window)
    if server.listen():
        server.message.connect(window.handle_message)

    window.show()

    if args.refresh and settings.get("auto_refresh_on_logon", True):
        window.refresh.start(reason="startup")

    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
