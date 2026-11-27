"""bacpro desktop launcher.

Starts the local service in a background thread, then opens the console in a
chromeless app window (Edge/Chrome ``--app``) or the default browser.

Behaviour
---------
* single instance - if the port is already bound it just re-opens the window
* the window owns the process: close the window and bacpro exits, leaving no
  orphaned background process behind
* if no Chromium-family browser is available the default browser is used and a
  page heartbeat decides when the app has been closed
* windowed (no console) builds write everything to a log file, and fatal
  errors are shown in a message box instead of vanishing
* ``--port`` / ``--no-browser`` / ``--headless`` / ``--keep-alive`` switches

This file is the PyInstaller entry point (see bacpro.spec).
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
import webbrowser

APP_NAME = "bacpro"
DEFAULT_PORT = 5001
HOST = "127.0.0.1"
WINDOW_SIZE = "1500,940"

log = logging.getLogger(APP_NAME)


# --------------------------------------------------------------------------- paths
def app_data_dir() -> str:
    """Per-user writable directory for logs and the app-window profile."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if not base:
            base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
        path = os.path.join(base, APP_NAME)
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", APP_NAME)
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
        path = os.path.join(base, APP_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = tempfile.gettempdir()
    return path


LOG_PATH = os.path.join(app_data_dir(), "logs", "bacpro.log")


def setup_logging(verbose: bool = False) -> None:
    """Log to file always; to stderr only when a console exists."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    except OSError:
        pass
    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None and sys.stderr.isatty():
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers or [logging.NullHandler()],
    )
    log.info("=" * 68)
    log.info("%s starting - python %s on %s", APP_NAME,
             sys.version.split()[0], sys.platform)
    log.info("log file: %s", LOG_PATH)


def redirect_streams() -> None:
    """Windowed builds have no stdout/stderr; give them a file so prints work."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        os.makedirs(os.path.join(app_data_dir(), "logs"), exist_ok=True)
        stream = open(os.path.join(app_data_dir(), "logs", "console.log"),
                      "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def install_excepthook() -> None:
    """Log uncaught errors and, on Windows, tell the user instead of dying mute."""
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.critical("unhandled exception\n%s", text)
        if sys.platform.startswith("win"):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None,
                    f"bacpro hit an unexpected error and has to close.\n\n"
                    f"{exc_type.__name__}: {exc}\n\nDetails were written to:\n{LOG_PATH}",
                    "bacpro", 0x10,
                )
            except Exception:
                pass
    sys.excepthook = hook
    try:
        threading.excepthook = lambda args: hook(
            args.exc_type, args.exc_value, args.exc_traceback)
    except Exception:
        pass


# --------------------------------------------------------------------------- net
def port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.35)
        return sock.connect_ex((host, port)) == 0


def wait_for_server(host: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_is_open(host, port):
            return True
        time.sleep(0.15)
    return False


# ------------------------------------------------------------------------ window
def find_chromium() -> str | None:
    """Return a Chromium-family browser that supports ``--app=``."""
    if sys.platform.startswith("win"):
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        candidates = [
            r"Microsoft\Edge\Application\msedge.exe",
            r"Google\Chrome\Application\chrome.exe",
            r"Chromium\Application\chrome.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe",
        ]
        for root in roots:
            if not root:
                continue
            for rel in candidates:
                path = os.path.join(root, rel)
                if os.path.isfile(path):
                    return path
    elif sys.platform == "darwin":
        for rel in ("Google Chrome.app/Contents/MacOS/Google Chrome",
                    "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                    "Chromium.app/Contents/MacOS/Chromium"):
            path = os.path.join("/Applications", rel)
            if os.path.isfile(path):
                return path
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium",
                     "chromium-browser", "microsoft-edge", "brave-browser"):
            for folder in os.environ.get("PATH", "").split(os.pathsep):
                path = os.path.join(folder, name)
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return path
    return None


def open_window(url: str):
    """Open the console in an app window. Returns the browser process or None.

    The browser is given its own throwaway profile so the returned process is
    exclusively ours: when the user closes the window that process exits, which
    is how the launcher knows to shut the service down.
    """
    browser = find_chromium()
    if not browser:
        log.info("no Chromium-family browser found; using the default browser")
        webbrowser.open(url)
        return None

    profile = os.path.join(app_data_dir(), "window")
    try:
        os.makedirs(profile, exist_ok=True)
    except OSError:
        profile = os.path.join(tempfile.gettempdir(), APP_NAME + "-window")

    args = [
        browser,
        f"--app={url}",
        f"--window-size={WINDOW_SIZE}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        "--disable-background-mode",
        "--disable-session-crashed-bubble",
    ]
    try:
        import subprocess
        kwargs = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, **kwargs)
        log.info("app window started (pid %s) using %s", proc.pid, browser)
        return proc
    except Exception as exc:  # noqa: BLE001
        log.warning("could not start %s (%s); falling back to the default browser",
                    browser, exc)
        webbrowser.open(url)
        return None


# ------------------------------------------------------------------------ server
def start_server(port: int):
    """Start the Flask app on a werkzeug server we can shut down cleanly."""
    from server import app as flask_app
    from werkzeug.serving import make_server

    httpd = make_server(HOST, port, flask_app, threaded=True)
    thread = threading.Thread(target=httpd.serve_forever,
                              name="bacpro-http", daemon=True)
    thread.start()
    return httpd, thread


def main() -> int:
    parser = argparse.ArgumentParser(
        prog=APP_NAME, description="bacpro BACnet / Modbus commissioning console")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="do not open a window")
    parser.add_argument("--headless", action="store_true",
                        help="serve only, no window (Ctrl+C to stop)")
    parser.add_argument("--keep-alive", action="store_true",
                        help="keep running after the window closes")
    parser.add_argument("--idle-timeout", type=float, default=180.0,
                        help="seconds without a page heartbeat before exiting "
                             "when the window process cannot be tracked")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    redirect_streams()
    setup_logging(args.verbose)
    install_excepthook()

    url = f"http://{HOST}:{args.port}/"

    if port_is_open(HOST, args.port):
        log.info("%s already running on port %s - opening a window", APP_NAME, args.port)
        if not (args.no_browser or args.headless):
            open_window(url)
        return 0

    try:
        httpd, thread = start_server(args.port)
    except Exception as exc:  # noqa: BLE001
        log.exception("could not start the local service")
        if sys.platform.startswith("win"):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None,
                    f"bacpro could not start its local service on port {args.port}.\n\n"
                    f"{exc}\n\nAnother program may be using that port. Details:\n{LOG_PATH}",
                    "bacpro", 0x10)
            except Exception:
                pass
        return 1

    if not wait_for_server(HOST, args.port):
        log.error("service did not answer on %s", url)
        return 1
    log.info("%s listening on %s", APP_NAME, url)

    if args.no_browser or args.headless:
        log.info("running without a window - press Ctrl+C to stop")
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        httpd.shutdown()
        log.info("stopped")
        return 0

    window = open_window(url)
    exit_reason = "window closed"

    if args.keep_alive:
        exit_reason = "Ctrl+C"
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    elif window is not None:
        # The window process is ours alone: its exit means the user closed the app.
        while window.poll() is None:
            time.sleep(0.4)
        log.info("app window closed (exit code %s)", window.returncode)
    else:
        # No trackable window: fall back to the page heartbeat.
        from server import last_seen
        started = time.time()
        while thread.is_alive():
            time.sleep(0.5)
            beat = last_seen()
            if beat:
                if time.time() - beat > args.idle_timeout:
                    exit_reason = "page closed"
                    break
            elif time.time() - started > args.idle_timeout:
                exit_reason = "no page connected"
                break

    log.info("shutting down (%s)", exit_reason)
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
