"""The game's own log (WizardClient.log), read for what memory does not say
plainly: the server logged the wizard out.

10-22 17:50 (run 20261022-161220-seeds): keys had not reached the game for
~35 min, the server sent MSG_Disconnect_Afk ("You have been disconnected due
to inactivity."), and the bot waited for boars at the login screen for two
hours. AppCloseConnection alone is not a logout: every zone change logs it.
"""

from __future__ import annotations

from pathlib import Path

from .navmesh import game_data_dir

MARKS = ("MSG_Disconnect", "msg string key = GUI_Connection")
READ_MAX = 4 << 20  # at most this much new log per poll (the game can log ~60 lines a second)


def game_log_path() -> Path | None:
    data = game_data_dir()  # .../Wizard101/Data/GameData
    if data is None:
        return None
    return data.parent.parent / "Bin" / "WizardClient.log"


def disconnect_line(text: str) -> str | None:
    """The first line that says the server dropped the wizard, without its timestamp."""
    for line in text.splitlines():
        if any(mark in line for mark in MARKS):
            return line[18:].strip() if line[:2].isdigit() else line.strip()
    return None


class LogWatch:
    """Reads only what the game logs after the watch starts."""

    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else game_log_path()
        self.offset = self._size()

    def _size(self) -> int:
        try:
            return self.path.stat().st_size if self.path else 0
        except OSError:
            return 0

    def poll(self) -> str | None:
        """A disconnect line logged since the last poll, or None."""
        size = self._size()
        if size < self.offset:
            self.offset = 0  # the game was started again and rewrote its log
        if size == self.offset or self.path is None:
            return None
        start = max(self.offset, size - READ_MAX)
        try:
            with self.path.open("rb") as f:
                f.seek(start)
                data = f.read(size - start)
        except OSError:
            return None
        self.offset = size
        return disconnect_line(data.decode("utf-8", errors="replace"))
