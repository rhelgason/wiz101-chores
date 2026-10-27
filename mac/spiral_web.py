#!/usr/bin/env python3
"""The Spiral Progress page: http://127.0.0.1:8101/

macOS Python, standard library only. It reads what the tracker
(`mac/run.sh tracker`) writes into state/ and the quest lists in docs/, and
never touches the game, so it can stay open with or without the game.

  python3 mac/spiral_web.py            serve (Ctrl+C stops it)
  python3 mac/spiral_web.py --open     ... and open it in the browser
  python3 mac/spiral_web.py --ensure   start it in the background unless it runs, open it
  python3 mac/spiral_web.py --print    the page's numbers once, as text

The page asks for /data.json every two seconds. It only reads.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
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
        elif path == "/data.json":
            try:
                body = json.dumps(spiral.build(DOCS, STATE)).encode("utf-8")
            except Exception as exc:  # a bad file must not take the page down
                body = json.dumps({"error": repr(exc)}).encode("utf-8")
            self._send(body, "application/json")
        else:
            self.send_error(404)

    def log_message(self, *args):  # keep the terminal quiet
        pass


def serving(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def serve(port: int, open_browser: bool) -> None:
    url = f"http://127.0.0.1:{port}/"
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Spiral Progress at {url} (Ctrl+C stops it)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def ensure(port: int) -> None:
    """Start the page in the background unless it is up, then open it."""
    url = f"http://127.0.0.1:{port}/"
    if not serving(port):
        log = ROOT / "logs" / "spiral_web.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as out:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--port", str(port)],
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
    args = ap.parse_args()
    if args.state:
        global STATE
        STATE = args.state.resolve()
    if args.print:
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
