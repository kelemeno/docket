// The work page: Docket's task list in two views, Triage and Work (Linear, linear.py), with today's
// meetings, your agent (aag.py), your pull requests and your Claude Code sessions on the side. index.html is the whole page; an app that
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
  const hhmm = (m) => String(Math.floor(m / 60) % 24).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
  // Your open work in today's free time, from now to the end of your hours (settings "hours"). What you
  // put in order (Work's list, or dragging in the grid; data.order) goes first, in that order. Then Work
  // in its order, and your quick ones batched in the gaps too short for deep work. Each takes its
  // estimate (est), carried on into the next gap when it doesn't fit. Worked out again on every load.
  const PRI = ["P0", "P1", "P2"];
  const pri = (t) => (t.star ? -1 : 0) + ((PRI.indexOf(t.priority) + 4) % 4);
  const place = (d, t) => { const i = (d.order || []).indexOf(t.id); return i < 0 ? Infinity : i; };
  const byOrder = (a, b, d) => { const x = place(d, a), y = place(d, b); return x === y ? 0 : x === Infinity ? 1 : y === Infinity ? -1 : x - y; };
  function plan(d) {
    const [from, to] = String(d.hours || "09:00-18:00").split("-").map((x) => minutes(x.trim()));
    const n = new Date(), now = Math.ceil((n.getHours() * 60 + n.getMinutes()) / 5) * 5;
    const busy = (d.meetings || []).map((m) => [minutes(m.start), minutes(m.end)]).sort((a, b) => a[0] - b[0]);
    const gaps = [];
    let at = Math.max(from, now);
    for (const [s, e] of [...busy, [to, to]]) {
      if (Math.min(s, to) - at >= 15) gaps.push([at, Math.min(s, to)]);
      at = Math.max(at, e);
    }
    const open = (d.tasks || []).filter((t) => due(d, t) && (work(t) || triage(t))).map((t) => ({ ...t }));
    const left = (t) => t.left ?? est(t);
    const mine = open.filter((t) => place(d, t) < Infinity).sort((a, b) => byOrder(a, b, d));
    const rest = open.filter((t) => place(d, t) === Infinity);
    const deep = rest.filter(work).sort((a, b) => level(d, a)[0] - level(d, b)[0] || level(d, a)[1] - level(d, b)[1] || pri(a) - pri(b));
    const quick = rest.filter(triage).sort((a, b) => pri(a) - pri(b));
    const out = [];
    for (let [s, e] of gaps) {
      while (e - s >= 15 && mine.length) {  // yours, in your order: deep ones need 30 minutes, or wait for the next gap
        const t = mine[0];
        if (work(t)) {
          if (e - s < 30) break;
          const len = Math.min(left(t), e - s);
          out.push({ start: s, end: s + len, view: "work", tasks: [t] }); s += len;
          if ((t.left = left(t) - len) < 15) mine.shift();  // what's left goes on in the next gap
        } else {
          let k = 0; while (k < mine.length && !work(mine[k]) && (k + 1) * 10 <= Math.min(e - s, 30)) k++;
          out.push({ start: s, end: s + k * 10, view: "triage", tasks: mine.splice(0, k) }); s += k * 10;
        }
      }
      while (e - s >= 15 && (deep.length && !mine.length || quick.length)) {  // the rest; quick ones also fill a gap yours don't fit
        if (deep.length && !mine.length && (e - s >= 45 || !quick.length && e - s >= 30)) {
          const t = deep[0], len = Math.min(left(t), e - s);
          out.push({ start: s, end: s + len, view: "work", tasks: [t] }); s += len;
          if ((t.left = left(t) - len) < 15) deep.shift();
        } else if (quick.length) {
          const k = Math.max(1, Math.min(quick.length, Math.floor(Math.min(e - s, 30) / 10)));
          out.push({ start: s, end: s + k * 10, view: "triage", tasks: quick.splice(0, k) }); s += k * 10;
        } else break;
      }
    }
    return { blocks: out, left: mine.length + deep.length + quick.length, from, to };
  }
  // The day as a grid: meetings where they are (side by side when they overlap), with their call
  // links, and the plan's blocks dashed; a click on one shows its task, and dragging one onto another
  // puts it first (your order, data.order).
  const HOUR = 52;
  function meetings(data) {
    if (!(data.meetings || []).length && !data.refreshed) return `<p class="empty">Meetings come with the first refresh.</p>`;
    const p = plan(data), n = new Date(), now = n.getHours() * 60 + n.getMinutes();
    const items = [...(data.meetings || []).map((m) => ({ ...m, s: minutes(m.start), e: Math.max(minutes(m.end), minutes(m.start) + 15) })),
                   ...p.blocks.map((b) => ({ ...b, s: b.start, e: b.end }))].sort((a, b) => a.s - b.s || b.e - a.e);
    const top = Math.floor(Math.min(p.from, ...items.map((x) => x.s)) / 60), end = Math.ceil(Math.max(p.to, ...items.map((x) => x.e)) / 60);
    const y = (m) => (m - top * 60) * HOUR / 60;
    // columns: within each run of overlapping items, the first free column, all as wide as the run needs
    let run = [], runEnd = -1;
    const close = () => { const cols = Math.max(...run.map((x) => x.col)) + 1; run.forEach((x) => { x.cols = cols; }); run = []; };
    for (const x of items) {
      if (x.s >= runEnd && run.length) close();
      const used = new Set(run.filter((o) => o.e > x.s).map((o) => o.col));
      x.col = 0; while (used.has(x.col)) x.col++;
      run.push(x); runEnd = Math.max(runEnd, x.e);
    }
    if (run.length) close();
    let html = "";
    for (let h = top; h <= end; h++) html += `<div class="hr" style="top:${y(h * 60)}px"><span>${hhmm(h * 60)}</span></div>`;
    for (const x of items) {
      const short = x.e - x.s < 35 ? " short" : "";
      const box = `top:${y(x.s)}px;height:${Math.max(18, y(x.e) - y(x.s) - 2)}px;left:${x.col * 100 / x.cols}%;width:calc(${100 / x.cols}% - 3px)`;
      const tm = `<span class="tm">${hhmm(x.s)}–${hhmm(x.e)}</span>`;
      if (x.tasks) {
        const t = x.tasks[0], title = x.tasks.length > 1 ? `Triage · ${x.tasks.length} quick` : t.title;
        html += `<div class="blk plan ${x.view}${short}" style="${box}" draggable="true" data-task="${esc(t.id)}" data-ids="${esc(x.tasks.map((o) => o.id).join(" "))}" data-view="${x.view}" title="${esc(x.tasks.map((o) => o.title).join("\n"))}">${esc(title)} ${tm}</div>`;
      } else {
        html += `<div class="blk${short}${x.e <= now ? " past" : ""}" style="${box}">${x.link ? `<a href="${esc(x.link)}" target="_blank" rel="noopener" class="join">Join ↗</a>` : ""}${esc(x.title)} ${tm}</div>`;
      }
    }
    if (now >= top * 60 && now <= end * 60) html += `<div class="nowat" style="top:${y(now)}px"></div>`;
    const left = (p.left ? `<p class="meta">${p.left} more open task${p.left === 1 ? "" : "s"} don't fit before ${hhmm(p.to)}.</p>` : "")
      + `<p class="meta">Drag a block onto another to do it first.${(data.order || []).length ? ` <a href="#" data-order-reset>Clear your order</a>` : ""}</p>`;
    return `<div class="day" style="height:${y(end * 60) + 8}px">${html}</div>`
      + ((data.meetings || []).length ? "" : `<p class="meta">No meetings today.</p>`) + left;
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
    // the ones a task claims show on its row; the rest here, to make into a task (it comes to Triage)
    const loose = mine.filter((s) => !taskOf[s.title]);
    const part = (title, list) => list.length ? `<div class="sub">${title}</div>` + list.map((s) => row(s).replace(/<\/div><\/div>$/,
      `<button class="mk" data-session-task="${esc(s.title)}">Make it a task</button></div></div>`)).join("") : "";
    return part("Waiting for you", loose.filter((s) => s.state === "waiting")) + part("Running", loose.filter((s) => s.state === "running"))
      + part("Stopped mid-turn", loose.filter((s) => s.state === "stopped"));
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
    const listed = new Set((data.tasks || []).map((x) => x.id));  // on your list: shown on its row instead
    const shown = f.tasks.filter((t) => !listed.has("aag-" + t.id) && (col(t) !== "done" || new Date(t.statusAt || t.updatedAt).toLocaleDateString("sv") === data.today));
    const ln = (href, text) => href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${text}</a>` : "";
    const row = (t, tone) => {
      const p = t.pr;
      const facts = p ? [ln(p.url, esc(p.key.split("/")[1])), CI[p.ci], p.draft && "draft", p.conflicts && "conflicts",
                         p.review === "APPROVED" && "approved", p.unresolved && `${p.unresolved} unresolved`].filter(Boolean).join(" ") : "";
      // a working agent's next move is what it does now (t.now), with how long its run has gone on
      const since = t.now && Math.round((Date.now() - Date.parse(t.now.since)) / 60e3);
      const sub = [`<span class="st ${tone}">${esc(t.next)}</span>`, t.now && `run ${since < 60 ? since + " min" : Math.round(since / 60) + " h"}`, facts,
                   ln(t.dm, "DM"), ln(t.thread, "thread"), t.embargo && "embargo", t.cost >= 1 && "$" + Math.round(t.cost)].filter(Boolean).join(" · ");
      return `<div class="pr">${ln(t.dm || (p && p.url) || t.thread, esc(t.title)) || esc(t.title)}<div class="s">${sub}</div></div>`;
    };
    const body = AAG.map(([column, title, tone]) => {
      const l = shown.filter((t) => col(t) === column);
      return l.length ? `<div class="sub">${title}</div>` + l.map((t) => row(t, tone)).join("") : "";
    }).join("");
    if (!body && !head) return "";  // everything your agent does is on your list
    return head + body + `<p class="meta" style="margin:8px 0 0">${esc(f.host)}${a.at ? ` · read ${hhmm(a.at)}` : ""}</p>`;
  }
  // Two views of one list. Work: your real work in your order, one row per piece of work, with whoever is on
  // it: a Linear issue or review, deep work that isn't on Linear yet, and everything your agent does (its
  // task for an issue shows on the issue's row). Triage: the back-and-forth, and new work to place. The tab
  // is remembered in this browser.
  const work = (t) => !!(t.linear || (t.issue && !t.aag) || (t.aag && !t.issue) || (t.kind === "deep" && !t.issue));
  // who is on a piece of work: your agent's run (aag's feed) and the Claude sessions the refresh tied to it
  const feedOf = (d, t) => t.aag && (((d.aag || {}).feed || {}).tasks || []).find((x) => "aag-" + x.id === t.id);
  const sessionsOf = (d, t) => (t.sessions || []).map((n) => (d.sessions || []).find((s) => s.title === n && !s.agent)).filter(Boolean);
  const mins = (since) => { const m = Math.round((Date.now() - Date.parse(since)) / 60e3); return m < 60 ? m + " min" : Math.round(m / 60) + " h"; };
  function whoIsOn(d, t) {
    const f = feedOf(d, t), out = [];
    if (f && f.now) out.push(`<span class="st go">agent working · ${mins(f.now.since)}</span>`);
    else if (f && f.column === "needs_you") out.push(`<span class="st bad">agent: your move</span>`);
    else if (f && f.waiting) out.push(`agent waiting on ${esc(f.waiting.who === "owner" ? "you" : f.waiting.what || f.waiting.who)}`);
    else if (t.aag) out.push("your agent");
    const ses = sessionsOf(d, t), running = ses.filter((x) => x.state === "running").length;
    if (ses.length) out.push(`⌁ ${ses.length} session${ses.length > 1 ? "s" : ""}${running ? ` (${running} running)` : ""}`);
    return out;
  }
  // Work has what you put in your order; everything else, new work too, is in Triage, where you decide on
  // it (→ Work puts it in its place). Your agent's task for an issue shows on the issue's card.
  const inWork = (t, d) => work(t) && place(d, t) < Infinity;
  const triage = (t, d) => !inWork(t, d) && !(t.aag && t.issue);
  const issueOf = (d, t) => t.linear ? t : (d.tasks || []).find((x) => x.linear && x.linear.key === t.issue);
  const level = (d, t) => { const i = issueOf(d, t); return i ? [i.linear.level, i.linear.sort] : [9, 0]; };
  const ROLE = { assignee: "assigned to you", reviewer: "your review" };
  // how long a task takes, for the day's plan: yours (t.est, minutes), or 90 minutes of deep work, 10 quick
  const est = (t) => t.est || (t.aag ? 15 : work(t) ? 90 : 10);  // your agent does the work; yours is a check or a word
  const dur = (m) => m < 60 ? m + "m" : m % 60 ? (m / 60).toFixed(1) + "h" : m / 60 + "h";
  const EST = [15, 30, 60, 120, 240];
  // A new Linear issue for a task, filled in (Linear's linear.new parameters): your team, you as the assignee, its
  // priority, the next step and why as the description, and its links attached as links. Docket writes nothing to Linear.
  const LINEAR_PRIORITY = { P0: "Urgent", P1: "High", P2: "Medium" };
  function newIssue(d, t) {
    const q = new URLSearchParams({ title: t.title, description: [t.next, t.why].filter(Boolean).join("\n\n"), assignee: "me" });
    if (d.linear && d.linear.team) q.set("team", d.linear.team);
    if (LINEAR_PRIORITY[t.priority]) q.set("priority", LINEAR_PRIORITY[t.priority]);
    const links = (t.sources || []).filter((s) => s.link).map((s) => `${s.link}|${String(s.label).replace(/[,|]/g, " ")}`);
    if (links.length) q.set("links", links.join(","));
    return "https://linear.new?" + q;
  }
  // A row's PRs, after its Linear issue: #2648, with its state as gh read it (open ones you author or review)
  function prLinks(d, t) {
    const urls = [...new Set((t.sources || []).map((s) => (s.link || "").match(/^https:\/\/github\.com\/[^/]+\/[^/]+\/pull\/\d+/)?.[0]).filter(Boolean))];
    if (!urls.length) return "";
    const known = [...((d.prs || {}).items || []), ...((d.prs || {}).reviewing || [])];
    const one = (u) => {
      const p = known.find((x) => x.url === u), n = u.split("/").pop();
      return `<a href="${esc(u)}" target="_blank" rel="noopener">#${esc(n)}</a>${p ? ` <span class="st ${PR[p.state] || ""}">${esc(p.state)}</span>` : ""}`;
    };
    return urls.slice(0, 3).map(one).join(" · ") + (urls.length > 3 ? ` +${urls.length - 3}` : "");
  }
  // your agent's open tasks on the same work: its issue, or one of its PRs
  const prsOf = (t) => new Set((t.sources || []).map((s) => (s.link || "").match(/github\.com\/[^/]+\/[^/]+\/pull\/\d+/)?.[0]).filter(Boolean));
  function agentsOn(d, t) {
    const key = t.linear ? t.linear.key : t.issue, mine = prsOf(t);
    return (d.tasks || []).filter((x) => x.aag && x.id !== t.id && x.status !== "done" && x.status !== "dropped"
      && ((key && x.issue === key) || [...prsOf(x)].some((p) => mine.has(p))));
  }
  function moreWork(t, d, where) {
    const i = t.linear;
    const helpers = agentsOn(d, t);
    const create = newIssue(d, t);
    if (where === "row") {
      // the issue as Linear has it: its key and status (In Progress, In Review…), and whether it's your review
      const on = i || (t.issue && (issueOf(d, t) || {}).linear);
      const issue = on ? `${esc(on.key || t.issue)} <span class="lstate">${esc(on.state || "")}</span>${on.role === "reviewer" ? " · your review" : ""}` : t.issue && esc(t.issue);
      return [issue || `<span style="color:var(--warn)">not on Linear</span> <a href="${esc(create)}" target="_blank" rel="noopener">create ↗</a>`, prLinks(d, t),
              ...whoIsOn(d, t), ...helpers.map((x) => `agent: ${esc(x.next)}`),
              `<span class="${t.est ? "" : "meta"}">${dur(est(t))}</span>`].filter(Boolean).join(" · ");
    }
    const pick = `<div class="estpick"><span class="meta">Takes</span>${EST.map((m) => `<button data-act="est" data-value="${m}"${t.est === m ? ' class="on"' : ""}>${dur(m)}</button>`).join("")}${t.est ? "" : `<span class="meta">(${dur(est(t))} until you say)</span>`}</div>`;
    // each agent task on this work, with all its links (DM thread, PR, agents thread) the task doesn't have itself
    const has = new Set((t.sources || []).map((s) => s.link));
    const agentLines = helpers.map((x) => `<p class="why">Your agent: ${[esc(x.next), ...(x.sources || []).filter((s) => s.link && !has.has(s.link))
      .map((s) => `<a href="${esc(s.link)}" target="_blank" rel="noopener">${esc(s.label)} ↗</a>`)].join(" · ")}</p>`).join("");
    if (i) return `<div class="chips"><span class="chip">${esc(i.key)}</span><span class="chip">${ROLE[i.role] || ""}</span><span class="chip">${esc(i.state)}</span></div>` + agentLines + pick;
    if (t.issue) return agentLines + pick;
    return agentLines + pick + `<p class="meta" style="color:var(--warn)">Not on Linear · <a href="${esc(create)}" target="_blank" rel="noopener">create it ↗</a> · or name its key in a note</p>`;
  }
  function linearLine(d) {
    const l = d.linear;
    if (!l) return "";
    if (!l.key) return `<p class="meta">Work comes from Linear: put a personal API key in ${esc(d.folder)}/settings.json as <code>"linear_key"</code> (Linear: Settings → Security &amp; access).</p>`;
    return `<p class="meta">${l.error ? `<span class="err">Linear unreachable since ${esc(String(l.since || "").slice(11, 16))}: ${esc(l.error)}</span>` : l.at ? `Linear read ${esc(l.at.slice(11, 16))} · ${l.count} issue${l.count === 1 ? "" : "s"} and reviews` : "Reading Linear…"}</p>`;
  }
  let view = "triage"; try { view = localStorage.getItem("docket-view") || "triage"; } catch {}
  el.innerHTML = `<div class="views"><div class="seg viewtabs"><button data-view="triage">Triage <span class="count" data-vn="triage"></span></button><button data-view="work">Work <span class="count" data-vn="work"></span></button></div><span data-part="linear"></span></div>
    <div data-view-el="triage"></div><div data-view-el="work"></div>`;
  const box = (v) => el.querySelector(`[data-view-el="${v}"]`);
  let loading = null;  // the two views load the same state: one fetch for both
  const load = () => loading || (loading = fetch("/state").then((r) => r.json()).finally(() => setTimeout(() => { loading = null; }, 300)));
  const views = {};
  const both = async (v, p) => { const out = await p; for (const k in views) if (k !== v) views[k].reload(); return out; };
  function base(v) {
    return {
      assistant: opts.assistant,
      actions: ["done", "snooze", "waiting", "drop", "open"],
      minutes: true,
      load,
      act: (t, action, value) => both(v, act(t, action, value)),
      reply: (t, text) => both(v, post("/reply", { id: t.id, text })),
      refresh: () => opts.run && opts.run.click(),
      side: (d) => `<section class="card"><h2>Today</h2>${meetings(d)}</section>${d.aag ? (x => x ? `<section class="card"><h2>Your agent</h2>${x}</section>` : "")(agent(d)) : ""}<section class="card"><h2>Pull requests</h2>${prs(d)}</section>${(x => x ? `<section class="card"><h2>Sessions not on your list</h2>${x}</section>` : "")(sessions(d))}`,
      async sideClick(e, d) {
        const mk = e.target.closest("[data-session-task]");
        if (mk) return both(v, post("/task", { action: "add", value: mk.dataset.sessionTask, kind: "deep", sessions: [mk.dataset.sessionTask] }));
        if (e.target.closest("[data-order-reset]")) {
          e.preventDefault();
          if (!confirm("Clear your order? Its tasks go back to Triage (New for Work), to place again.")) return;
          return both(v, post("/task", { id: d.tasks[0].id, action: "order", value: [] }));
        }
        const blk = e.target.closest(".blk[data-task]");
        if (blk && !e.target.closest("a")) { show(blk.dataset.view); views[blk.dataset.view].select(blk.dataset.task); return; }
        const b = e.target.closest("button[data-prtab]"); if (!b) return;
        prTab = b.dataset.prtab; try { localStorage.setItem("docket-pr-tab", prTab); } catch {}
        return d;
      },
      foot: opts.foot || ((d) => `Kept on this computer in ${d.folder}.`),
      rendered,
    };
  }
  async function act(t, action, value) {
    if (action === "work" || action === "triage") {
      const out = await post("/task", { id: t.id, action: "kind", value: action === "work" ? "deep" : "quick" });
      const at = value && value.at;
      if (action !== "work" || at === undefined || at === "sort") return out;
      // into your order where you picked: above that task, or at the end
      const order = (out.order || []).filter((i) => i !== t.id), shown = workOrder(out, t);
      const before = typeof at === "number" && shown[at] ? order.indexOf(shown[at].id) : -1;
      order.splice(before < 0 ? order.length : before, 0, t.id);
      return post("/task", { id: t.id, action: "order", value: order });
    }
    if (action !== "restore") return post("/task", { id: t.id, action, value });
    // undo: back to the view, or the status and day, it had before
    const today = views.triage.data.today, cur = (views.triage.data.tasks || []).find((x) => x.id === t.id);
    if (cur && cur.kind !== value.kind && cur.status === value.status) return post("/task", { id: t.id, action: "kind", value: value.kind });
    if (value.status === "waiting") return post("/task", { id: t.id, action: "waiting" });
    const reopened = await post("/task", { id: t.id, action: "reopen" });
    const now = reopened.tasks.find((x) => x.id === t.id);
    const later = value.until && value.until > new Date().toLocaleString("sv").slice(0, 16).replace(" ", "T");  // a snooze that hasn't run out
    const back = later ? value.until : value.do < today ? today : value.do;
    return now && back && (back !== now.do || value.until) ? post("/task", { id: t.id, action: "snooze", value: back }) : reopened;
  }
  const due = (d, t) => t.status === "open" && (t.do || d.today) <= d.today && !(t.until && t.until > new Date().toLocaleString("sv").slice(0, 16).replace(" ", "T"));
  function rendered(d) {
    if (opts.run) { opts.run.disabled = !!d.running; opts.run.textContent = d.running ? "Reading…" : "Refresh"; }
    if (opts.status) opts.status.innerHTML = d.error ? `<span class="err">Last refresh failed: ${esc(d.error)}</span>`
      : d.running ? "Reading your accounts…" : d.refreshed ? `Updated ${esc(d.refreshed.slice(11, 16))}` : "Not refreshed yet";
    for (const [v, f] of [["triage", triage], ["work", inWork]]) el.querySelector(`[data-vn="${v}"]`).textContent = (d.tasks || []).filter((t) => f(t, d) && due(d, t)).length || "";
    const line = el.querySelector('[data-part="linear"]'), html = view === "work" ? linearLine(d) : "";
    if (line.innerHTML !== html) line.innerHTML = html;
    clearTimeout(window.poll);
    if (d.running || d.tasks.some((t) => t.asked) || (d.prs || {}).loading) window.poll = setTimeout(reloadAll, 5000);  // a refresh or an answer coming
  }
  // your Work order as it shows: the tasks in it that are still open or waiting
  const workOrder = (d, t) => (d.order || []).map((i) => (d.tasks || []).find((x) => x.id === i))
    .filter((x) => x && x.id !== (t && t.id) && work(x) && (x.status === "open" || x.status === "waiting"));
  views.triage = docket(box("triage"), {
    ...base("triage"), show: triage, rank: byOrder,
    actions: [...base("triage").actions, "work"],
    places: (t, d, a) => a === "work" ? workOrder(d, t).map((x) => ({ id: x.id, title: x.title, blocked: x.status === "waiting" || (x.do || d.today) > d.today })) : null,
    lists: [{ key: "next", title: "Up next", empty: "Nothing else due today.", pick: (l) => l }],
    listed: (t) => work(t),  // new work to place stays here, greyed while it waits, not folded away
    // new work to place: marked, with what Work would show; → Work puts it in its place in your order
    more: (t, d, where) => work(t) ? (where === "row" ? `<span class="chip">deep work · → Work</span> ` : "") + moreWork(t, d, where) : "",
    add: (text, kind) => both("triage", post("/task", { action: "add", value: text, kind })),
    empty: (d) => d.refreshed ? `<p class="title">Nothing due today.</p><p class="meta">Everything open is waiting on others or planned for later. Real work is under Work.</p>`
      : `<p class="title">No tasks yet.</p><p class="meta">The first refresh reads the last 3 days of whatever your Claude Code is connected to: Slack, email, calendar, GitHub, Linear, Notion. It takes a few minutes.</p>`,
  });
  views.work = docket(box("work"), {
    ...base("work"), show: inWork,
    actions: [...base("work").actions, "triage"],
    can: (t, a) => a !== "triage" || (!t.linear && !t.issue && !t.aag),  // an issue's and your agent's work stay Work
    queue: { heading: "Your order", empty: "Nothing in your order yet: new work comes in Triage, where → Work puts it in its place." },
    // your order as it is now, and what it had that isn't shown (snoozed, later) stays at the end
    order: (ids, shown) => both("work", post("/task", { id: ids[0] || shown[0], action: "order", value: [...ids, ...(views.work.data.order || []).filter((i) => !shown.includes(i))] })),
    // your order, then priority (Docket's own, after this), and Linear's order within one
    rank: (a, b, d) => { const x = level(d, a), y = level(d, b); return byOrder(a, b, d) || (a.priority === b.priority ? x[0] - y[0] || x[1] - y[1] : 0); },
    more: moreWork,
    empty: (d) => linearLine(d),
  });
  // Dragging a planned block onto another: its tasks go just before that block's, in the order the grid
  // shows now. Only the dragged block moves; tasks outside today's plan keep their places.
  let dragged = null;
  el.addEventListener("dragstart", (e) => { const b = e.target.closest?.(".blk.plan"); if (b) { dragged = b.dataset.ids.split(" "); e.dataTransfer.effectAllowed = "move"; } });
  el.addEventListener("dragover", (e) => { if (dragged && e.target.closest(".blk.plan")) e.preventDefault(); });
  el.addEventListener("drop", async (e) => {
    const target = e.target.closest(".blk.plan"); if (!dragged || !target) return;
    e.preventDefault();
    const before = target.dataset.ids.split(" ")[0];
    const ids = dragged; dragged = null;
    if (ids.includes(before)) return;
    try { await post("/task", { id: ids[0], action: "order_move", value: { ids, before } }); } catch (err) { alert(err.message); }
    await Promise.all(Object.values(views).map((x) => x.reload()));
    for (const k in views) views[k].select(null);  // Now: your new first one
  });
  el.addEventListener("dragend", () => { dragged = null; });
  function show(v) {
    view = v; try { localStorage.setItem("docket-view", v); } catch {}
    for (const k of ["triage", "work"]) { box(k).hidden = k !== v; el.querySelector(`[data-view="${k}"]`).classList.toggle("on", k === v); }
    views[v].render();
  }
  el.querySelector(".viewtabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-view]"); if (b) show(b.dataset.view); });
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || !el.offsetParent) return;
    if (e.key === "1" || e.key === "2") { show(e.key === "1" ? "triage" : "work"); e.preventDefault(); }
  });
  const reloadAll = () => { views.triage.reload(); views.work.reload(); };
  if (opts.run) opts.run.onclick = async () => { await post("/refresh"); reloadAll(); };
  show(view);
  reloadAll();
  setInterval(() => { if (document.visibilityState === "visible" && !views.triage.data?.running) reloadAll(); }, 60 * 1000);
  return views[view];
};
