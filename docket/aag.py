"""Your agent's work on the list: aag (auto-agents) writes and reviews PRs for you from Slack. Docket
has it from two places, and they make one list:

- Slack, always: your agent DMs you in one thread per task, and the refresh reads those threads like
  any other DM, so their asks become tasks wherever aag runs.
- aag's own feed, when it can be read: `aag status --json` every minute, on this computer (found by
  itself) or over SSH where aag runs on a server. A task whose next move is yours is open, linked to
  its DM thread and its PR; one the agent works on, or that waits on others, is waiting; a finished one
  is handled, and so is a review once the agents agree (approving and merging it is the author's and
  the humans' to do). The side panel's "Your agent" card has the rest: what each agent is doing now,
  and where each PR stands.

The two meet in the DM thread: one task per thread. While the feed can be read it has the say on a
task's state (status, title, next step, who's waiting), the refresh adds what the thread says (why) and
its links, and a task leaves the list when aag stops listing it. While it can't, the refresh keeps
them from the threads. Your snooze, priority and kind on one stay yours; a done or drop stays until its
next move changes.

    settings.json   "aag" unset          the aag on this computer, if there is one
                    "aag": "local"       the same, and say so when there's none
                    "aag": "ssh HOST"    the one on HOST (BatchMode, so it never asks for a password)
                    "aag": "ssh HOST CMD"  runs CMD status --json there, when aag isn't on SSH's PATH
                    "aag": "off"         no feed, and the refresh reads the agent's DMs as any others
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
THREAD = re.compile(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})")
_feed = {"feed": None, "at": None, "error": None, "since": None}
_lock = threading.Lock()
_known = set()  # the list's task ids at the last read: what a refresh adds is the rest
_rules = ""  # app.RULES before this module added its own


def command(setting):
    """The command that reads aag, from the "aag" setting; None for "off", and when it's unset and
    there's no aag here."""
    words = str(setting or "").split()
    if words in ([], ["local"]):
        aag = app._which("aag")
        if aag:
            return [aag, "status", "--json"]
        if words:
            raise RuntimeError("no `aag` command here (looked on PATH and in ~/.local/bin)")
        return None
    if len(words) >= 2 and words[0] == "ssh":
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", words[1], *(words[2:] or ["aag"]),
                "status", "--json"]
    if words == ["off"]:
        return None
    raise RuntimeError(f'"aag" is "local", "ssh HOST" or "off", not {setting!r}')


def read(cmd):
    """aag's feed (its shape: StatusJson in aag's src/views.ts). Raises when it can't be had."""
    r = subprocess.run(cmd, env=app._env(), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}")
    try:
        feed = json.loads(r.stdout)
    except ValueError:
        feed = None
    if not isinstance(feed, dict) or feed.get("v") != 1 or not isinstance(feed.get("tasks"), list):
        raise RuntimeError("that's not aag's feed: an aag from before `aag status --json`? (`aag update`)")
    return feed


def live():
    """The feed was read at the last try."""
    with _lock:
        return bool(_feed["feed"]) and not _feed["error"]


def thread(link):
    """The Slack thread a link is in, as (channel, the thread's ts), whatever the workspace's host name
    (one workspace has several); None for any other link."""
    m = THREAD.search(str(link or ""))
    if not m:
        return None
    root = re.search(r"[?&]thread_ts=(\d+\.\d+)", link)
    return m.group(1), root.group(1) if root else f"{m.group(2)}.{m.group(3)}"


def _same(link):
    """What a link points at, whatever the host: a Slack message, or the link itself."""
    m = THREAD.search(str(link or ""))
    return (m.group(1), m.group(2) + m.group(3)) if m else link


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
    """Puts aag's tasks on the list as aag has them now, and takes off the ones it no longer lists. A
    task the refresh made from a DM thread (while the feed couldn't be read) becomes that thread's aag
    task; a new one is "aag-…", unless it finished before today. Returns whether the list changed."""
    me, before = feed.get("me"), json.dumps(reg, sort_keys=True)
    listed = {str(a["id"]): a for a in feed["tasks"] if isinstance(a, dict) and a.get("id") and a.get("title")}
    kept, made = [], {}  # made: a DM thread -> the refresh's task for it
    for t in reg["tasks"]:
        if not t.get("aag"):
            kept.append(t)
            for k in {thread(s["link"]) for s in t["sources"]} - {None} if "title" not in t.get("mine", []) else ():
                made.setdefault(k, t)  # not one you wrote yourself
        elif t["aag"]["id"] in listed:
            _apply(t, listed.pop(t["aag"]["id"]), me, now)
            kept.append(t)
    for a in listed.values():
        t = made.pop(thread(a.get("dm")), None)
        if t is not None and not t.get("aag"):
            _apply(t, a, me, now)
            continue
        f = _fields(a, me)
        if f["status"] == "done" and not str(f["done_at"]).startswith(today):
            continue
        t = {"id": f"aag-{a['id']}", "kind": "deep" if a.get("phase") == "owner_review" else "quick", "priority": "P1",
             "do": today, "rolled": 0, "sessions": [], "created": now, "updated": now, "mine": []}
        _apply(t, a, me, now)
        kept.append(t)
    reg["tasks"] = kept
    if feed.get("dm"):
        reg["aag_dm"] = feed["dm"]  # the DM channel, for the refresh when the feed can't be read
    return json.dumps(reg, sort_keys=True) != before


def _apply(t, a, me, now):
    """aag's word on one of its tasks. Its why is aag's state line until the refresh wrote what the DM
    thread says; the links the refresh added stay."""
    f, move, was = _fields(a, me), f"{a.get('column')}: {a.get('next')}", t.get("aag") or {}
    ours = f["sources"]
    if t.get("why") and t.get("why") != was.get("why"):
        f.pop("why")  # the refresh wrote it, from the thread
    keys = {_same(s["link"]) for s in ours}
    f["sources"] = (ours + [s for s in t.get("sources", []) if s not in was.get("sources", [])
                            and _same(s["link"]) not in keys])[:6]
    mine = set(t.get("mine") or [])
    if "status" in mine and was.get("move") != move:
        mine.discard("status")  # your done or drop was about the move before: aag has the say again
    new = {k: v for k, v in f.items() if not ("status" in mine and k in STATUS)}
    if any(t.get(k) != v for k, v in new.items()):
        t.update(new, updated=now)
    t["mine"] = sorted(mine)
    t["aag"] = {**_fields(a, me), "id": str(a["id"]), "move": move, "sources": ours}  # what aag said: extra() puts it back


def extra(item, current):
    """For tasks.extra: while the feed can be read, an aag task's state stays as aag says through the
    refresh and the answers to your notes (what you set yourself stays yours); the refresh's why and
    links stay too. While it can't, the refresh has the say."""
    a = (current or {}).get("aag")
    if not a or not live():
        return {}
    mine = set(current.get("mine") or [])
    return {k: a[k] for k in ("title", "next", "who", *STATUS) if k in a and k not in mine
            and not ("status" in mine and k in STATUS)}


def rules(reg):
    """For app.RULES: how the refresh reads the agent's DMs, and what it leaves to aag's feed."""
    ours = [t for t in reg["tasks"] if t.get("aag") and t["status"] in tasks.OPEN]
    dm = reg.get("aag_dm")
    text = ("{who}'s agent (aag; its Slack bot is \"{who}'s agent\") DMs {who} in one thread per task it works on"
            + (f", in DM channel {dm}" if dm else "") + ". Read those threads like any other DM: what the agent asks "
            "or tells {who} there, or waits on {who} for, is a task, with the thread as its source. Skip the bot's "
            "notices that aren't about one task (online, offline, paused, reminders that list several).")
    if ours:
        text += ("\nThese tasks on the list are the agent's, one per thread: "
                 + "; ".join(f"{t['id']} ({', '.join(x['label'] + ' ' + x['link'] if x['label'].endswith('thread') else x['label'] for x in t['aag']['sources'])})"
                             for t in ours)
                 + ". For a thread that's one of these, write what it says into an update of that task (why: what the "
                 "agent asks or reports, in a line), not a new task, and don't add tasks for their PRs. "
                 + ("The agent's own feed keeps their status, title and next step: leave those out."
                    if live() else "The agent's own feed can't be read just now: update their status from the thread too."))
    return text.replace("{who}", "\0").replace("{", "{{").replace("}", "}}").replace("\0", "{who}")


def after_refresh(_reg):
    """For app.AFTER_REFRESH: a task the refresh added anyway about one of the agent's tasks (its DM
    thread, or its PR) is folded into that one, its why and links, and taken off. So is one from the
    agent's DM that's about none of them, while the feed can be read (a notice). Read again under the
    lock: the page may have changed the list since."""
    with tasks.LOCK:
        reg = tasks.load()
        ours, at = [t for t in reg["tasks"] if t.get("aag")], {}
        for t in ours:
            for s in t["aag"]["sources"]:
                at[thread(s["link"]) or s["link"].split("#")[0]] = t
        dm, folded = reg.get("aag_dm"), set()
        for n in [t for t in reg["tasks"] if t["id"] not in _known and not t.get("aag") and "title" not in t.get("mine", [])]:
            links = [s["link"] for s in n["sources"] if s["link"]]
            hit = next((at[k] for k in (thread(x) or re.sub(r"(/pull/\d+).*", r"\1", x) for x in links) if k in at), None)
            if hit:
                if n.get("why") and hit.get("why") == hit["aag"].get("why"):
                    hit["why"] = n["why"]  # what the thread says, over aag's state line
                seen = {s["link"] for s in hit["sources"]}
                hit["sources"] = (hit["sources"] + [s for s in n["sources"] if s["link"] and s["link"] not in seen])[:6]
            elif not (live() and dm and links and all(f"/archives/{dm}/" in x for x in links)):
                continue
            folded.add(n["id"])
        if folded:
            reg["tasks"] = [t for t in reg["tasks"] if t["id"] not in folded]
            tasks.save(reg)
            print(f"aag: folded {len(folded)} task(s) the refresh added about the agent's work", file=sys.stderr)


def poll():
    """Reads aag once and puts its tasks on the list. On a failure the last ones stay, with the error
    and since when it fails; the refresh keeps them from the DM threads meanwhile."""
    global _known
    s = store.settings()
    setting = s.get("aag")
    feed = error = None
    try:
        cmd = command(setting)
        feed = read(cmd) if cmd else None
    except Exception as e:
        error = str(e)[-200:]
    with _lock:
        if feed:
            _feed.update(feed=feed, at=store.now(), error=None, since=None)
        elif error:
            _feed.update(error=error, since=_feed["since"] or store.now())
        else:  # off, or no aag here
            _feed.update(feed=None, at=None, error=None, since=None)
    with tasks.LOCK:
        reg = tasks.load()
        changed = sync(reg, feed, store.today(), store.now()) if feed else False
        if setting == "off" and any(t.get("aag") for t in reg["tasks"]):  # its tasks go too
            reg["tasks"], changed = [t for t in reg["tasks"] if not t.get("aag")], True
        if changed:
            tasks.save(reg)
        _known = {t["id"] for t in reg["tasks"]}
    app.RULES = "\n\n".join(x for x in (_rules, "" if setting == "off" else rules(reg)) if x)
    # the agents' own Claude sessions run in aag's workdir: counted, not listed
    work = (feed or {}).get("workdir")
    if setting in (None, "local") and work and work not in s.get("agent_paths", []):
        store.write(store.SETTINGS, {**s, "agent_paths": [*s.get("agent_paths", []), work]})


def state():
    """For app.STATE: the "Your agent" card's data, once aag was read (or reading it failed)."""
    with _lock:
        f = dict(_feed)
    if not f["feed"] and not f["error"]:
        return {}
    return {"aag": {**f, "where": store.settings().get("aag") or "local"}}


def start(wait=10):
    """From `docket run`: the hooks, and a read of aag every minute. Returns after the first read (at
    most `wait` seconds): the first refresh starts right after, and it should know what aag has."""
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
