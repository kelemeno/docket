"""Your agent's work on the list: aag (auto-agents) writes and reviews PRs for you from Slack, and every
minute Docket reads what it has (`aag status --json`, on this computer or over SSH where aag runs on a
server). A task whose next move is yours is open in the list, linked to its DM thread and its PR; one
the agent works on, or that waits on others, is waiting; a finished one is handled, and so is a review
once the agents agree (approving and merging it is the author's and the humans' to do). The side panel's
"Your agent" card has the rest: what each agent is doing now, and where each PR stands.

aag keeps these tasks: the hourly refresh leaves them alone and skips what they cover (your DMs with
the bot, the PRs aag tracks), and a task leaves the list when aag stops listing it. Your snooze,
priority and kind on one stay yours; a done or drop stays until its next move changes.

    settings.json   "aag": "local"       runs `aag status --json` here
                    "aag": "ssh HOST"    runs it on HOST (BatchMode, so it never asks for a password)
                    "aag": "ssh HOST CMD"  runs CMD status --json there, when aag isn't on SSH's PATH
"""
import datetime as dt
import json
import re
import subprocess
import sys
import threading
import time

from . import app, store, tasks

EVERY = 60
TIMEOUT = 30
STATUS = ("status", "done_at", "closed_by", "evidence")  # what your done or drop sets
_feed = {"feed": None, "at": None, "error": None, "since": None}
_lock = threading.Lock()
_known = set()  # the list's task ids at the last read: what a refresh adds is the rest
_rules = ""  # app.RULES before this module added its own


def command(setting):
    """The command that reads aag, from the "aag" setting; None when it's not set."""
    words = str(setting or "").split()
    if words == ["local"]:
        aag = app._which("aag")
        if not aag:
            raise RuntimeError("no `aag` command here (looked on PATH and in ~/.local/bin)")
        return [aag, "status", "--json"]
    if len(words) >= 2 and words[0] == "ssh":
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", words[1], *(words[2:] or ["aag"]),
                "status", "--json"]
    if words:
        raise RuntimeError(f'"aag" is "local" or "ssh HOST", not {setting!r}')
    return None


def read(setting):
    """aag's feed (its shape: StatusJson in aag's src/views.ts). Raises when it can't be had."""
    r = subprocess.run(command(setting), env=app._env(), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}")
    try:
        feed = json.loads(r.stdout)
    except ValueError:
        feed = None
    if not isinstance(feed, dict) or feed.get("v") != 1 or not isinstance(feed.get("tasks"), list):
        raise RuntimeError("that's not aag's feed: an aag from before `aag status --json`? (`aag update`)")
    return feed


def _local(stamp):
    """An ISO time as Docket writes its own: this computer's clock, to the second, no offset."""
    try:
        return dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone().replace(tzinfo=None) \
            .isoformat(timespec="seconds")
    except ValueError:
        return None


def finished(a):
    """Done for you: closed in aag, or a review the agents agree on (aag waits on the humans for it,
    unless it asks for your approval: then it's your move)."""
    return a.get("column") == "done" or (a.get("role") == "reviewer" and a.get("status") == "agents_done"
                                         and a.get("column") != "needs_you")


def _fields(a, me):
    """What aag says about one of its tasks, as Docket's fields."""
    pr, column = a.get("pr") or {}, a.get("column")
    done = finished(a)
    sources = [{"label": "DM thread", "link": a.get("dm")}, {"label": pr.get("key"), "link": pr.get("url")},
               {"label": "agents thread", "link": a.get("thread")}]
    return {"title": tasks._text(a.get("title"), 160), "next": tasks._text(a.get("next"), 240),
            "why": tasks._text(re.sub(r" \(\d+[mhd]\)$", "", str(a.get("line") or "")), 240),  # not its age: no news
            "who": tasks._text(a.get("requestedBy"), 80) if a.get("requestedBy") != me else "",
            "status": "done" if done else "open" if column == "needs_you" else "waiting",
            "done_at": _local(a.get("statusAt") or a.get("updatedAt")) if done else None,  # closed, or reviewed, then
            "closed_by": "aag" if done else None, "evidence": tasks._text(a.get("next"), 240) if done else "",
            "sources": tasks._sources([s for s in sources if s["label"] and s["link"]])}


def sync(reg, feed, today, now):
    """Puts aag's tasks on the list as aag has them now (ids "aag-…"), and takes off the ones it no
    longer lists. One that finished before today isn't added. Returns whether the list changed."""
    me, before = feed.get("me"), json.dumps(reg["tasks"], sort_keys=True)
    listed = {str(a["id"]): a for a in feed["tasks"] if isinstance(a, dict) and a.get("id") and a.get("title")}
    kept = []
    for t in reg["tasks"]:
        if not t.get("aag"):
            kept.append(t)
        elif t["aag"]["id"] in listed:
            _apply(t, listed.pop(t["aag"]["id"]), me, now)
            kept.append(t)
    for a in listed.values():
        f = _fields(a, me)
        if f["status"] == "done" and not str(f["done_at"]).startswith(today):
            continue
        t = {"id": f"aag-{a['id']}", "kind": "deep" if a.get("phase") == "owner_review" else "quick", "priority": "P1",
             "do": today, "rolled": 0, "sessions": [], "created": now, "updated": now, "mine": []}
        _apply(t, a, me, now)
        kept.append(t)
    reg["tasks"] = kept
    return json.dumps(kept, sort_keys=True) != before


def _apply(t, a, me, now):
    f, move = _fields(a, me), f"{a.get('column')}: {a.get('next')}"
    mine = set(t.get("mine") or [])
    if "status" in mine and t.get("aag", {}).get("move") != move:
        mine.discard("status")  # your done or drop was about the move before: aag has the say again
    new = {k: v for k, v in f.items() if not ("status" in mine and k in STATUS)}
    if any(t.get(k) != v for k, v in new.items()):
        t.update(new, updated=now)
    t["mine"] = sorted(mine)
    t["aag"] = {"id": str(a["id"]), "move": move, **f}  # what extra() puts back after the refresh


def extra(item, current):
    """For tasks.extra: an aag task stays as aag says through the refresh and the answers to your
    notes. What Claude changed on one is put back, except what you set yourself."""
    a = (current or {}).get("aag")
    if not a:
        return {}
    mine = set(current.get("mine") or [])
    return {k: a[k] for k in ("title", "next", "why", "who", "sources", *STATUS)
            if k in a and k not in mine and not ("status" in mine and k in STATUS)}


def covers(feed, link):
    """A link to what aag already tracks: one of its PRs, or a message in your DM with the bot."""
    prs = {(a.get("pr") or {}).get("url") for a in feed["tasks"] if isinstance(a, dict)} - {None}
    dm = feed.get("dm")
    return any(link == u or link.startswith(u + "/") or link.startswith(u + "#") for u in prs) \
        or bool(dm and f"/archives/{dm}/" in link)


def rules(feed):
    """For app.RULES: what the hourly refresh leaves to aag."""
    prs = sorted({(a.get("pr") or {}).get("key") for a in feed["tasks"] if isinstance(a, dict)} - {None})
    text = ("Tasks with an id starting aag- come from {who}'s agent (aag), which keeps them current: leave them out "
            "of \"updates\". Don't add tasks for what aag already covers: "
            + (f"Slack messages in {{who}}'s DM with the agent's bot (channel {feed['dm']}), " if feed.get("dm") else
               "Slack DMs from {who}'s agent's bot, ")
            + "the agents' threads it links, "
            + (f"or these PRs: {', '.join(prs)}." if prs else "or PRs it works on."))
    return text.replace("{who}", "\0").replace("{", "{{").replace("}", "}}").replace("\0", "{who}")


def after_refresh(_reg):
    """For app.AFTER_REFRESH: a task the refresh added anyway about what aag covers is taken off again
    (read again under the lock: the page may have changed the list since)."""
    with _lock:
        feed = _feed["feed"]
    if not feed:
        return
    with tasks.LOCK:
        reg = tasks.load()
        added = [t for t in reg["tasks"] if t["id"] not in _known and not t.get("aag") and "title" not in t.get("mine", [])]
        drop = {t["id"] for t in added if (links := [s["link"] for s in t["sources"] if s["link"]])
                and all(covers(feed, x) for x in links)}
        if drop:
            reg["tasks"] = [t for t in reg["tasks"] if t["id"] not in drop]
            tasks.save(reg)
            print(f"aag: took {len(drop)} task(s) about aag's work off the refresh", file=sys.stderr)


def poll():
    """Reads aag once and puts its tasks on the list. On a failure the last ones stay, with the error
    and since when it fails."""
    global _known
    s = store.settings()
    if not s.get("aag"):  # turned off: its tasks go too
        app.RULES = _rules
        with _lock:
            _feed.update(feed=None, at=None, error=None, since=None)
        with tasks.LOCK:
            reg = tasks.load()
            if any(t.get("aag") for t in reg["tasks"]):
                reg["tasks"] = [t for t in reg["tasks"] if not t.get("aag")]
                tasks.save(reg)
        return
    try:
        feed = read(s["aag"])
    except Exception as e:
        with _lock:
            _feed.update(error=str(e)[-200:], since=_feed["since"] or store.now())
        return
    with tasks.LOCK:
        reg = tasks.load()
        if sync(reg, feed, store.today(), store.now()):
            tasks.save(reg)
        _known = {t["id"] for t in reg["tasks"]}
    with _lock:
        _feed.update(feed=feed, at=store.now(), error=None, since=None)
    app.RULES = "\n\n".join(x for x in (_rules, rules(feed)) if x)
    # the agents' own Claude sessions run in aag's workdir: counted, not listed
    work = feed.get("workdir")
    if s["aag"] == "local" and work and work not in s.get("agent_paths", []):
        store.write(store.SETTINGS, {**s, "agent_paths": [*s.get("agent_paths", []), work]})


def state():
    """For app.STATE: the "Your agent" card's data, while the "aag" setting is there."""
    setting = store.settings().get("aag")
    if not setting:
        return {}
    with _lock:
        return {"aag": {**_feed, "where": setting}}


def start(wait=10):
    """From `docket run`: the hooks, and a read of aag every minute while settings.json has "aag".
    Returns after the first read (at most `wait` seconds): the first refresh starts right after, and
    it should know what aag covers."""
    global _rules
    _rules = app.RULES
    shown, kept = app.STATE, tasks.extra
    app.STATE = lambda: {**shown(), **state()}
    tasks.extra = lambda item, current: {**kept(item, current), **extra(item, current)}
    app.AFTER_REFRESH.append(after_refresh)
    first = threading.Event()

    def loop():
        while True:
            try:
                poll()
            except Exception as e:  # a bad tasks.json, say: the next minute tries again
                print(f"aag: {e!r}", file=sys.stderr)
            first.set()
            time.sleep(EVERY)
    threading.Thread(target=loop, daemon=True).start()
    first.wait(wait)
