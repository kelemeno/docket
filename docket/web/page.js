// The work page: Docket's task list in the cockpit, with today's meetings, your agent (aag.py), your
// pull requests and your Claude Code sessions on the side. index.html is the whole page; an app that
// carries Docket can put it inside its own:
//
//   const work = docketPage(element, { status: element for "Updated 11:20", run: the Refresh button,
//                                      assistant: what the reply box calls Claude (default "Claude"),
//                                      foot(data): the line under the side panel })
//
// Needs esc.js and docket.js loaded first, and the server's /state, /task, /reply and /refresh.
window.docketPage = function (el, opts = {}) {
  async function post(path, body) {
    let res;
    try {
      res = await fetch(path, { method: "POST", headers: { "X-Docket": "1", "Content-Type": "application/json" },
                                body: JSON.stringify(body || {}) });
    } catch {  // the server is restarting (an update) or stopped
      throw new Error("Can't reach the page's server just now. Try again in a moment.");
    }
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || "That didn't work.");
    return out;
  }
  const minutes = (t) => +t.slice(0, 2) * 60 + +t.slice(3, 5);
  function meetings(data) {
    const items = data.meetings || [], now = new Date().toTimeString().slice(0, 5);
    if (!items.length) return `<p class="empty">${data.refreshed ? "No meetings today." : "Meetings come with the first refresh."}</p>`;
    let html = "", placed = false, prevEnd = null;
    for (const m of items) {
      if (prevEnd && m.start > now && minutes(m.start) - minutes(prevEnd < now ? now : prevEnd) >= 30) {
        if (!placed && prevEnd <= now) { html += `<div class="nowline"></div>`; placed = true; }
        html += `<div class="ev free"><span class="tm">${esc(prevEnd < now ? now : prevEnd)}–${esc(m.start)}</span><span>free</span></div>`;
      }
      if (!placed && m.start > now) { html += `<div class="nowline"></div>`; placed = true; }
      html += `<div class="ev ${m.end <= now ? "past" : m.start <= now ? "cur" : ""}"><span class="tm">${esc(m.start)}–${esc(m.end)}</span><span>${esc(m.title)}</span></div>`;
      prevEnd = prevEnd && prevEnd > m.end ? prevEnd : m.end;
    }
    return html + (placed ? "" : `<div class="nowline"></div>`);
  }
  function ago(at) {
    const s = Date.now() / 1000 - at;
    return s < 90 ? "now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago`
      : s < 2 * 86400 ? "yesterday" : `${Math.floor(s / 86400)} days ago`;
  }
  function sessions(data) {
    const all = data.sessions || [], mine = all.filter((s) => !s.agent && s.title !== "untitled");
    const taskOf = {};
    for (const t of data.tasks || []) if (t.status === "open" || t.status === "waiting") for (const s of t.sessions || []) taskOf[s] = t.title;
    // a session the Claude app runs opens there on a click
    const row = (s) => `<div class="ses"><span class="dot ${esc(s.state)}"></span><div><div>${s.link
      ? `<a href="${esc(s.link)}" title="Open in Claude">${esc(s.title)}</a>` : esc(s.title)}</div>
      <div class="s">${esc(s.project)}${s.branch && s.branch !== "HEAD" ? " · " + esc(s.branch) : ""} · ${ago(s.at)}${taskOf[s.title] ? " · for: " + esc(taskOf[s.title]) : ""}</div></div></div>`;
    const part = (title, list) => list.length ? `<div class="sub">${title}</div>` + list.map(row).join("") : "";
    const agents = all.length - mine.length;
    return (part("Waiting for you", mine.filter((s) => s.state === "waiting")) + part("Running", mine.filter((s) => s.state === "running"))
      + part("Stopped mid-turn", mine.filter((s) => s.state === "stopped")) || `<p class="empty">No sessions in the last two days.</p>`)
      + (agents ? `<p class="meta" style="margin:8px 0 0">+ ${agents} agent run${agents > 1 ? "s" : ""}</p>` : "");
  }
  // Pull requests from the last two weeks, read with gh on this computer: yours by what they need, and the
  // ones you review by where they stand from your side. The tab is remembered in this browser.
  const PR = { ready: "go", conflicts: "bad", "CI failing": "bad", "changes requested": "bad", "CI running": "wait", "needs review": "wait", draft: "",
               "review requested": "bad", "re-requested": "bad", "new commits": "bad", "waiting on author": "wait", approved: "go" };
  let prTab = "mine"; try { prTab = localStorage.getItem("docket-pr-tab") || "mine"; } catch {}
  function prs(data) {
    const p = data.prs || {}, cutoff = Date.now() - 14 * 86400e3, recent = (l) => (l || []).filter((x) => Date.parse(x.updated + "Z") >= cutoff);
    const mine = recent(p.items), rev = recent(p.reviewing), list = prTab === "reviewing" ? rev : mine;
    const all = (prTab === "reviewing" ? p.reviewing : p.items) || [], older = all.length - list.length;
    const row = (x) => `<div class="pr"><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>
      <div class="s"><span class="st ${PR[x.state] || ""}">${esc(x.state)}</span>${x.draft && x.state !== "draft" ? " · draft" : ""}
        · ${esc(x.repo.split("/")[1])}#${x.number}${prTab === "reviewing" && x.author ? " · " + esc(x.author) : ""} · ${ago(Date.parse(x.updated + "Z") / 1000)}</div></div>`;
    const part = (title, states) => { const l = list.filter((x) => states.includes(x.state)); return l.length ? `<div class="sub">${title}</div>` + l.map(row).join("") : ""; };
    const body = prTab === "reviewing"
      ? part("Needs your review", ["review requested", "re-requested", "new commits"]) + part("Waiting on the author", ["waiting on author"]) + part("Approved by you", ["approved"])
      : part("Ready to merge", ["ready"]) + part("Needs you", ["conflicts", "CI failing", "changes requested"]) + part("Waiting", ["needs review", "CI running"]) + part("Drafts", ["draft"]);
    const tab = (k, name, n) => `<button data-prtab="${k}" class="${prTab === k ? "on" : ""}">${name}${n ? ` <span class="count">${n}</span>` : ""}</button>`;
    return `<div class="seg prtabs">${tab("mine", "Mine", mine.length)}${tab("reviewing", "Reviewing", rev.filter((x) => PR[x.state] === "bad").length || rev.length)}</div>`
      + (p.error ? `<p class="err" style="font-size:13px">gh: ${esc(p.error)}</p>` : "")
      + (body || `<p class="empty">${p.loading ? "Reading your PRs…" : prTab === "reviewing" ? "Nothing to review from the last two weeks." : "No open PRs in the last two weeks."}</p>`)
      + `<p class="meta" style="margin:8px 0 0">${older ? `+ ${older} older · ` : ""}<a href="https://github.com/pulls${prTab === "reviewing" ? "/review-requested" : ""}" target="_blank" rel="noopener">All on GitHub ↗</a></p>`;
  }
  // Your agent (aag), when settings.json has "aag": its tasks by who has the move, what each agent does
  // now, the PR's state, and the links. Read every minute; when that fails, the last read stays.
  const AAG = [["needs_you", "Needs you", "bad"], ["working", "Working", "go"], ["waiting", "Waiting on others", "wait"], ["done", "Done today", ""]];
  const CI = { success: "CI ✓", failure: "CI failing", pending: "CI running" };
  function agent(data) {
    const a = data.aag, f = a.feed, hhmm = (s) => esc(String(s || "").slice(11, 16));
    const head = (a.error ? `<p class="err" style="font-size:13px">aag unreachable since ${hhmm(a.since)}: ${esc(a.error)}</p>` : "")
      + (!f ? "" : f.daemon === "off" ? `<p class="meta"><span class="st bad">Off</span>: <code>aag start</code> turns it on.</p>`
        : f.daemon === "paused" ? `<p class="meta"><span class="st wait">Paused</span>: no agent runs until you DM it <code>on</code>.</p>` : "")
      + (f && f.offline ? `<p class="meta"><span class="st wait">Offline</span>: no ${[f.offline.slack && "Slack", f.offline.github && "GitHub"].filter(Boolean).join(" or ")}; nothing reaches the agents.</p>` : "")
      + (f && f.sign ? `<p class="meta">To sign ${esc(f.sign.prs.join(", "))}: <code>${esc(f.sign.command)}</code></p>` : "");
    if (!f) return head || `<p class="empty">Reading your agent…</p>`;
    // as on the list (aag.py finished): a review the agents agree on is done for you, and done shows for the day
    const col = (t) => t.column === "done" || (t.role === "reviewer" && t.status === "agents_done" && t.column !== "needs_you") ? "done" : t.column;
    const shown = f.tasks.filter((t) => col(t) !== "done" || new Date(t.statusAt || t.updatedAt).toLocaleDateString("sv") === data.today);
    const ln = (href, text) => href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${text}</a>` : "";
    const row = (t, tone) => {
      const p = t.pr;
      const facts = p ? [ln(p.url, esc(p.key.split("/")[1])), CI[p.ci], p.draft && "draft", p.conflicts && "conflicts",
                         p.review === "APPROVED" && "approved", p.unresolved && `${p.unresolved} unresolved`].filter(Boolean).join(" ") : "";
      const sub = [`<span class="st ${tone}">${esc(t.next)}</span>`, t.now && "now: " + esc(t.now.text), facts,
                   ln(t.dm, "DM"), ln(t.thread, "thread"), t.embargo && "embargo", t.cost >= 1 && "$" + Math.round(t.cost)].filter(Boolean).join(" · ");
      return `<div class="pr">${ln(t.dm || (p && p.url) || t.thread, esc(t.title)) || esc(t.title)}<div class="s">${sub}</div></div>`;
    };
    const body = AAG.map(([column, title, tone]) => {
      const l = shown.filter((t) => col(t) === column);
      return l.length ? `<div class="sub">${title}</div>` + l.map((t) => row(t, tone)).join("") : "";
    }).join("");
    return head + (body || `<p class="empty">Nothing for your agent this week.</p>`)
      + `<p class="meta" style="margin:8px 0 0">${esc(f.host)}${a.at ? ` · read ${hhmm(a.at)}` : ""}</p>`;
  }
  const work = docket(el, {
    assistant: opts.assistant,
    actions: ["done", "snooze", "waiting", "drop", "open"],
    minutes: true,
    load: async () => (await fetch("/state")).json(),
    async act(t, action, value) {
      if (action !== "restore") return post("/task", { id: t.id, action, value });
      // undo: back to the status and day it had before
      const today = work.data.today;
      if (value.status === "waiting") return post("/task", { id: t.id, action: "waiting" });
      const reopened = await post("/task", { id: t.id, action: "reopen" });
      const now = reopened.tasks.find((x) => x.id === t.id);
      const later = value.until && value.until > new Date().toLocaleString("sv").slice(0, 16).replace(" ", "T");  // a snooze that hasn't run out
      const back = later ? value.until : value.do < today ? today : value.do;
      return now && back && (back !== now.do || value.until) ? post("/task", { id: t.id, action: "snooze", value: back }) : reopened;
    },
    add: (text, kind) => post("/task", { action: "add", value: text, kind }),
    reply: (t, text) => post("/reply", { id: t.id, text }),
    refresh: () => opts.run && opts.run.click(),
    empty: (d) => d.refreshed ? `<p class="title">Nothing due today.</p><p class="meta">Everything open is waiting on others or planned for later.</p>`
      : `<p class="title">No tasks yet.</p><p class="meta">The first refresh reads the last 3 days of whatever your Claude Code is connected to: Slack, email, calendar, GitHub, Linear, Notion. It takes a few minutes.</p>`,
    side: (d) => `<section class="card"><h2>Today</h2>${meetings(d)}</section>${d.aag ? `<section class="card"><h2>Your agent <span class="count">${((d.aag.feed || {}).tasks || []).filter((t) => t.column === "needs_you").length || ""}</span></h2>${agent(d)}</section>` : ""}<section class="card"><h2>Pull requests</h2>${prs(d)}</section><section class="card"><h2>Claude sessions</h2>${sessions(d)}</section>`,
    sideClick(e, d) {
      const b = e.target.closest("button[data-prtab]"); if (!b) return;
      prTab = b.dataset.prtab; try { localStorage.setItem("docket-pr-tab", prTab); } catch {}
      return d;
    },
    foot: opts.foot || ((d) => `Kept on this computer in ${d.folder}.`),
    rendered(d) {
      if (opts.run) { opts.run.disabled = !!d.running; opts.run.textContent = d.running ? "Reading…" : "Refresh"; }
      if (opts.status) opts.status.innerHTML = d.error ? `<span class="err">Last refresh failed: ${esc(d.error)}</span>`
        : d.running ? "Reading your accounts…" : d.refreshed ? `Updated ${esc(d.refreshed.slice(11, 16))}` : "Not refreshed yet";
      clearTimeout(window.poll);
      if (d.running || d.tasks.some((t) => t.asked) || (d.prs || {}).loading) window.poll = setTimeout(work.reload, 5000);  // a refresh or an answer coming
    },
  });
  if (opts.run) opts.run.onclick = async () => { await post("/refresh"); work.reload(); };
  work.reload();
  setInterval(() => { if (document.visibilityState === "visible" && !work.data?.running) work.reload(); }, 60 * 1000);
  return work;
};
