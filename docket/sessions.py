"""Your Claude Code sessions on this computer, from their transcripts in ~/.claude/projects: title,
project, branch, when each last did something, and whether it's waiting for you. Only those fields
are read; nothing of the conversation itself. A session the Claude desktop app runs gets a link
that opens it there.
"""
import datetime as dt
import json
import re
import time
from pathlib import Path

from . import store

PROJECTS = Path.home() / ".claude/projects"
TAIL_BYTES = 600_000
# The Claude app's own record of each session it runs: it knows the transcript's id (cliSessionId) and
# opens the session from claude://code/continue?session=<its id>.
DESKTOP = Path.home() / "Library/Application Support/Claude/claude-code-sessions"
APP_ID = re.compile(r"^local_[A-Za-z0-9-]{1,64}$")
_app = {}  # record path -> (mtime, transcript id, app id, archived): the records are big, read each once


def _app_ids(since):
    """Transcript id -> the app's id, for the sessions the Claude app runs (not archived, touched since)."""
    out = {}
    for path in DESKTOP.glob("*/*/local_*.json"):
        try:
            mtime = path.stat().st_mtime
            if mtime < since:
                continue
            if _app.get(path, (None,))[0] != mtime:
                d = json.loads(path.read_text())
                _app[path] = (mtime, d.get("cliSessionId"), d.get("sessionId"), bool(d.get("isArchived")))
        except (OSError, ValueError, AttributeError):
            continue
        _, transcript, app, archived = _app[path]
        if transcript and isinstance(app, str) and APP_ID.match(app) and not archived:
            out[transcript] = app
    return out


def recent(days=2):
    """The sessions active in the last `days` days: title, project, branch, when it last did something,
    and its state:
      waiting: it finished its turn, so it's your move
      running: in the middle of a turn, active in the last 10 minutes
      stopped: in the middle of a turn but quiet since (interrupted, or the app was closed)
    Sessions whose folder matches settings' "agent_paths" (e.g. where your background agents run) are
    marked agent=True and only counted. A session the Claude app runs has a link that opens it there."""
    start = time.time() - days * 86400
    app = _app_ids(start)
    agents = [p for p in store.settings().get("agent_paths", []) if isinstance(p, str) and p]
    out = []
    for path in PROJECTS.glob("*/*.jsonl"):
        try:  # a transcript can vanish or be unreadable mid-listing: skip it, keep the rest
            st = path.stat()
            if st.st_mtime < start:
                continue
            with open(path, "rb") as f:
                f.seek(max(0, st.st_size - TAIL_BYTES))
                lines = f.read().decode("utf-8", errors="replace").splitlines()[1:]
        except OSError:
            continue
        title = cwd = branch = None
        last = None  # the last user/assistant record
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            title = e.get("customTitle") or title
            cwd = e.get("cwd") or cwd
            branch = e.get("gitBranch") or branch
            if e.get("type") in ("user", "assistant") and not e.get("isMeta"):
                last = e
        when = st.st_mtime
        try:
            when = dt.datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00")).timestamp()
        except (TypeError, KeyError, ValueError, AttributeError):
            pass
        if when < start:
            continue
        msg = (last or {}).get("message") or {}
        if last and last.get("type") == "assistant" and msg.get("stop_reason") == "end_turn":
            state = "waiting"
        elif time.time() - when < 10 * 60:
            state = "running"
        else:
            state = "stopped"
        out.append({"title": (title or "untitled")[:120], "project": Path(cwd).name if cwd else path.parent.name,
                    "branch": (branch or "")[:80], "at": int(when), "state": state,
                    "agent": any(p in (cwd or "") for p in agents),
                    "link": f"claude://code/continue?session={app[path.stem]}" if path.stem in app else ""})
    return sorted(out, key=lambda s: s["at"], reverse=True)[:60]
