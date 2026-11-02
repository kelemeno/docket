# Docket

A docket for your work day: one task list that your own [Claude Code](https://claude.com/claude-code)
keeps current from Slack, email, calendar, GitHub, Linear and Notion, and a page on your computer to
work through it. One task in **Now**, the rest queued below, your day on the side.

It runs on your computer, with your Claude Code and whatever it's connected to. There's no server and
no account, and nothing to install beyond Python. The task list is a JSON file in a folder only you can read.

## What it does

- **A list that carries over.** Every 15 minutes from 08:00 to 20:00 Claude reads only what's new since the
  last run and updates the list. It adds what you owe (promises, unanswered questions, review requests,
  decisions, deadlines), updates open tasks and adds today's meetings. Every task links to its source:
  the Slack thread, the PR, the doc.
- **Rules that keep it honest.**
  - A field you set stays yours, and the refresh leaves it alone.
  - A task is done only with evidence: a draft isn't sent, silence isn't done.
  - A task you dropped or finished doesn't come back.
  - An overdue task rolls to today and shows how often it slipped.
- **Reply to Claude** on a task (`c`): "ask Sam for a review in the team thread". It finds the thread,
  links it, rewrites the next step and answers under your note, usually in about a minute. The
  refresh reads your notes too.
- **Now:** **Done**, **Snooze** (30 minutes, an hour, tomorrow, Monday, next week, a date),
  **Waiting** (on someone else), **Drop**, **Open** the source, **Undo**.
- **Quick** (under 10 minutes) and **Deep work** for today, P0 first. **Waiting**, **Later** and
  **Handled today** are folded below. Add your own tasks too.
- **On the side:**
  - Today's meetings, with a now line and free gaps.
  - Your **pull requests** (read with `gh`), in two tabs. **Mine** groups them as ready to merge, needs
    you (conflicts, CI failing, changes requested), waiting, and drafts. **Reviewing** groups the PRs
    you're asked to review or have reviewed: review requested, new commits since your review, waiting
    on the author, approved.
  - Your **Claude Code sessions** from the last two days, the ones waiting for you first. On a Mac,
    a session the Claude desktop app runs opens there when you click it.
- Keys: `j`/`k` move · `d` done · `s` snooze · `w` waiting · `x` drop · `o` open · `c` reply ·
  `u` undo · `n` new task · `r` refresh.

## Setup

You need Python 3.9 or newer, [Claude Code](https://claude.com/claude-code) signed in and connected to
your tools (Slack, calendar, etc.), and optionally the [GitHub CLI](https://cli.github.com) (`gh`) for
the PR panel.

```bash
pip install git+https://github.com/kelemeno/docket.git
docket setup --name Sam
docket run
```

`docket setup` asks Claude which tools it has and keeps only the read-only ones (below). `--name` is
how the prompts refer to you. `docket run` serves the page at http://127.0.0.1:8766 (`docket open`
opens it) and refreshes the list every 15 minutes. The first refresh reads the last three days and takes a
few minutes.

Other commands: `docket refresh` reads what's new now, and `docket now` prints the top of today's list.

To keep it running on a Mac, a LaunchAgent works (`~/Library/LaunchAgents/docket.plist` running
`docket run`, with `RunAtLoad` and `KeepAlive`).

### Settings

The folder is `$DOCKET_HOME`, or `~/.local/share/docket` by default. In it, `settings.json` has these fields:

| key | what |
|---|---|
| `tools` / `refused` | the tools Claude may use, and the ones it may not; filled in by `docket setup` |
| `who` | your first name, as the prompts call you |
| `every_min` | minutes between refreshes (default 15; each is a Claude run, so fewer use less of your plan) |
| `model` | the Claude model for refreshes and replies (default `sonnet`) |
| `agent_paths` | folders where your background agents run: their sessions are only counted, not listed |
| `aag` | your agent's feed ([below](#your-agent-aag)): unset for the aag on this computer, `"ssh HOST"` where aag runs on a server, or `"off"` |

The page's port is `$DOCKET_PORT` (default 8766).

### Your agent (aag)

If you run aag (auto-agents: Slack agents that write and review PRs for you), Docket shows its work too,
from two places that make one list:

- **Slack, always.** Your agent DMs you in one thread per task, and the refresh reads those threads
  like any other DM. Their asks become tasks, wherever aag runs.
- **aag's own feed, when it can be read.** Every minute Docket runs `aag status --json`: the aag on this
  computer, found by itself, or `"ssh HOST"` where aag runs on a server. Over SSH it never asks for a
  password (BatchMode), and nothing has to listen on the server. Use `"ssh HOST /path/to/aag"` when
  `aag` isn't on the PATH an SSH command gets there, and `"off"` for no feed.

How the feed's tasks show:

- A task whose next move is yours (say go, review, sign, merge) is on the list, linked to its DM thread
  and its PR. One the agent works on, or that waits on others, is under **Waiting**. A finished one is
  under **Handled today**, and so is a review once the agents agree: approving and merging it is the
  author's and the humans' to do. A task leaves the list when aag stops listing it.
- The **Your agent** card on the side has the rest: who has each move, what each agent is doing now,
  where each PR stands, and the links. When aag can't be read, the card keeps the last read and says
  since when.

The two meet in the DM thread: one task per thread. While the feed can be read, it has the say on a
task's status, title, next step and who's waiting. The refresh adds what the thread says (the why) and
its links, and doesn't add tasks for the PRs aag tracks. While the feed can't be read, the refresh keeps
these tasks from the threads, and the feed takes them back when it returns. Your snooze, priority and
kind on one stay yours. Your done or drop stays until its next move changes. With the local aag, its
workdir joins `agent_paths`, so its agents' sessions are only counted.

## Safety

- **Claude only reads.**
  - It can use only the tools whose names say they read (list, get, search, read…) and contain no
    writing word, plus read-only `gh` commands. Every other tool is denied by name.
  - It runs in Claude Code's default permission mode, so your own Claude settings can't widen it.
  - Everything Claude reads is treated as data, never as instructions. Check the list `setup` prints.
- **Only your browser.** The page listens on 127.0.0.1 only and refuses other host names (DNS
  rebinding). It sends no CORS headers, and changes need a header a web form can't set.
- **Your data stays local.** The task list is in a 0700 folder, 0600 file. Nothing leaves your
  computer except what Claude Code sends to Anthropic and the calls to the services it reads.

## Using the page on another task list

`docket/web/docket.js` and `docket.css` carry no assumptions about where tasks come from. A page
hands `docket(element, source)` an adapter:

```js
docket(document.getElementById("view"), {
  load: async () => ({ today: "2026-03-02", tasks: [/* {id, title, next, kind, priority, status, do, sources…} */] }),
  act: async (task, action, value) => { /* done, drop, snooze, waiting, star, restore → new data */ },
  add: async (text, kind) => { /* → new data */ },
  actions: ["done", "snooze", "drop"],
  side: (data) => "<section class='card'>…</section>",
  empty: (data) => "<p class='title'>Nothing for today.</p>",
});
```

`docket.js` has the full interface at the top. `docket/web/page.js` is the work page built on it
(`docketPage(element, {status, run, assistant})`), and `docket/web/index.html` is the page around it.

## Carrying Docket inside another app

Docket is standard-library Python with relative imports, so an app can carry a copy as a
subpackage (`yourapp/docket/`) and change it through these hooks instead of editing it:

| hook | what |
|---|---|
| `store.configure(home=…, settings=…)` | where `tasks.json` lives, and which settings file to read |
| `tasks.extra = fn(item, current) → dict` | fields of your own kept on tasks and meetings (e.g. what may be shared elsewhere) |
| `app.RULES = "…"` | more rules appended to the refresh prompt (`{who}` is filled in) |
| `app.AFTER_REFRESH.append(fn(reg))` | something to do with the list after each refresh |
| `app.STATE = fn() → dict` | more fields in what the page loads |
| `app.serve(port, index=…, routes={…})` | your own page around `page.js`, and GET routes of your own |
| `docketPage(el, {assistant: "…", foot: fn(data)})` | what the reply box calls the one answering, and the line under the side panel |

## Tests

```bash
python -m unittest
```

Python standard library only, no build step.

## License

MIT
