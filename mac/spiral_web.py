#!/usr/bin/env python3
"""The Spiral Progress page: http://127.0.0.1:8101/

macOS Python, standard library only. It reads what the tracker
(`mac/run.sh tracker`) writes into state/ and the quest lists in docs/, and
never touches the game, so it can stay open with or without the game.

  python3 mac/spiral_web.py            serve (Ctrl+C stops it)
  python3 mac/spiral_web.py --open     ... and open it in the browser
  python3 mac/spiral_web.py --ensure   start it in the background unless it runs, open it
  python3 mac/spiral_web.py --print    the page's numbers once, as text
  python3 mac/spiral_web.py --finished Wintertusk --finished Wysteria
  python3 mac/spiral_web.py --unfinished Wintertusk

The page asks for /data.json every two seconds. Its one write is a world's
"Mark finished" button (state/worlds_done.json): the optional worlds
(Grizzleheim, Wintertusk, Wysteria, Catacombs) cannot be inferred.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from wiz101_auto import spiral  # noqa: E402  (stdlib-only module)

PORT = 8101
PAGE = Path(__file__).with_name("spiral.html")
DOCS = ROOT / "docs"
STATE = ROOT / "state"
HOSTS = ("127.0.0.1", "localhost")
# The code this server runs. A server started from older code is restarted:
# it would serve the new page file with the old numbers (10-27: "Mark
# finished" did nothing until the page server was restarted).
CODE = (Path(__file__).resolve(), ROOT / "src" / "wiz101_auto" / "spiral.py")
RELOAD_CHECK = 2.0


def code_version() -> str:
    h = hashlib.sha1()
    for f in CODE:
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


VERSION = code_version()


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, kind: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local(self) -> bool:
        """Only this Mac's own page: another site cannot rebind a name to 127.0.0.1."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in HOSTS

    def do_GET(self):  # noqa: N802 (http.server API)
        if not self._local():
            self.send_error(403)
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/version":
            self._send(json.dumps({"version": VERSION}).encode("utf-8"), "application/json")
        elif path == "/data.json":
            try:
                body = json.dumps(spiral.build(DOCS, STATE)).encode("utf-8")
            except Exception as exc:  # a bad file must not take the page down
                body = json.dumps({"error": repr(exc)}).encode("utf-8")
            self._send(body, "application/json")
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802 (http.server API)
        # The custom header needs a CORS preflight, which this server never
        # answers: only the page itself can mark a world.
        if not self._local() or self.headers.get("X-Spiral") != "1" or self.path != "/api/world":
            self.send_error(403)
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 10000)
            payload = json.loads(self.rfile.read(length) or b"{}")
            marked = spiral.set_world_done(STATE, str(payload.get("world", "")), bool(payload.get("done")))
            body = json.dumps({"ok": True, "worlds": marked}).encode("utf-8")
        except Exception as exc:
            body = json.dumps({"ok": False, "error": str(exc)}).encode("utf-8")
        self._send(body, "application/json")

    def log_message(self, *args):  # keep the terminal quiet
        pass


def serving(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def serve(port: int, open_browser: bool) -> None:
    url = f"http://127.0.0.1:{port}/"
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Spiral Progress at {url} (Ctrl+C stops it)", flush=True)
    if open_browser:
        webbrowser.open(url)
    changed = threading.Event()

    def watch_code() -> None:
        while not changed.wait(RELOAD_CHECK):
            if code_version() != VERSION:
                changed.set()
                server.shutdown()

    threading.Thread(target=watch_code, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    if changed.is_set():
        # A git pull changed this server's code: run the new code, same port.
        print("the page server's code changed; restarting it", flush=True)
        args = [a for a in sys.argv[1:] if a != "--open"]
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *args])


def running_version(port: int) -> str | None:
    """The version of the server on `port` ("" for one too old to say)."""
    from urllib.error import HTTPError
    from urllib.request import urlopen

    try:
        with urlopen(f"http://127.0.0.1:{port}/version", timeout=2) as r:
            return json.loads(r.read() or b"{}").get("version") or ""
    except HTTPError:
        return ""
    except Exception:
        return None


def stop_stale(port: int) -> bool:
    """Stop the page server listening on `port` (only a spiral_web.py process)."""
    try:
        pids = subprocess.run(["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                              capture_output=True, text=True, timeout=10).stdout.split()
    except Exception:
        return False
    stopped = False
    for pid in pids:
        try:
            cmd = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True,
                                 timeout=10).stdout
            if "spiral_web.py" in cmd:
                os.kill(int(pid), signal.SIGTERM)
                stopped = True
        except Exception:
            continue
    for _ in range(50):
        if not serving(port):
            break
        time.sleep(0.1)
    return stopped and not serving(port)


def ensure(port: int) -> None:
    """Start the page in the background unless it is up, then open it."""
    url = f"http://127.0.0.1:{port}/"
    if serving(port):
        theirs = running_version(port)
        if theirs is not None and theirs != VERSION:
            # (A current server restarts itself within RELOAD_CHECK; an old one never does.)
            time.sleep(RELOAD_CHECK + 1)
            if running_version(port) != VERSION:
                if stop_stale(port):
                    print("stopped an older Spiral Progress server")
                else:
                    print(f"An older Spiral Progress server is on port {port} and could not be stopped. "
                          "Stop it (pkill -f spiral_web.py), then run this again.")
                    return
    if not serving(port):
        log = ROOT / "logs" / "spiral_web.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as out:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--port", str(port),
                 *(["--state", str(STATE)] if STATE != ROOT / "state" else [])],
                stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True,
            )
        for _ in range(30):
            if serving(port):
                break
            time.sleep(0.1)
    print(f"Spiral Progress: {url}")
    webbrowser.open(url)


def print_summary() -> None:
    data = spiral.build(DOCS, STATE)
    cur = data["current"]
    print(f"tracker: {'live' if data['running'] else 'not running'}")
    print(f"now: {cur['world'] or '?'} / {cur['area'] or cur['zone'] or '?'}; quest: {cur['quest'] or '?'}; "
          f"goal: {cur['goal'] or '?'}")
    print(f"story: {data['story']['done']}/{data['story']['total']} ({data['story']['pct']}%)")
    for w in data["worlds"]:
        if w["has_list"]:
            mark = "*" if w["current"] else " "
            print(f" {mark} {w['name']:<12} {w['done']:>4}/{w['total']:<4} {w['pct']:>3}%")


def main() -> None:
    ap = argparse.ArgumentParser(description="The Spiral Progress page")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--open", action="store_true", help="open it in the browser")
    ap.add_argument("--ensure", action="store_true", help="start in the background unless running; open it")
    ap.add_argument("--print", action="store_true", help="print the numbers once and exit")
    ap.add_argument("--state", type=Path, help="read this state folder instead of the repo's (a demo)")
    ap.add_argument("--finished", action="append", default=[], metavar="WORLD", help="mark a world finished")
    ap.add_argument("--unfinished", action="append", default=[], metavar="WORLD", help="take that mark back")
    args = ap.parse_args()
    if args.state:
        global STATE
        STATE = args.state.resolve()
    if args.finished or args.unfinished:
        try:
            for world in args.finished:
                marked = spiral.set_world_done(STATE, world, True)
            for world in args.unfinished:
                marked = spiral.set_world_done(STATE, world, False)
        except ValueError as exc:
            sys.exit(f"{exc}. Worlds: {', '.join((*spiral.WORLDS, *spiral.SIDE_WORLDS))}")
        print(f"marked finished: {', '.join(marked) or 'none'}")
    elif args.print:
        print_summary()
    elif args.ensure:
        ensure(args.port)
    elif serving(args.port):
        print(f"already running at http://127.0.0.1:{args.port}/")
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
    else:
        serve(args.port, args.open)


if __name__ == "__main__":
    main()
