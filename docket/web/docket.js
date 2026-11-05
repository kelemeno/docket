// Docket's page: one task in Now, Quick and Deep work below, Waiting / Later / Handled folded, the day
// on the side. The page hands in a source that knows its data, so the same page can sit on any
// task list:
//
//   docket(element, {
//     load(): data        {today, tasks, refreshed?, running?, error?, ...}; a task: {id, title, next?, why?,
//                         who?, kind quick|deep, priority? P0-P2, star?, status open|waiting|done|dropped,
//                         do?, rolled?, sources? [{label, link}], sessions?, done_at?, closed_by?, evidence?}
//     act(task, action, value): data    done, drop, snooze (a day), waiting, star, restore (the task as
//                         it was, for undo)
//     add(text, kind): data
//     minutes: true                       snooze also offers 30 minutes and an hour (task.until)
//     assistant: "Claude"                 what the reply box and notes call the one answering
//     reply(task, text): data             optional: a note to Claude about the task; task.notes [{at, by
//                         you|claude, text}] show on the card, task.asked while the answer is coming
//     actions: ["done", "snooze", "waiting", "drop", "open", "star"]   the buttons this source has; and
//                         "work" / "triage" for a page with those two views (act gets them as they are)
//     can(task, action): bool         optional: a button this task doesn't get
//     side(data): html    the side panel;  sideClick(event, data): data or undefined
//     empty(data): html   Now when there's nothing to do;  foot(data): text under the side panel
//     show(task, data): bool          optional: the tasks this view has (a page with several views)
//     lists: [{key, title, empty, pick(open tasks): tasks}]   optional: the lists below Now, instead of
//                         Quick and Deep work (Waiting, Later and Handled stay)
//     rank(a, b, data): number        optional: comes before the usual order (★, priority, slips, age)
//     more(task, data, where): html   optional: more on a task, where "now" (its card) or "row"
//     queue: {heading, title, empty, unsorted(task, data), unsortedTitle}   optional: no Now card; one
//                         list in your order, waiting ones in it greyed, the first you can act on marked
//                         next, and above it, past a divider, the tasks not in your order yet. A row
//                         opens in place (click, space); drag rows, Shift+J/K or To top / To end to
//                         place them, which calls order(task ids in your order, task ids shown): data
//   Buttons with data-act="action" data-value="v" in more(…, "now") call act(task, action, v).
//   })  →  {reload(), render(), select(task id), data}
(() => {
  const PRI = ["P0", "P1", "P2"];
  const day = (d) => new Date(d + "T12:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  const link = (s) => s.link ? `<a href="${esc(s.link)}" target="_blank" rel="noopener">${esc(s.label)} ↗</a>` : esc(s.label);
  const LABEL = { done: "Done", drop: "Dropped", waiting: "Waiting on others", snooze: "Snoozed", star: "Starred",
                  work: "Moved to Work", triage: "Moved to Triage" };
  const KEYS = { done: "d", snooze: "s", waiting: "w", drop: "x", open: "o", star: "t", work: "m", triage: "m" };
  const NAMES = { done: "Done", snooze: "Snooze", waiting: "Waiting", drop: "Drop", open: "Open", star: "★ Today",
                  work: "→ Work", triage: "→ Triage" };

  window.docket = function (el, src) {
    const ai = src.assistant || "Claude";
    const Q = src.queue;
    const LISTS = Q ? [{ key: "queue", title: Q.heading || Q.title || "In order", empty: Q.empty || "Nothing to do." }] : src.lists || [{ key: "quick", title: "Quick · under 10 min", empty: "No quick items today.", pick: (l) => l.filter((t) => t.kind === "quick") },
                                { key: "deep", title: "Deep work", empty: "No deep work for today.", pick: (l) => l.filter((t) => t.kind !== "quick") }];
    const show = (t) => !src.show || src.show(t, data);
    const more = (t, where) => src.more ? src.more(t, data, where) || "" : "";
    // on the screen: its first box, since a page may lay el out with display: contents (no box of its own)
    const onScreen = () => !!(el.firstElementChild && el.firstElementChild.offsetParent);
    const can = (t, a) => src.actions.includes(a) && (!src.can || src.can(t, a));
    el.innerHTML = `
      <main>
        ${Q ? "" : `<section class="card now" data-part="now"></section>`}
        ${src.add ? `<form class="card add" data-part="add">
          <input placeholder="Add a task and press Enter  (n)" autocomplete="off" aria-label="New task">
          <select aria-label="Kind"><option value="quick">Quick</option><option value="deep">Deep work</option></select>
        </form>` : ""}
        ${LISTS.map((l) => `<section class="card"><h2>${esc(l.title)} <span class="count" data-n="${esc(l.key)}"></span></h2><div data-list="${esc(l.key)}"${Q ? " data-q" : ""}></div></section>`).join("")}
        ${src.actions.includes("waiting") && !Q ? `<details class="card"><summary>Waiting on others <span class="count" data-n="waiting"></span></summary><div data-list="waiting"></div></details>` : ""}
        ${Q ? "" : `<details class="card"><summary>Later <span class="count" data-n="later"></span></summary><div data-list="later"></div></details>`}
        <details class="card"><summary>Handled today <span class="count" data-n="handled"></span></summary><div data-list="handled"></div></details>
        <p class="meta">Keys: <kbd>j</kbd><kbd>k</kbd> move · ${Q ? "<kbd>space</kbd> open · <kbd>J</kbd><kbd>K</kbd> reorder · " : ""}${src.actions.filter((a) => KEYS[a]).map((a) => `<kbd>${KEYS[a]}</kbd> ${NAMES[a].replace("★ ", "").toLowerCase()}`).join(" · ")}
          ${src.reply ? "· <kbd>c</kbd> reply" : ""} · <kbd>u</kbd> undo · <kbd>n</kbd> new task${src.refresh ? " · <kbd>r</kbd> refresh" : ""}</p>
      </main>
      <aside><div data-part="side"></div><p class="meta" data-part="foot"></p></aside>`;
    // the card: Now, or in a queue the open row's details, which move under that row
    const nowEl = Q ? Object.assign(document.createElement("section"), { className: "card now detail" }) : el.querySelector('[data-part="now"]');
    if (Q) nowEl.dataset.part = "now";
    const part = (n) => n === "now" ? nowEl : el.querySelector(`[data-part="${n}"]`);
    let data = null, sel = null, lastUndo = null, offline = false, opened = null;

    const stamp = (d) => d.toLocaleString("sv").slice(0, 16).replace(" ", "T");  // local YYYY-MM-DDTHH:MM
    const due = (t) => (t.do || data.today) <= data.today && !(t.until && t.until > stamp(new Date()));
    const when = (t) => t.until && t.until.startsWith(data.today) ? "until " + t.until.slice(11) : day(t.do);
    const rank = (a, b) => (src.rank ? src.rank(a, b, data) : 0) || (b.star ? 1 : 0) - (a.star ? 1 : 0) || (PRI.indexOf(a.priority) + 4) % 4 - (PRI.indexOf(b.priority) + 4) % 4
      || (b.rolled || 0) - (a.rolled || 0) || String(a.created).localeCompare(String(b.created));
    // a queue's two lists: what isn't in your order yet, and your order
    const queued = (all) => {
      const out = Q.unsorted ? all.filter((t) => Q.unsorted(t, data)) : [];
      return { unsorted: out, queue: all.filter((t) => !out.includes(t)) };
    };
    function groups() {
      const tasks = (data.tasks || []).filter(show);
      const open = tasks.filter((t) => t.status === "open" && due(t)).sort(rank);
      const waiting = tasks.filter((t) => t.status === "waiting").sort(rank);
      const later = tasks.filter((t) => t.status === "open" && !due(t));
      return {
        // a queue has them all in one list: waiting and later ones greyed in their place
        open, lists: Q ? queued([...open, ...waiting, ...later].sort(rank)) : Object.fromEntries(LISTS.map((l) => [l.key, l.pick(open)])),
        waiting: Q ? [] : waiting,
        later: Q ? [] : later.sort((a, b) => (a.until || a.do).localeCompare(b.until || b.do) || rank(a, b)),
        handled: tasks.filter((t) => (t.status === "done" || t.status === "dropped") && String(t.done_at || "").startsWith(data.today)),
      };
    }
    const pri = (t) => t.star ? `<span class="pri star">★</span>` : `<span class="pri ${esc(t.priority || "")}">${esc(t.priority || "")}</span>`;
    function chips(t) {
      const c = [];
      if (t.star) c.push(`<span class="chip">★ today</span>`);
      if (t.priority) c.push(`<span class="chip ${esc(t.priority)}">${esc(t.priority)}</span>`);
      c.push(`<span class="chip">${t.kind === "quick" ? "quick" : "deep work"}</span>`);
      if (t.who) c.push(`<span class="chip">for ${esc(t.who)}</span>`);
      if (t.rolled) c.push(`<span class="chip slip">slipped ${t.rolled}×</span>`);
      if (t.status === "waiting") c.push(`<span class="chip">waiting on others</span>`);
      if (t.do > data.today) c.push(`<span class="chip">planned ${esc(day(t.do))}</span>`);
      else if (!due(t)) c.push(`<span class="chip">snoozed ${esc(when(t))}</span>`);
      return `<div class="chips">${c.join("")}</div>`;
    }
    function notes(t) {
      const list = (t.notes || []).slice(-4).map((n) => `<div class="note ${n.by === "you" ? "you" : "claude"}"><span class="by">${n.by === "you" ? "You" : esc(ai)}</span> ${esc(n.text)}</div>`);
      if (t.asked) list.push(`<div class="note claude waiting"><span class="by">${esc(ai)}</span> is looking into it…</div>`);
      return list.length ? `<div class="notes">${list.join("")}</div>` : "";
    }
    // A part is rebuilt only when what it shows changed: the page reloads its data every few seconds
    // while a refresh runs, and rebuilding the Now card each time ate clicks on Send (the button was
    // replaced between mouse-down and mouse-up), closed the snooze menu and moved the caret.
    const shown = new WeakMap();
    function put(node, html) {
      if (shown.get(node) === html) return false;
      node.innerHTML = html; shown.set(node, html);
      return true;
    }
    function renderNow(g) {
      const t = Q ? data.tasks.find((x) => x.id === opened && (x.status === "open" || x.status === "waiting") && show(x))
        : data.tasks.find((x) => x.id === sel && (x.status === "open" || x.status === "waiting") && show(x)) || g.open[0];
      if (Q && !t) { opened = null; nowEl.remove(); return; }
      // a reload mustn't eat a reply you're typing
      const box = part("now").querySelector(".reply input"), draft = box && box.dataset.id === (t && t.id) ? box.value : "",
            typing = box && document.activeElement === box;
      if (!t) { sel = null; put(part("now"), `<h2>Now</h2>${src.empty(data)}`); return; }
      sel = t.id;
      const btn = (a) => !can(t, a) || a === "open" && !(t.sources || []).some((s) => s.link) ? ""
        : `<button data-a="${a}"${a === "done" ? ' class="primary"' : ""}>${a === "star" && t.star ? "Unstar" : NAMES[a]}<kbd>${KEYS[a]}</kbd></button>`;
      const ses = (t.sessions || []).map((s) => `<span class="chip">⌁ ${esc(s)}</span>`).join("");
      if (!put(part("now"), `${Q ? "" : "<h2>Now</h2>"}
        ${Q ? "" : `<p class="title">${esc(t.title)}</p>`}
        ${t.next ? `<p class="next">→ ${esc(t.next)}</p>` : ""}${t.why ? `<p class="why">${esc(t.why)}</p>` : ""}
        ${chips(t)}${more(t, "now")}
        ${(t.sources || []).length ? `<div class="links">${t.sources.map(link).join("")}</div>` : ""}
        ${ses ? `<div class="chips">${ses}</div>` : ""}
        ${src.reply ? notes(t) + `<form class="reply"><input data-id="${esc(t.id)}" autocomplete="off" aria-label="Reply to ${esc(ai)} about this task"
          placeholder="Reply to ${esc(ai)}: what's changed, who to ask, where…  (c)"><button>Send</button></form>` : ""}
        <div class="actions">${src.actions.map(btn).join("")}
          <div class="snooze hidden" data-part="snooze">
            ${src.minutes ? `<button data-snooze="m30">30 min</button><button data-snooze="m60">1 hour</button>` : ""}<button data-snooze="1">Tomorrow</button><button data-snooze="mon">Monday</button><button data-snooze="7">Next week</button>
            <input type="date" min="${esc(data.today)}" aria-label="Snooze until">
          </div></div>`)) return;  // unchanged: the box, what's typed in it and the caret stay as they are
      const input = part("now").querySelector(".reply input");
      if (input && draft) input.value = draft;
      if (input && typing) input.focus();
    }
    function rows(list, opts = {}) {
      if (!list.length) return `<p class="empty">${opts.empty || "Nothing."}</p>`;
      const next = opts.queue && list.find((t) => t.id === opts.next);
      return list.map((t) => {
        // a queue's dividers: what's to sort above, your order below (dropping on it puts a task first)
        if (t.sep) return `<div class="qsep${t.sep === "order" ? " drop" : ""}"${t.sep === "order" ? " data-sep" : ""}>${esc(t.title)} <span class="count">${t.n || ""}</span>${t.hint ? ` <span class="meta">${esc(t.hint)}</span>` : ""}</div>`;
        const s0 = (t.sources || [])[0];
        const sub = opts.queue ? [more(t, "row"), t.rolled ? `<span style="color:var(--warn)">slipped ${t.rolled}×</span>` : "", t.status === "waiting" ? "waiting on others" : "",
                                  t.status === "open" && !due(t) ? (t.until && t.until.startsWith(data.today) ? "until " + esc(t.until.slice(11)) : "from " + esc(day(t.do))) : ""].filter(Boolean).join(" · ")
          : [t.next ? "→ " + esc(t.next) : "", s0 ? link(s0) : "", opts.later ? (t.until && t.until.startsWith(data.today) ? "" : "on ") + esc(when(t)) : "",
                     t.rolled ? `<span style="color:var(--warn)">slipped ${t.rolled}×</span>` : "",
                     opts.closed ? (t.status === "dropped" ? "dropped" : t.closed_by === "claude" ? "done: " + esc(t.evidence) : "done") : "", more(t, "row")]
          .filter(Boolean).join(" · ");
        const subline = sub + (t.asked ? ` · <span class="meta">${esc(ai)} is reading your note…</span>` : "");
        const quick = opts.closed ? `<button data-row="restore" title="Put it back">Undo</button>`
          : opts.unsorted && opts.unsorted.has(t.id) ? `<button data-row="top" title="First in your order">To top</button><button data-row="end" title="Last in your order">To end</button>`
          : `<button data-row="done" title="Done" aria-label="Done">✓</button><button data-row="snooze1" title="Tomorrow" aria-label="Snooze to tomorrow">→</button>`;
        const cls = (t.id === sel ? " sel" : "") + (opts.closed ? " closed" : "") + (opts.queue && (t.status === "waiting" || !due(t)) ? " blocked" : "") + (opts.unsorted?.has(t.id) ? " tosort" : "")
          + (t === next ? " next" : "") + (opts.queue && t.id === opened ? " opened" : "");
        return `<div class="row${cls}" data-id="${esc(t.id)}">${opts.queue ? `<span class="grip" title="Drag to reorder (or Shift+J / Shift+K)">⠿</span>` : ""}${pri(t)}
          <div class="body"><div class="t">${t === next ? `<span class="nextmark">next</span>` : ""}${esc(t.title)}</div>${subline ? `<div class="s">${subline}</div>` : ""}</div>
          <div class="quick">${quick}</div></div>`;
      }).join("");
    }
    function render() {
      if (!data || drag?.on) return;  // not mid-drag: the rows under the pointer stay put
      const g = groups();
      renderNow(g);
      const fill = (k, list, opts) => {
        const box = el.querySelector(`[data-list="${k}"]`); if (!box) return;
        put(box, rows(list, opts)); el.querySelector(`[data-n="${k}"]`).textContent = list.filter((t) => !t.sep).length || "";
      };
      // in a queue, next is the first you can act on in your order, else in what's to sort
      const next = Q && (g.lists.queue.find((t) => t.status === "open" && due(t)) || g.lists.unsorted.find((t) => t.status === "open" && due(t)));
      // a queue with tasks to sort shows them first, then your order, in one list with a divider between
      const shownQueue = Q && g.lists.unsorted.length
        ? [{ sep: "sort", title: Q.unsortedTitle || "To sort", n: g.lists.unsorted.length }, ...g.lists.unsorted,
           { sep: "order", title: Q.title || "In order", n: g.lists.queue.length, hint: g.lists.queue.length ? "drop here to put it first" : "drag tasks here" }, ...g.lists.queue]
        : Q && g.lists.queue;
      for (const l of LISTS) fill(l.key, Q ? shownQueue : g.lists[l.key], { empty: l.empty, queue: !!Q, unsorted: Q && new Set(g.lists.unsorted.map((t) => t.id)), next: next && next.id });
      if (Q && opened) { const r = el.querySelector(`[data-q] .row[data-id="${CSS.escape(opened)}"]`); if (r && r.nextSibling !== nowEl) r.after(nowEl); }
      fill("waiting", g.waiting); fill("later", g.later, { later: true }); fill("handled", g.handled, { closed: true, empty: "Nothing yet." });
      put(part("side"), src.side(data));
      part("foot").textContent = src.foot ? src.foot(data) : "";
      if (src.rendered) src.rendered(data);
    }

    async function act(t, action, value, label, keep) {
      const before = { ...t };
      try { data = await src.act(t, action, value); } catch (e) { return toast(e.message || "That didn't work."); }
      if (label) { lastUndo = before; toast(`${label}: ${t.title}`, true); }
      if (sel === t.id && action !== "star" && !keep) sel = null;
      render();
    }
    async function undo() {
      if (!lastUndo) return;
      const t = lastUndo; lastUndo = null;
      try { data = await src.act(t, "restore", t); } catch (e) { return toast(e.message || "That didn't work."); }
      sel = t.id; render(); toast("Undone");
    }
    const current = () => data && data.tasks.find((x) => x.id === sel);
    function snoozeDay(v) {
      if (v[0] === "m") return stamp(new Date(Date.now() + +v.slice(1) * 60e3));
      const d = new Date(data.today + "T12:00");
      if (v === "mon") d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7)); else d.setDate(d.getDate() + +v);
      return d.toLocaleDateString("sv");
    }
    function nowAction(a) {
      const t = current(); if (!t || !can(t, a)) return;
      if (a === "open") { const s = (t.sources || []).find((x) => x.link); if (s) window.open(s.link, "_blank", "noopener"); return; }
      if (a === "snooze") { part("snooze")?.classList.toggle("hidden"); return; }
      act(t, a, null, a === "star" ? null : LABEL[a]);
    }
    part("now").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.a) nowAction(b.dataset.a);
      if (b.dataset.snooze && current()) act(current(), "snooze", snoozeDay(b.dataset.snooze), "Snoozed");
      if (b.dataset.act && current()) act(current(), b.dataset.act, b.dataset.value, null, true);
    });
    part("now").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = e.target.querySelector("input"), text = input.value.trim(), t = current(); if (!text || !t) return;
      input.disabled = true;
      try { data = await src.reply(t, text); } catch (err) { input.disabled = false; return toast(err.message || "That didn't work."); }
      input.value = ""; input.blur(); render();
    });
    part("now").addEventListener("keydown", (e) => { if (e.key === "Escape" && e.target.tagName === "INPUT") e.target.blur(); });
    part("now").addEventListener("change", (e) => { if (e.target.type === "date" && e.target.value && current()) act(current(), "snooze", e.target.value, "Snoozed"); });
    el.querySelector("main").addEventListener("click", (e) => {
      const r = e.target.closest(".row"); if (!r || e.target.closest("a") || dragged) return;
      const t = data.tasks.find((x) => x.id === r.dataset.id); if (!t) return;
      const b = e.target.closest("button");
      if (b && b.dataset.row === "done") return act(t, "done", null, "Done");
      if (b && b.dataset.row === "snooze1") return act(t, "snooze", snoozeDay(1), "Snoozed");
      if (b && b.dataset.row === "restore") return act(t, "restore", { ...t, status: "open", done_at: null }, "Back on the list");
      if (b && (b.dataset.row === "top" || b.dataset.row === "end")) {
        const ids = groups().lists.queue.map((x) => x.id);
        return reorder(b.dataset.row === "top" ? [t.id, ...ids] : [...ids, t.id]);
      }
      if (Q && r.closest("[data-q]")) { sel = t.id; opened = opened === t.id ? null : t.id; return render(); }
      sel = t.id; render(); scrollTo({ top: 0, behavior: "smooth" });
    });
    // a queue's order: drag a row onto another (above its middle: before it; below: after it)
    async function reorder(ids) {
      const g = groups(), shown = [...g.lists.unsorted, ...g.lists.queue].map((t) => t.id);
      try { data = await src.order(ids, shown); } catch (e) { return toast(e.message || "That didn't work."); }
      render();
    }
    // dragging a row (pointer events, not the browser's drag and drop: a redraw mid-drag can't cancel it, and the page
    // scrolls near its edges): onto the divider, first in your order; onto a row of your order, above or below it;
    // onto one to sort, out of your order. A press that doesn't move is a click.
    let drag = null, dragged = false;
    const marks = () => el.querySelectorAll(".dropbefore, .dropafter").forEach((x) => x.classList.remove("dropbefore", "dropafter"));
    function drop(moved, over, after) {
      const g = groups(), ids = g.lists.queue.map((t) => t.id).filter((i) => i !== moved);
      if (over.dataset.sep !== undefined) ids.unshift(moved);
      else if (ids.includes(over.dataset.id)) ids.splice(ids.indexOf(over.dataset.id) + (after ? 1 : 0), 0, moved);
      else if (!g.lists.unsorted.some((t) => t.id === over.dataset.id)) return;
      reorder(ids);
    }
    if (Q) {
      el.addEventListener("pointerdown", (e) => {
        const r = e.target.closest("[data-q] .row");
        if (!r || e.button !== 0 || e.target.closest("a, button, input, select")) return;
        if (e.target.closest(".grip")) e.preventDefault();  // the grip is only for dragging: no text selection from it
        drag = { id: r.dataset.id, row: r, x: e.clientX, y: e.clientY, at: e.clientY, on: false };
      });
      addEventListener("pointermove", (e) => {
        if (!drag) return;
        if (!drag.on && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 6) return;
        if (!drag.on) {
          drag.on = true; drag.row.classList.add("dragging"); document.body.classList.add("dragging-row");
          getSelection()?.removeAllRanges();  // the text the press began selecting
          drag.scroll = setInterval(() => { const edge = 70; if (drag && drag.at < edge) scrollBy(0, -14); else if (drag && drag.at > innerHeight - edge) scrollBy(0, 14); }, 16);
        }
        e.preventDefault();
        drag.at = e.clientY;
        const over = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-q] .row, [data-q] [data-sep]");
        marks(); drag.over = null;
        if (over && over.dataset.id !== drag.id) {
          drag.after = over.dataset.sep !== undefined || e.clientY > over.getBoundingClientRect().top + over.offsetHeight / 2;
          over.classList.add(drag.after ? "dropafter" : "dropbefore"); drag.over = over;
        }
      });
      const end = (cancel) => {
        if (!drag) return;
        const d = drag; drag = null;
        clearInterval(d.scroll); marks(); d.row.classList.remove("dragging"); document.body.classList.remove("dragging-row");
        if (!d.on) return;  // a click
        dragged = true; setTimeout(() => { dragged = false; }, 0);  // the click that follows isn't one
        if (!cancel && d.over) drop(d.id, d.over, d.after); else render();
      };
      addEventListener("pointerup", () => end(false));
      addEventListener("pointercancel", () => end(true));
      addEventListener("keydown", (e) => { if (e.key === "Escape" && drag?.on) end(true); });
    }
    part("side").addEventListener("click", async (e) => {
      if (!src.sideClick) return;
      const out = await src.sideClick(e, data);
      if (out) { data = out; render(); }
    });
    if (src.add) part("add").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = part("add").querySelector("input"), text = input.value.trim(); if (!text) return;
      try { data = await src.add(text, part("add").querySelector("select").value); } catch (err) { return toast(err.message || "That didn't work."); }
      input.value = ""; input.blur(); render();  // the keys work on the list again
    });
    if (src.add) part("add").addEventListener("keydown", (e) => { if (e.key === "Escape") e.target.blur(); });
    document.addEventListener("keydown", (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || !data || !onScreen()) return;
      const g = groups(), list = Q ? [...g.lists.unsorted, ...g.lists.queue] : LISTS.flatMap((l) => g.lists[l.key]), i = list.findIndex((t) => t.id === sel);
      const k = e.shiftKey && /^[jk]$/i.test(e.key) ? e.key.toUpperCase() : e.key;  // Shift+J may come as "j" with shift
      const action = Object.keys(KEYS).find((a) => KEYS[a] === k && src.actions.includes(a));
      if (k === "j" || k === "k") { const n = list[Math.max(0, Math.min(list.length - 1, i + (k === "j" ? 1 : -1)))]; if (n) { sel = n.id; if (opened) opened = n.id; render(); } }
      else if (Q && (k === "J" || k === "K") && g.lists.queue.some((t) => t.id === sel)) {
        const ids = g.lists.queue.map((t) => t.id), at = ids.indexOf(sel), j = Math.max(0, Math.min(ids.length - 1, at + (k === "J" ? 1 : -1)));
        if (j === at) return;
        ids.splice(j, 0, ...ids.splice(at, 1)); reorder(ids);
      }
      else if (Q && (k === "J" || k === "K") && i >= 0) {  // to sort: Shift+K puts it first in your order, Shift+J last
        const ids = g.lists.queue.map((t) => t.id);
        reorder(k === "K" ? [sel, ...ids] : [...ids, sel]);
      }
      else if (Q && (k === " " || k === "Enter") && sel) { opened = opened === sel ? null : sel; render(); }
      else if (action) nowAction(action);
      else if (k === "u") undo();
      else if (k === "n" && src.add) part("add").querySelector("input").focus();
      else if (k === "c" && src.reply && Q && sel && opened !== sel) { opened = sel; render(); part("now").querySelector(".reply input")?.focus(); }
      else if (k === "c" && src.reply && part("now").querySelector(".reply input")) part("now").querySelector(".reply input").focus();
      else if (k === "r" && src.refresh) src.refresh();
      else if (k === "Escape") { if (Q && opened && part("snooze")?.classList.contains("hidden") !== false) { opened = null; render(); } else part("snooze")?.classList.add("hidden"); }
      else return;
      e.preventDefault();
    });
    let toastTimer;
    function toast(text, canUndo) {
      document.querySelector(".toast")?.remove();
      const t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status");
      t.innerHTML = esc(text) + (canUndo ? `<button>Undo</button>` : "");
      t.querySelector("button")?.addEventListener("click", () => { t.remove(); undo(); });
      document.body.appendChild(t); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.remove(), 5000);
    }
    async function reload() {
      try { data = await src.load(); offline = false; } catch (e) {
        // the server restarting (an update) mustn't wipe the page: keep what's shown, and say so once
        if (!data) Q ? toast(String(e.message || e)) : put(part("now"), `<h2>Now</h2><p class="err">${esc(e.message || e)}</p>`);
        else if (!offline) { offline = true; toast("Can't reach the page's server just now. It catches up by itself."); }
        return;
      }
      render();
    }
    return { reload, render, select(id) { sel = id; if (Q) opened = id; render(); }, get data() { return data; } };
  };
})();
