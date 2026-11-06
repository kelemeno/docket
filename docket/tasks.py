"""The task list: one list that carries over from day to day, kept current by the refresh
(app.py has Claude read what's new and hands it here) and by what you do on the page.

The rules: your changes always win (a field you set is listed in "mine" and the refresh leaves it
alone); a task is done only with evidence (a draft isn't sent, an approval isn't signed, silence
isn't done); a closed task stays closed unless you reopen it; an overdue task rolls to today and
counts how often it slipped.

    tasks.json   {"since", "refreshed", "rolled_on", "meetings": {"day", "items"}, "order": [task ids], "tasks": [...]}

A task: id, title, next (the very next step), why, who (who's waiting), kind quick|deep, priority
P0|P1|P2, status open|waiting|done|dropped, do (the day to work on it, not the deadline), until
(YYYY-MM-DDTHH:MM: snoozed for a while, e.g. 30 minutes while an agent runs), rolled,
sources [{label, link}], sessions [Claude Code session titles], created, updated, done_at, evidence,
closed_by, mine, notes [{at, by you|claude, text}] (what you told Claude about the task and its
answers), asked (when your latest note is still waiting for one), review (for a review asked of you:
the PR's link; the task closes once gh shows you approved it).
"""
import datetime as dt
import re
import threading
import uuid

from . import store

LOCK = threading.Lock()  # read-modify-write of the file, between the refresh and the page
PRIORITY = ("P0", "P1", "P2")
KIND = ("quick", "deep")
OPEN = ("open", "waiting")
CLOSED = ("done", "dropped")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MOMENT = re.compile(r"^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d$")
KEEP_CLOSED_DAYS = 14
TEXT = {"title": 160, "next": 240, "why": 240, "who": 80}
KEEP_NOTES = 12
ESTIMATES = {"15", "30", "60", "120", "240"}
PR = re.compile(r"^https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)", re.I)
ASKS_REVIEW = ("review", "re-review", "approve")  # how a task from before the "review" field is titled


def extra(item, current):
    """Fields of the host app's own on a task or meeting, from what Claude returned (`item`) and the
    task as it is (`current`, None for a new one). Docket has none; an app replaces this function."""
    return {}


def path():
    return store.HOME / "tasks.json"


def load():
    reg = store.read(path()) or {}
    reg.setdefault("tasks", [])
    for t in reg["tasks"]:  # lists from before one link per place
        t["sources"] = _merge([], t.get("sources") or [])
    return reg


def save(reg):
    store.write(path(), reg)


def _text(v, n, words=True):
    """One line of at most n characters; a longer one is cut at a word, with "…" (a link, words=False,
    just where it ends)."""
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    if len(s) <= n or not words:
        return s[:n]
    cut = s[:n - 1]
    return (cut[:cut.rindex(" ")] if " " in cut[n // 2:] else cut).rstrip(" ,;:-–") + "…"


def _sources(items):
    out = []
    for s in (items or [])[:6] if isinstance(items, list) else []:
        if isinstance(s, dict):
            link = _text(s.get("link"), 400, words=False)
            out.append({"label": _text(s.get("label"), 80) or "source",
                        "link": link if link.startswith("https://") else ""})  # the page makes these <a href>
    return _merge([], out)


# A Slack message as Slack links it, under any of the workspace's host names: its channel, its
# message, and the thread it's in.
SLACK = re.compile(r"^https://[^/]+\.slack\.com/archives/([A-Z0-9]+)/p(\d+)(?:.*[?&]thread_ts=([\d.]+))?")


def _key(link):
    """The same message under another host name is the same link."""
    m = SLACK.match(link or "")
    return f"slack:{m[1]}:{m[2]}" if m else link


def _place(s):
    """Where a link points: a Slack conversation (a DM, or a thread) is one place, whichever of its
    messages is linked; anything else is its link."""
    m = SLACK.match(s["link"] or "")
    return f"slack:{m[1]}:{m[3] or ''}" if m else s["link"] or s["label"]


def _merge(old, new):
    """Sources one per place: a newer link to a place already there takes the old one's spot (and keeps
    its label), so a DM that keeps going is one link, to its latest message."""
    out = list(old)
    for s in new:
        i = next((i for i, o in enumerate(out) if _place(o) == _place(s)), None)
        if i is None:
            out.append(s)
        else:
            out[i] = {**out[i], "link": s["link"] or out[i]["link"]}
    return out


def pr_key(link):
    """The PR a GitHub link points at, as "owner/repo#n" (lowercase); None for any other link."""
    m = PR.match(str(link or ""))
    return f"{m.group(1).lower()}#{m.group(2)}" if m else None


def _review(n):
    return {"review": _text(n["review"], 400, words=False)} if isinstance(n, dict) and pr_key(n.get("review")) else {}


def _new(n, today, now):
    return {"id": uuid.uuid4().hex[:8], **{k: _text(n.get(k), lim) for k, lim in TEXT.items()},
            "kind": n.get("kind") if n.get("kind") in KIND else "deep",
            "priority": n.get("priority") if n.get("priority") in PRIORITY else "P2",
            "status": "waiting" if n.get("status") == "waiting" else "open",
            "do": n["do"] if isinstance(n.get("do"), str) and DAY.match(n["do"]) and n["do"] >= today else today,
            "rolled": 0, "sources": _sources(n.get("sources")), "sessions": _titles(n.get("sessions")),
            "created": now, "updated": now, "mine": [], **_review(n), **extra(n, None)}


def _titles(items):
    return [t for t in (_text(x, 120) for x in (items if isinstance(items, list) else [])[:5]) if t]


def _duplicate(tasks, n):
    """Already on the list, open or closed: a task you dropped or finished doesn't come back as new."""
    title = n["title"].casefold()
    links = {_key(s["link"]) for s in n["sources"] if s["link"]}
    return any(t["title"].casefold() == title or links & {_key(s["link"]) for s in t["sources"] if s["link"]} for t in tasks)


def meetings(items):
    out = []
    """Today's meetings in this Mac's time. Claude passes the calendar's own times (with their offset)
    and the conversion happens here: models get time zones wrong, Python doesn't."""
    for m in (items if isinstance(items, list) else [])[:20]:
        start, end = (store.local_hhmm(m.get(k)) for k in ("start", "end")) if isinstance(m, dict) else (None, None)
        if start and end:
            link = str(m.get("link") or "").strip()
            out.append({"start": start, "end": end, "title": _text(m.get("title"), 160),
                        **({"link": link} if re.match(r"https://\S+$", link) else {}), **extra(m, None)})
    return sorted(out, key=lambda m: m["start"])


def merge(reg, out, today, now):
    """Folds one refresh into the list: updates first (never a closed task or a field you set), then
    new tasks that aren't already there, then today's meetings."""
    by_id = {t["id"]: t for t in reg["tasks"]}
    for u in (out.get("updates") or []) if isinstance(out, dict) else []:
        t = by_id.get(str(u.get("id"))) if isinstance(u, dict) else None
        if t and t["status"] not in CLOSED:
            _update(t, u, set(t.get("mine") or []), today, now)
    for n in (out.get("new") or []) if isinstance(out, dict) else []:
        if not isinstance(n, dict) or not _text(n.get("title"), 160):
            continue
        task = _new(n, today, now)
        if not _duplicate(reg["tasks"], task):
            reg["tasks"].append(task)
    if isinstance(out, dict) and isinstance(out.get("meetings"), list):
        reg["meetings"] = {"day": today, "items": meetings(out["meetings"])}
    return reg


def _update(t, u, mine, today, now):
    """One update from Claude; the fields in `mine` stay as they are."""
    if u.get("status") == "done":
        evidence = _text(u.get("evidence"), 240)
        if evidence and "status" not in mine:
            t.update(status="done", done_at=now, evidence=evidence, closed_by="claude")
    elif u.get("status") in OPEN and "status" not in mine:
        t["status"] = u["status"]
    for k, lim in TEXT.items():
        if _text(u.get(k), lim) and k not in mine:
            t[k] = _text(u.get(k), lim)
    if u.get("kind") in KIND and "kind" not in mine:
        t["kind"] = u["kind"]
    if u.get("priority") in PRIORITY and "priority" not in mine:
        t["priority"] = u["priority"]
    if isinstance(u.get("do"), str) and DAY.match(u["do"]) and u["do"] >= today and "do" not in mine:
        t["do"] = u["do"]
    t["sources"] = _merge(t["sources"], _sources(u.get("sources")))[:6]
    t["sessions"] = list(dict.fromkeys(t.get("sessions", []) + _titles(u.get("sessions"))))[:5]
    t.update(_review(u))
    t.update(extra(u, t))
    t["updated"] = now


def reviewed(reg, approved, now):
    """Closes the review tasks whose PR gh shows you approved: `approved` is {pr_key: when you approved
    it}, your latest review being an approval with no commits since. A review task is one the refresh
    gave a "review" link, or one from before that titled Review…/Approve… that links one PR. Not one
    whose status you set yourself, nor your agent's or Linear's (aag and linear.py have the say on
    those). Returns whether anything changed."""
    changed = False
    for t in reg["tasks"]:
        if t["status"] not in OPEN or t.get("aag") or t.get("linear") or "status" in (t.get("mine") or []):
            continue
        key = pr_key(t.get("review"))
        if not key and t["title"].casefold().startswith(ASKS_REVIEW):
            keys = {pr_key(s["link"]) for s in t["sources"]} - {None}
            key = keys.pop() if len(keys) == 1 else None
        if key in approved:
            t.update(status="done", done_at=now, closed_by="gh", updated=now,
                     evidence=f"You approved {key} on GitHub{_at(approved[key], now)}")
            changed = True
    return changed


def _at(stamp, now):
    """" at 11:43" on this computer's clock (with the day, when it's not today); "" if it's no time."""
    try:
        t = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""
    return t.strftime(" at %H:%M" if t.date().isoformat() == now[:10] else " on %Y-%m-%d at %H:%M")


def roll(reg, today):
    """Once a day: open tasks from earlier days move to today and count the slip; closed ones older
    than two weeks are dropped from the file. Returns whether anything changed."""
    if reg.get("rolled_on") == today:
        return False
    for t in reg["tasks"]:
        if t["status"] in OPEN and t.get("do", today) < today:
            t["do"] = today
            if t["status"] == "open":
                t["rolled"] = t.get("rolled", 0) + 1
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=KEEP_CLOSED_DAYS)).isoformat()
    reg["tasks"] = [t for t in reg["tasks"] if t["status"] in OPEN or (t.get("done_at") or today) >= cutoff]
    reg["rolled_on"] = today
    return True


def add(title, kind, today, now):
    """A task you type in yourself: it's all yours, so the refresh only adds links and sessions to it."""
    title = _text(title, 160)
    if not title:
        raise ValueError("a task needs a title")
    with LOCK:
        reg = load()
        task = _new({"title": title, "kind": kind if kind in KIND else "quick", "priority": "P1",
                     "sources": [{"label": "added by you"}]}, today, now)
        task["mine"] = ["title", "kind", "priority", "do", "status"]
        reg["tasks"].append(task)
        save(reg)
        return reg


def act(task_id, action, value, today, now):
    """What you do on the page. Raises KeyError / ValueError on a bad id or value."""
    with LOCK:
        reg = load()
        t = next((t for t in reg["tasks"] if t["id"] == task_id), None)
        if not t:
            raise KeyError(task_id)
        mine = set(t.get("mine") or [])
        if action in ("done", "drop"):
            t.update(status="done" if action == "done" else "dropped", done_at=now, closed_by="you", evidence="")
            mine.add("status")
        elif action in ("reopen", "waiting", "open"):
            t.update(status="waiting" if action == "waiting" else "open", done_at=None, closed_by=None)
            t.pop("until", None)
            mine.add("status")
        elif action == "snooze":  # a day, or a time today or later ("for 30 minutes")
            if isinstance(value, str) and MOMENT.match(value) and value >= now[:16]:
                t["do"], t["until"] = value[:10], value
            elif isinstance(value, str) and DAY.match(value) and value >= today:
                t["do"] = value
                t.pop("until", None)
            else:
                raise ValueError("snooze needs a day from today on, or a time from now on")
            mine.add("do")
        elif action == "order":  # the order you'll do things in (the day's plan, page.js): the whole list
            if not isinstance(value, list):
                raise ValueError("order needs a list of task ids")
            ids = {x["id"] for x in reg["tasks"]}
            reg["order"] = list(dict.fromkeys(i for i in value if i in ids))[:200]
            save(reg)
            return reg
        elif action == "est":  # how long you think it takes, in minutes (the day's plan sizes it so)
            if str(value) not in ESTIMATES:
                raise ValueError("bad estimate")
            t["est"] = int(value)
            mine.add("est")
        elif action in ("priority", "kind"):
            if value not in (PRIORITY if action == "priority" else KIND):
                raise ValueError(f"bad {action}")
            t[action] = value
            mine.add(action)
        else:
            raise ValueError(f"unknown action {action}")
        t["mine"], t["updated"] = sorted(mine), now
        save(reg)
        return reg


def note(task_id, text, now):
    """Something you tell Claude about a task ("ask Sam for a review in the team thread"). It waits
    for Claude's answer (app.Page.reply), and the refresh reads it too."""
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()[:600]
    if not text:
        raise ValueError("the note is empty")
    with LOCK:
        reg = load()
        t = next((t for t in reg["tasks"] if t["id"] == task_id), None)
        if not t:
            raise KeyError(task_id)
        t["notes"] = (t.get("notes") or [])[-(KEEP_NOTES - 1):] + [{"at": now, "by": "you", "text": text}]
        t["asked"], t["updated"] = now, now
        save(reg)
        return t


def answer(task_id, out, today, now):
    """Claude's answer to your note: its reply goes under the note, and the task changes the way you
    asked, even fields you set yourself (you asked), and even back open if it was closed. A done
    still needs evidence. Tasks it adds (a follow-up the note mentions) are merged like a refresh's."""
    with LOCK:
        reg = load()
        t = next((t for t in reg["tasks"] if t["id"] == task_id), None)
        if not t:
            return reg
        u = out.get("update") if isinstance(out, dict) and isinstance(out.get("update"), dict) else {}
        if t["status"] in CLOSED and u.get("status") in OPEN:
            t.update(done_at=None, closed_by=None)
        if t["status"] not in CLOSED or u.get("status") in OPEN:
            _update(t, u, set(), today, now)
        reply = _text(out.get("answer"), 600) if isinstance(out, dict) else ""
        t["notes"] = (t.get("notes") or [])[-(KEEP_NOTES - 1):] + [{"at": now, "by": "claude", "text": reply or "Done."}]
        t.pop("asked", None)
        if isinstance(out, dict) and out.get("new"):
            merge(reg, {"new": out["new"]}, today, now)
        save(reg)
        return reg


def unasked(task_id, error, now):
    """Claude couldn't answer: say so under the note, so the page stops waiting."""
    with LOCK:
        reg = load()
        t = next((t for t in reg["tasks"] if t["id"] == task_id), None)
        if t:
            t["notes"] = (t.get("notes") or []) + [{"at": now, "by": "claude", "text": f"Couldn't answer: {_text(error, 200)}"}]
            t.pop("asked", None)
            save(reg)


def order(tasks, today):
    """Open tasks due by today: P0 first, then the ones that slipped most, then the oldest."""
    due = [t for t in tasks if t["status"] == "open" and t.get("do", today) <= today]
    return sorted(due, key=lambda t: (PRIORITY.index(t.get("priority", "P2")), -t.get("rolled", 0), t.get("created", "")))
