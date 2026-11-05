"""Your Linear issues on the list, and the reviews you're asked for there. Deep work lives in Linear:
every task that's real work belongs to an issue, and the page's Work tab lists them in Linear's
order, each with whose move it is now. Linear is read with your API key every 5 minutes, apart from
the hourly refresh, so the order and the states are Linear's own, not Claude's reading of them.

Two kinds of item:
- an issue assigned to you that isn't done or cancelled;
- a review you're asked for in a comment on someone's issue: a line `review: @you`, with a priority
  if it has one (`review: @kl high, @valera`); a later `review: @you done` (or drop) takes it off.
  The latest such line about you on an issue counts. @you is your Linear display name, first name,
  name or email's name.

Each becomes a task ("lin-EVM-123"), kept as Linear has it. Whose move it is comes from the issue's
PRs (Linear's GitHub links) as `gh` reads them, and from your agent (aag.py): a PR that needs you
makes the issue open, one that waits on CI, the author or other reviewers makes it wait. A reviewed
issue whose PRs are all merged or closed is done. What else on the list is about an issue (its key,
its PR) is coupled to it ("issue": the key): a task the refresh made is folded in, your agent's and
your own stay and show with it. Deep work that belongs to no issue shows in the Work tab as not on
Linear, with a link to create it there.

    settings.json   "linear_key": "lin_api_…"   a personal API key (Linear: Settings → Security & access),
                                               or $LINEAR_API_KEY; without one there's no Work feed
                    "reviews_days": 30          how far back `review:` lines count
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

from . import aag, app, store, tasks

API = "https://api.linear.app/graphql"
EVERY = 300  # Linear's read; the coupling and the PR states are looked at every minute
TICK = 60
TIMEOUT = 30
KEY = re.compile(r"\b([A-Z][A-Z0-9]{0,9}-\d{1,6})\b")
ISSUE_LINK = re.compile(r"https://linear\.app/[^/\s]+/issue/([A-Z][A-Z0-9]{0,9}-\d{1,6})")
PR = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+")
REVIEW = re.compile(r"^[\s>*_-]*review:\s*(.+)$", re.I | re.M)
MENTION = re.compile(r"\[@?[^\]]*\]\(https://linear\.app/[^/\s]+/profiles/([\w.-]+)\)"
                     r"|https://linear\.app/[^/\s]+/profiles/([\w.-]+)|@([\w.-]+)")
LEVEL = {"urgent": 1, "p0": 1, "high": 2, "p1": 2, "medium": 3, "normal": 3, "p2": 3, "low": 4, "p3": 4}
STOP = ("done", "drop", "dropped", "off", "none", "no")
NAMED = {1: "urgent", 2: "high", 3: "medium", 4: "low", 5: "no priority"}
STATUS = aag.STATUS
# what a PR's state (app._pr_state, app._review_state) says about whose move it is
YOURS = {"conflicts", "CI failing", "changes requested", "ready", "draft", "review requested", "re-requested", "new commits"}
THEIRS = {"needs review", "CI running", "waiting on author", "approved"}

ISSUE = ("id identifier title url priority sortOrder state{name type} assignee{name} "
         "attachments{nodes{url metadata}}")
QUERY = ("query($since:DateTimeOrDuration!){viewer{id name displayName email teams(first:20){nodes{key archivedAt}} "
         "assignedIssues(first:100,filter:{state:{type:{nin:[\"completed\",\"canceled\"]}}}){nodes{" + ISSUE + "}}} "
         "comments(first:100,orderBy:updatedAt,filter:{body:{containsIgnoreCase:\"review:\"},updatedAt:{gt:$since}})"
         "{nodes{id body url createdAt user{name displayName} issue{" + ISSUE + "}}}}")

_feed = {"items": None, "me": None, "at": None, "error": None, "since": None, "read": 0, "team": None}
_lock = threading.Lock()
_rules = ""  # what rules() said at the last look, for app.RULES_FROM


def key():
    return store.settings().get("linear_key") or os.environ.get("LINEAR_API_KEY") or ""


def read(api_key, days=30):
    """Linear's answer to QUERY: {viewer, comments}. Raises when it can't be had."""
    body = json.dumps({"query": QUERY, "variables": {"since": f"-P{int(days)}D"}}).encode()
    req = urllib.request.Request(API, data=body, headers={"Authorization": api_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Linear said {e.code}: {e.read().decode(errors='replace')[:200]}") from None
    if out.get("errors"):
        raise RuntimeError("Linear: " + "; ".join(str(x.get("message")) for x in out["errors"])[:200])
    return out["data"]


def _names(me):
    """What a mention of you can say: your display name, first name, name, or email's name."""
    name = str(me.get("name") or "")
    return {n.casefold() for n in (me.get("displayName"), name.replace(" ", ""), name.split(" ")[0],
                                   str(me.get("email") or "").split("@")[0]) if n}


def reviews(comments, me):
    """The reviews you're asked for: {issue id: (comment, level 1-4)}, from the latest `review:` line
    about you on each issue; one that says done or drop takes the issue off."""
    names, out = _names(me), {}
    for c in sorted((c for c in comments if isinstance(c, dict) and c.get("issue")), key=lambda c: c.get("createdAt") or ""):
        for line in REVIEW.findall(str(c.get("body") or "")):
            for part in re.split(r"[,;]", line):
                m = MENTION.search(part)
                if not m or next(g for g in m.groups() if g).casefold() not in names:
                    continue
                word = (part[m.end():].strip().lower().split() or [""])[0].strip(".:!()")
                if word in STOP:
                    out.pop(c["issue"]["id"], None)
                else:
                    out[c["issue"]["id"]] = (c, LEVEL.get(word, 3))
    return out


def _prs(i):
    """The issue's GitHub PRs, and the ones Linear says are merged or closed."""
    urls, over = [], set()
    for a in (i.get("attachments") or {}).get("nodes") or []:
        m = PR.match(str(a.get("url") or ""))
        if m and m.group(0) not in urls:
            urls.append(m.group(0))
            meta = a.get("metadata") or {}
            if str(meta.get("status") or meta.get("state") or "").lower() in ("merged", "closed"):
                over.add(m.group(0))
    return urls, over


def items(data):
    """Linear's answer as the list's items, in Linear's order (priority, then the manual order):
    {key, title, url, role assignee|reviewer, level 1-5, sort, state, prs, over, comment, by}."""
    me, out = data["viewer"], {}

    def item(i, role, level, c=None):
        prs, over = _prs(i)
        return {"key": i["identifier"], "title": tasks._text(i.get("title"), 160), "url": i.get("url") or "",
                "role": role, "level": level or 5, "sort": float(i.get("sortOrder") or 0),
                "state": (i.get("state") or {}).get("name") or "", "prs": prs, "over": sorted(over),
                "comment": (c or {}).get("url") or "", "by": ((c or {}).get("user") or {}).get("name") or ""}
    for i in (me.get("assignedIssues") or {}).get("nodes") or []:
        out[i["id"]] = item(i, "assignee", i.get("priority"))
    for iid, (c, level) in reviews((data.get("comments") or {}).get("nodes") or [], me).items():
        if iid not in out and (c["issue"].get("state") or {}).get("type") not in ("completed", "canceled"):
            out[iid] = item(c["issue"], "reviewer", level, c)
    return sorted(out.values(), key=lambda x: (x["level"], x["sort"]))


def keys(t):
    """The Linear issues a task names: in its title or next step, or a link to one."""
    text = f"{t.get('title', '')} {t.get('next', '')}"
    return set(KEY.findall(text)) | {m.group(1) for s in t.get("sources", []) for m in [ISSUE_LINK.match(s["link"])] if m}


def _pr_links(t):
    return {m.group(0) for s in t.get("sources", []) for m in [PR.match(s["link"])] if m}


def coupled(t, by_key, by_pr):
    """The key of the issue a task is about, or None."""
    return next((k for k in sorted(keys(t)) if k in by_key), None) or next((by_pr[u] for u in sorted(_pr_links(t)) if u in by_pr), None)


def move(x, prs, agents):
    """Whose move an issue is, as (status, next, why): open when one of its PRs or your agent's task
    for it needs you, or there's nothing to wait on; waiting when they all wait on someone else; done
    when a review's PRs are all merged or closed."""
    if x["role"] == "reviewer" and x["prs"] and set(x["prs"]) <= set(x["over"]):
        return "done", "Its PRs are merged or closed", ""
    seen = [(prs[u], u) for u in x["prs"] if u in prs]
    name = lambda u: "#" + u.rsplit("/", 1)[1]
    yours = [f"{name(u)}: {p['state']}" for p, u in seen if p["state"] in YOURS]
    yours += [f"your agent: {t['next']}" for t in agents if t["status"] == "open"]
    theirs = [f"{name(u)}: {p['state']}" for p, u in seen if p["state"] in THEIRS]
    theirs += [f"your agent: {t['next']}" for t in agents if t["status"] == "waiting"]
    why = ("review asked" + (f" by {x['by']}" if x["by"] else "") if x["role"] == "reviewer" else f"Linear: {x['state']}") \
        + f" · {NAMED[x['level']]}"
    if yours:
        return "open", "; ".join(yours), why
    if theirs:
        return "waiting", "; ".join(theirs), why
    return "open", ("Review it" if x["role"] == "reviewer" else f"{x['state']}: no open PR yet"), why


def _fields(x, prs, agents, now):
    status, next, why = move(x, prs, agents)
    sources = [{"label": f"{x['key']} on Linear", "link": x["url"]}] + \
              ([{"label": f"review asked{' by ' + x['by'] if x['by'] else ''}", "link": x["comment"]}] if x["comment"] else []) + \
              [{"label": "#".join(u.split("/")[-3::2]), "link": u} for u in x["prs"] if u not in x["over"]]
    return {"title": x["title"], "next": tasks._text(next, 240), "why": why, "who": x["by"] if x["role"] == "reviewer" else "",
            "status": status, "done_at": now if status == "done" else None, "closed_by": "linear" if status == "done" else None,
            "evidence": next if status == "done" else "", "sources": tasks._sources(sources)}


def _apply(t, x, prs, agents, now):
    """Linear's word on one of its tasks, with whose move it is now. What you set stays yours; your
    done or drop stays until the move changes. The links the refresh folded in stay."""
    f, was = _fields(x, prs, agents, now), t.get("linear") or {}
    move_now = f"{f['status']}: {f['next']}"
    ours = f["sources"]
    keep = [s for s in t.get("sources", []) if s not in was.get("sources", []) and s["link"] not in {o["link"] for o in ours}]
    f["sources"] = (ours + keep)[:6]
    if t.get("why") and t.get("why") != was.get("why"):
        f.pop("why")  # the refresh wrote it, from the thread or the PR
    mine = set(t.get("mine") or [])
    if "status" in mine and was.get("move") != move_now:
        mine.discard("status")
    if t["status"] == "done" and f["status"] == "done":
        f.pop("done_at")  # still done: since when it was
    new = {k: v for k, v in f.items() if k not in mine and not ("status" in mine and k in STATUS)}
    if any(t.get(k) != v for k, v in new.items()):
        t.update(new, updated=now)
    level = {1: "P0", 2: "P1"}.get(x["level"], "P2")
    if "priority" not in mine:
        t["priority"] = level
    t["mine"] = sorted(mine)
    t["linear"] = {"key": x["key"], "role": x["role"], "level": x["level"], "sort": x["sort"], "state": x["state"],
                   "url": x["url"], "move": move_now, "why": f["why"] if "why" in f else was.get("why"), "sources": ours,
                   **{k: v for k, v in f.items() if k in ("title", *STATUS)}}


def sync(reg, feed, prs, today, now):
    """Puts the Linear items on the list as Linear has them, couples the rest of the list to them and
    folds in what the refresh added about one; a Linear task that's no longer yours is handled. `prs`
    is {PR url: {state}} from gh. Returns whether the list changed."""
    before = json.dumps(reg, sort_keys=True)
    by_key = {x["key"]: x for x in feed}
    by_pr = {u: x["key"] for x in feed for u in x["prs"]}
    ours = {t["linear"]["key"]: t for t in reg["tasks"] if t.get("linear")}
    fresh = set()
    for k, x in by_key.items():  # new ones first, so what's about them can be folded in
        if k not in ours:
            ours[k] = {"id": f"lin-{k}", "kind": "deep", "priority": "P2", "status": "open", "do": today, "rolled": 0,
                       "sessions": [], "sources": [], "created": now, "updated": now, "mine": [], "linear": {"key": k}}
            reg["tasks"].append(ours[k])
            fresh.add(ours[k]["id"])
    folded = set()
    for t in reg["tasks"]:
        if t.get("linear"):
            continue
        k = coupled(t, by_key, by_pr)
        if not k:
            t.pop("issue", None)
            continue
        t["issue"] = k
        home = ours.get(k)
        # the refresh's own task about an issue: its why and links go to the issue's task, and it goes
        if home and not t.get("aag") and t["status"] in tasks.OPEN and not t.get("mine") and not t.get("notes"):
            if t.get("why") and (home["id"] in fresh or home.get("why") == home["linear"].get("why")):
                home["why"] = t["why"]
            seen = {s["link"] for s in home["sources"]}
            home["sources"] = (home["sources"] + [s for s in t["sources"] if s["link"] and s["link"] not in seen])[:6]
            folded.add(t["id"])
    reg["tasks"] = [t for t in reg["tasks"] if t["id"] not in folded]
    agents = {}
    for t in reg["tasks"]:
        if t.get("aag") and t.get("issue"):
            agents.setdefault(t["issue"], []).append(t)
    for t in reg["tasks"]:
        lin = t.get("linear")
        if not lin:
            continue
        x = by_key.get(lin["key"])
        if x:
            _apply(t, x, prs, agents.get(x["key"], []), now)
        elif t["status"] in tasks.OPEN:  # done or cancelled there, or not yours any more
            t.update(status="done", done_at=now, closed_by="linear", evidence="no longer yours on Linear", updated=now)
    # one that's already done the first time it's seen (a review whose PRs are merged) isn't added
    reg["tasks"] = [t for t in reg["tasks"] if not (t["id"] in fresh and t["status"] == "done")]
    return json.dumps(reg, sort_keys=True) != before


def _pr_states():
    """{PR url: {state}} for your PRs and the ones you review, as the page last read them with gh."""
    p = app.prs()
    return {x["url"]: x for x in (p.get("items") or []) + (p.get("reviewing") or [])}


def live():
    with _lock:
        return _feed["items"] is not None and not _feed["error"]


def extra(item, current):
    """For tasks.extra: while Linear can be read, a Linear task's title and state stay as Linear and
    its PRs say through the refresh and the answers to your notes (what you set stays yours)."""
    lin = (current or {}).get("linear")
    if not lin or not live():
        return {}
    mine = set(current.get("mine") or [])
    return {k: lin[k] for k in ("title", *STATUS) if k in lin and k not in mine and not ("status" in mine and k in STATUS)}


def rules(reg):
    """For app.RULES_FROM: what the refresh leaves to Linear, and where it writes news about an issue."""
    ours = [t for t in reg["tasks"] if t.get("linear") and t["status"] in tasks.OPEN]
    text = ("Deep work is tracked in Linear. For real work (deep), name its Linear issue key in the title (e.g. "
            "\"EVM-123: …\") when the source names one or links to it; a PR's Linear issue is often in its title, "
            "branch or description.")
    if ours:
        text += ("\nThese tasks on the list are {who}'s Linear issues and reviews: "
                 + "; ".join(f"{t['id']} ({t['linear']['key']}, {t['title'][:60]})" for t in ours)
                 + ". For news about one (its PR, its thread, a question about it), write it into an update of that "
                   "task (why, sources), not a new task. Linear and the PRs keep their title, status and next step: "
                   "leave those out.")
    return text.replace("{who}", "\0").replace("{", "{{").replace("}", "}}").replace("\0", "{who}")


def poll(read_linear=True):
    """Reads Linear (when `read_linear`; on a failure the last items stay, with the error and since
    when), then puts the items on the list with their PRs' states."""
    global _rules
    if read_linear:
        api_key, error, got = key(), None, None
        if api_key:
            try:
                data = read(api_key, store.settings().get("reviews_days", 30))
                got = items(data)
            except Exception as e:
                error = str(e)[-200:]
            else:
                with _lock:
                    _feed["team"] = team(data)
        with _lock:
            _feed["read"] = time.time()
            if got is not None:
                _feed.update(items=got, at=store.now(), error=None, since=None)
            elif error:
                _feed.update(error=error, since=_feed["since"] or store.now())
            else:  # no key
                _feed.update(items=None, at=None, error=None, since=None)
    with _lock:
        feed = _feed["items"]
    with tasks.LOCK:
        reg = tasks.load()
        if feed is not None and sync(reg, feed, _pr_states(), store.today(), store.now()):
            tasks.save(reg)
    _rules = rules(reg) if feed is not None else ""


def team(data):
    """Your team on Linear (its key): the first of yours that isn't archived."""
    teams = (((data.get("viewer") or {}).get("teams") or {}).get("nodes") or [])
    return next((t["key"] for t in teams if not t.get("archivedAt")), None)


def state():
    """For app.STATE: the Work tab's line about Linear, and the team a new issue goes to (settings
    "linear_team", else yours as Linear says)."""
    with _lock:
        f = dict(_feed)
    return {"linear": {"key": bool(key()), "at": f["at"], "error": f["error"], "since": f["since"],
                       "count": len(f["items"] or []), "team": store.settings().get("linear_team") or f["team"]}}


def start(wait=10):
    """From `docket run`: the hooks, a read of Linear every 5 minutes and of the coupling every minute.
    Returns after the first read (at most `wait` seconds), so the first refresh knows the issues."""
    shown, kept = app.STATE, tasks.extra
    app.STATE = lambda: {**shown(), **state()}
    tasks.extra = lambda item, current: {**kept(item, current), **extra(item, current)}
    app.RULES_FROM.append(lambda: _rules)
    app.AFTER_REFRESH.append(lambda _reg: poll(read_linear=False))  # fold what the refresh added
    first = threading.Event()

    def loop():
        while True:
            try:
                poll(read_linear=time.time() - _feed["read"] >= EVERY)
            except Exception as e:  # a bad tasks.json, say: the next minute tries again
                print(f"linear: {e!r}", file=sys.stderr)
            first.set()
            time.sleep(TICK)
    threading.Thread(target=loop, daemon=True).start()
    first.wait(wait)
