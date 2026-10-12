# -*- coding: utf-8 -*-
import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

from . import __version__
from .manager import Manager, data_dir
from .server import make_server


def find_edge():
    cands = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            cands.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
            cands.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="uvp280-monitor", description="УВП-280 Монитор")
    ap.add_argument("--port", type=int, default=0, help="порт локального сервера (по умолчанию - свободный)")
    ap.add_argument("--no-window", action="store_true", help="не открывать окно, только сервер")
    ap.add_argument("--config", help="файл со списком приборов")
    a = ap.parse_args(argv)

    m = Manager(a.config)
    srv = make_server(m, port=a.port)
    port = srv.server_address[1]
    url = "http://127.0.0.1:%d/" % port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    m.start()
    if sys.stdout:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print("УВП-280 Монитор %s: %s" % (__version__, url), flush=True)

    proc = None
    if not a.no_window:
        edge = find_edge()
        if edge:
            prof = os.path.join(data_dir(), "window")
            try:
                proc = subprocess.Popen([edge, "--app=" + url, "--user-data-dir=" + prof, "--no-first-run",
                                         "--window-size=1360,900"])
            except OSError:
                proc = None
        if proc is None:
            webbrowser.open(url)

    app = srv.app
    try:
        # закрываем программу, когда окно закрыто (нет «пульса» от страницы)
        while not app.shutdown.is_set():
            time.sleep(0.5)
            if app.last_ping is not None and time.time() - app.last_ping > 180:
                break
            if app.last_ping is None and time.time() - app.started > 120 and not a.no_window:
                break
    except KeyboardInterrupt:
        pass
    m.stop.set()
    srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
