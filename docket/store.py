"""Where Docket keeps its files, and the clock it reads.

Everything lives in one folder, readable only by you: $DOCKET_HOME, or ~/.local/share/docket.

    settings.json   {"tools": [...], "refused": [...], "who": "Sam", "every_min": 15, "model": "sonnet"}
    tasks.json      the task list (tasks.py)
"""
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path

HOME = Path(os.environ.get("DOCKET_HOME") or Path.home() / ".local/share/docket")
SETTINGS = HOME / "settings.json"


def configure(home=None, settings=None):
    """For an app that carries Docket inside it: its own folder for tasks.json, and its own settings file."""
    global HOME, SETTINGS
    if home:
        HOME = Path(home)
        SETTINGS = HOME / "settings.json"
    if settings:
        SETTINGS = Path(settings)
HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def write(path, data):
    """Whole or not at all, and only for you: the file is 0600, and Docket's own folder 0700."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent == HOME:
        path.parent.chmod(0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def settings():
    return read(SETTINGS) or {}


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def today():
    return dt.date.today().isoformat()


def local_hhmm(stamp):
    """HH:MM on this computer's clock for a time as a calendar gives it ("2026-03-02T14:20:00Z", or with
    an offset). A bare HH:MM is taken as local already. None if it's neither."""
    stamp = str(stamp or "").strip()
    if HHMM.match(stamp):
        return stamp.zfill(5)
    try:
        t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))  # 3.9 doesn't read Z
    except ValueError:
        return None
    return (t.astimezone() if t.tzinfo else t).strftime("%H:%M")
