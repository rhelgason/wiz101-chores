#!/usr/bin/env python3
"""Watch one bot run from the Mac side and keep the evidence if Wizard101 dies.

mac/run.sh calls `start` before the bot and `watch` beside it. macOS Python,
standard library only (the minidump reader is src/wiz101_auto/crashdump.py).

Why it exists: the game rewrites WizardClient.log on every launch and
overwrites its one minidump on every crash, so by the time anyone looked,
the evidence for the earlier crashes was gone. This copies it the moment
the game process disappears (or Wine's crash handler starts, or a new
minidump appears), and writes CRASH.md: the verdict from the dump, the
bot's last steps and code writes, and the game log's last error lines.

  crashwatch.py start  --run-dir DIR
  crashwatch.py watch  --run-dir DIR --bot-pid PID
  crashwatch.py report --run-dir DIR            (rebuild CRASH.md from what was saved)
  crashwatch.py dump   FILE.dmp [--hooks hook_session.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from wiz101_auto import crashdump  # noqa: E402  (stdlib-only module)

BOTTLE = Path.home() / "Library/Application Support/Wizard101/Bottles/wizard101"
GAME_BIN = BOTTLE / "drive_c/ProgramData/KingsIsle Entertainment/Wizard101/Bin"
GAME_LOG = GAME_BIN / "WizardClient.log"
DIAG = Path.home() / "Library/Logs/DiagnosticReports"
DIAG_NAMES = re.compile(r"wine|wizard|preloader|python|rosetta|oah", re.I)
INTERESTING = re.compile(
    r"error|exception|crash|fatal|assert|unhandled|access violation|disconnect|lost connection|"
    r"timed out|timeout|failed|abort|terminate|shut ?down",
    re.I,
)
LOG_KEEP = 4 * 1024 * 1024
POLL = 1.0


def now_text(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def processes() -> list[tuple[int, str]]:
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, cmd = line.partition(" ")
        if pid.isdigit():
            rows.append((int(pid), cmd))
    return rows


def game_pids(rows=None) -> list[int]:
    rows = rows if rows is not None else processes()
    return [pid for pid, cmd in rows if "WizardGraphicalClient.exe" in cmd and "crashwatch" not in cmd]


def crash_handlers(rows=None) -> list[str]:
    rows = rows if rows is not None else processes()
    return [cmd for _pid, cmd in rows if re.search(r"winedbg|BugReporter\.exe", cmd)]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def dumps() -> dict[str, float]:
    try:
        return {p.name: p.stat().st_mtime for p in GAME_BIN.glob("*.dmp")}
    except OSError:
        return {}


def size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def run_cmd(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception as exc:
        return f"({exc})"


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tail_lines(path: Path, n: int) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(max(0, end - 512 * 1024))
            return f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []


def start(run_dir: Path) -> None:
    """Note what exists before the bot starts, so anything newer belongs to this run."""
    run_dir.mkdir(parents=True, exist_ok=True)
    info = {
        "started": now_text(),
        "started_ts": time.time(),
        "game_pids": game_pids(),
        "game_log_offset": size_of(GAME_LOG),
        "dumps_before": dumps(),
        "macos": run_cmd(["sw_vers", "-productVersion"]),
        "machine": run_cmd(["uname", "-m"]),
        "cpu": run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"]),
        "wine": os.environ.get("WIZ_WINE_VERSION", ""),
    }
    (run_dir / "run.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    # The game overwrites its one dump per crash: keep a copy of whatever is there now.
    keep = ROOT / "logs" / "dumps"
    for name, mtime in info["dumps_before"].items():
        target = keep / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(mtime))}-{name}"
        if not target.exists():
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy2(GAME_BIN / name, target)


def hook_map(run_dir: Path) -> dict:
    hooks = load_json(run_dir / "hook_session.json") or load_json(ROOT / "state" / "hook_session.json") or {}
    writes = load_json(run_dir / "code_writes.json") or []
    hooks = dict(hooks)
    hooks["extra_writes"] = [w for w in writes if isinstance(w, list) and len(w) == 3]
    return hooks


def events(run_dir: Path) -> list[dict]:
    rows = []
    for line in tail_lines(run_dir / "events.jsonl", 400):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def short_event(row: dict) -> str:
    rest = {k: v for k, v in row.items() if k not in ("t", "ts", "kind")}
    text = json.dumps(rest, default=str)
    return f"{row.get('t', '?')} {row.get('kind', '?')} {text if rest else ''}".rstrip()


def collect(run_dir: Path, why: str, died_at: float) -> Path:
    """Copy the evidence into run_dir and write CRASH.md. Returns its path."""
    info = load_json(run_dir / "run.json") or {}
    started = float(info.get("started_ts") or 0)
    out: list[str] = [
        f"# Wizard101 stopped during the bot run ({now_text(died_at)})",
        "",
        f"**What was seen:** {why}",
        "",
    ]

    # 1. The game's own crash dump.
    verdicts = []
    for name, mtime in dumps().items():
        if mtime < started - 2 or info.get("dumps_before", {}).get(name) == mtime:
            continue
        target = run_dir / f"{time.strftime('%H%M%S', time.localtime(mtime))}-{name}"
        shutil.copy2(GAME_BIN / name, target)
        try:
            text = crashdump.summarize(target.read_bytes(), hook_map(run_dir))
        except Exception as exc:
            text = f"(could not read {target.name}: {exc})"
        verdicts.append((target.name, text))
    out.append("## Crash dump")
    if verdicts:
        for name, text in verdicts:
            out += [f"`{name}`", "", "```", text, "```", ""]
    else:
        out += [
            "No new minidump. The client was closed without one (Wine killed it, it was quit, or it hung "
            "and was closed). The game log below is the next best evidence.",
            "",
        ]

    # 2. What the bot was doing.
    rows = events(run_dir)
    if rows:
        last = rows[-1]
        writes = [r for r in rows if r.get("kind") == "code_write"]
        blocked = [r for r in rows if r.get("kind") == "code_write_blocked"]
        out.append("## The bot just before")
        out.append(
            f"- last event: `{short_event(last)}` "
            f"({died_at - float(last.get('ts', died_at)):.1f}s before this was seen)"
        )
        if writes:
            w = writes[-1]
            out.append(
                f"- last write to game code: {w.get('label')} at {w.get('address')} "
                f"({died_at - float(w.get('ts', died_at)):.0f}s before)"
            )
        else:
            out.append("- no write to game code in the recent events")
        if blocked:
            out.append(f"- {len(blocked)} code write(s) were blocked by the guard (see events)")
        out += ["", "```"] + [short_event(r) for r in rows[-40:]] + ["```", ""]

    # 3. The game's log during the run.
    offset = int(info.get("game_log_offset") or 0)
    size = size_of(GAME_LOG)
    if size:
        start_at = offset if offset <= size else 0
        start_at = max(start_at, size - LOG_KEEP)
        with GAME_LOG.open("rb") as f:
            f.seek(start_at)
            data = f.read()
        (run_dir / "WizardClient.during-run.log").write_bytes(data)
        lines = data.decode("utf-8", "replace").splitlines()
        hits = [line for line in lines if INTERESTING.search(line)]
        out += (
            [
                "## Game log (WizardClient.log)",
                f"Saved as WizardClient.during-run.log ({len(data)} bytes).",
                "",
                "Last lines that mention errors:",
                "```",
            ]
            + hits[-30:]
            + ["```", "", "Last lines:", "```"]
            + lines[-25:]
            + ["```", ""]
        )

    # 4. macOS crash reports (Rosetta, wine preloader).
    found = []
    try:
        for p in DIAG.iterdir():
            if p.stat().st_mtime >= started - 2 and DIAG_NAMES.search(p.name):
                shutil.copy2(p, run_dir / p.name)
                found.append(p.name)
    except OSError:
        pass
    out += ["## macOS crash reports", ", ".join(found) if found else "none newer than the run start", ""]

    # 5. The bot's own last lines.
    out += ["## bot.log (last lines)", "```"] + tail_lines(run_dir / "bot.log", 40) + ["```", ""]
    console = tail_lines(run_dir / "console.log", 25)
    if console:
        out += ["## Wine console (last lines)", "```"] + console + ["```", ""]
    report = run_dir / "CRASH.md"
    report.write_text("\n".join(out), encoding="utf-8")
    return report


def notify(text: str) -> None:
    script = f'display notification {json.dumps(text)} with title "Wizard101 bot"'
    try:
        subprocess.run(["osascript", "-e", script], timeout=10, capture_output=True)
    except Exception:
        pass


def new_events(path: Path, offset: int) -> tuple[list[dict], int]:
    """Events appended since `offset`, and the new offset."""
    try:
        with path.open("rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n") + 1
    rows = []
    for line in data[:end].splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows, offset + end


def player_message(row: dict) -> str:
    """A notification for an event that needs the player, else ''."""
    if row.get("kind") != "needs_player":
        return ""
    why = row.get("why", "")
    if why == "backpack full":
        used, room = row.get("used"), row.get("capacity")
        return f"Backpack {used}/{room}: the bot stopped. Make room, then start it again."
    zone = row.get("zone") or "another zone"
    return f"The wizard is {why} (in {zone}). Walk it back; the bot carries on by itself."


FOCUS_REQUEST = ROOT / "state" / "focus.request"


def bring_forward(pids: list[int], run_dir: Path) -> str:
    """Make the Wizard101 game process the active Mac app (System Events; needs Accessibility)."""
    if not pids:
        return "no game process"
    script = (
        'tell application "System Events" to set frontmost of '
        f"(first process whose unix id is {pids[0]}) to true"
    )
    try:
        done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        result = "ok" if done.returncode == 0 else (done.stderr.strip() or f"exit {done.returncode}")
    except Exception as exc:
        result = str(exc)
    with (run_dir / "focus.log").open("a", encoding="utf-8") as f:
        f.write(f"{now_text()} bring Wizard101 forward (pid {pids[0]}): {result}\n")
    return result


def watch(run_dir: Path, bot_pid: int) -> int:
    info = load_json(run_dir / "run.json") or {}
    known_dumps = dict(info.get("dumps_before") or {})
    games = set(info.get("game_pids") or game_pids())
    reported = False
    offset = 0
    while True:
        if FOCUS_REQUEST.exists():
            try:
                FOCUS_REQUEST.unlink()
            except OSError:
                pass
            result = bring_forward(game_pids(), run_dir)
            if result != "ok":
                notify(
                    "The bot needs Wizard101 in front to walk. Click the game window, or allow "
                    "Terminal in System Settings > Privacy & Security > Accessibility."
                )
        rows, offset = new_events(run_dir / "events.jsonl", offset)
        for row in rows:
            message = player_message(row)
            if message:
                notify(message)
        rows = processes()
        live = set(game_pids(rows)) & games if games else set(game_pids(rows))
        why = ""
        if games and not live:
            why = "the Wizard101 process exited"
        handlers = crash_handlers(rows)
        if handlers and not why:
            why = "a crash handler started: " + "; ".join(h[:120] for h in handlers)
        fresh = {n: m for n, m in dumps().items() if known_dumps.get(n) != m}
        if fresh and not why:
            why = "the game wrote a new crash dump: " + ", ".join(fresh)
        if why and not reported:
            died = time.time()
            # Give the game a moment to finish writing its dump and log.
            time.sleep(3.0)
            report = collect(run_dir, why, died)
            reported = True
            print(f"\n*** Wizard101 stopped: {why}\n*** crash report: {report}\n", flush=True)
            notify(
                "Wizard101 stopped. Report: "
                f"{report.relative_to(ROOT) if report.is_relative_to(ROOT) else report}"
            )
        if not alive(bot_pid):
            info["ended"] = now_text()
            info["game_alive_at_end"] = bool(set(game_pids()) & games) if games else None
            info["crash_reported"] = reported
            (run_dir / "run.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
            return 0
        time.sleep(POLL)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("start", "watch", "report"):
        p = sub.add_parser(name)
        p.add_argument("--run-dir", required=True)
        if name == "watch":
            p.add_argument("--bot-pid", type=int, required=True)
    d = sub.add_parser("dump")
    d.add_argument("file")
    d.add_argument("--hooks", default=None)
    args = parser.parse_args(argv)
    if args.cmd == "dump":
        hooks = load_json(Path(args.hooks)) if args.hooks else None
        print(crashdump.summarize(Path(args.file).read_bytes(), hooks))
        return 0
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    if args.cmd == "start":
        start(run_dir)
        return 0
    if args.cmd == "report":
        print(collect(run_dir, "report rebuilt by hand", time.time()))
        return 0
    return watch(run_dir, args.bot_pid)


if __name__ == "__main__":
    sys.exit(main())
