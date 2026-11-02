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
//     actions: ["done", "snooze", "waiting", "drop", "open", "star"]   the buttons this source has
//     side(data): html    the side panel;  sideClick(event, data): data or undefined
//     empty(data): html   Now when there's nothing to do;  foot(data): text under the side panel
//   })  →  {reload(), render()}
(() => {
  const PRI = ["P0", "P1", "P2"];
  const day = (d) => new Date(d + "T12:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  const link = (s) => s.link ? `<a href="${esc(s.link)}" target="_blank" rel="noopener">${esc(s.label)} ↗</a>` : esc(s.label);
  const LABEL = { done: "Done", drop: "Dropped", waiting: "Waiting on others", snooze: "Snoozed", star: "Starred" };
  const KEYS = { done: "d", snooze: "s", waiting: "w", drop: "x", open: "o", star: "t" };
  const NAMES = { done: "Done", snooze: "Snooze", waiting: "Waiting", drop: "Drop", open: "Open", star: "★ Today" };

  window.docket = function (el, src) {
    const ai = src.assistant || "Claude";
    el.innerHTML = `
      <main>
        <section class="card now" data-part="now"></section>
        <form class="card add" data-part="add">
          <input placeholder="Add a task and press Enter  (n)" autocomplete="off" aria-label="New task">
          <select aria-label="Kind"><option value="quick">Quick</option><option value="deep">Deep work</option></select>
        </form>
        <section class="card"><h2>Quick · under 10 min <span class="count" data-n="quick"></span></h2><div data-list="quick"></div></section>
        <section class="card"><h2>Deep work <span class="count" data-n="deep"></span></h2><div data-list="deep"></div></section>
        ${src.actions.includes("waiting") ? `<details class="card"><summary>Waiting on others <span class="count" data-n="waiting"></span></summary><div data-list="waiting"></div></details>` : ""}
        <details class="card"><summary>Later <span class="count" data-n="later"></span></summary><div data-list="later"></div></details>
        <details class="card"><summary>Handled today <span class="count" data-n="handled"></span></summary><div data-list="handled"></div></details>
        <p class="meta">Keys: <kbd>j</kbd><kbd>k</kbd> move · ${src.actions.filter((a) => KEYS[a]).map((a) => `<kbd>${KEYS[a]}</kbd> ${NAMES[a].replace("★ ", "").toLowerCase()}`).join(" · ")}
          ${src.reply ? "· <kbd>c</kbd> reply" : ""} · <kbd>u</kbd> undo · <kbd>n</kbd> new task${src.refresh ? " · <kbd>r</kbd> refresh" : ""}</p>
      </main>
      <aside><div data-part="side"></div><p class="meta" data-part="foot"></p></aside>`;
    const part = (n) => el.querySelector(`[data-part="${n}"]`);
    let data = null, sel = null, lastUndo = null;

    const stamp = (d) => d.toLocaleString("sv").slice(0, 16).replace(" ", "T");  // local YYYY-MM-DDTHH:MM
    const due = (t) => (t.do || data.today) <= data.today && !(t.until && t.until > stamp(new Date()));
    const when = (t) => t.until && t.until.startsWith(data.today) ? "until " + t.until.slice(11) : day(t.do);
    const rank = (a, b) => (b.star ? 1 : 0) - (a.star ? 1 : 0) || (PRI.indexOf(a.priority) + 4) % 4 - (PRI.indexOf(b.priority) + 4) % 4
      || (b.rolled || 0) - (a.rolled || 0) || String(a.created).localeCompare(String(b.created));
    function groups() {
      const tasks = data.tasks || [];
      const open = tasks.filter((t) => t.status === "open" && due(t)).sort(rank);
      return {
        open, quick: open.filter((t) => t.kind === "quick"), deep: open.filter((t) => t.kind !== "quick"),
        waiting: tasks.filter((t) => t.status === "waiting").sort(rank),
        later: tasks.filter((t) => t.status === "open" && !due(t)).sort((a, b) => (a.until || a.do).localeCompare(b.until || b.do) || rank(a, b)),
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
    function renderNow(g) {
      const t = data.tasks.find((x) => x.id === sel && (x.status === "open" || x.status === "waiting")) || g.open[0];
      // a reload mustn't eat a reply you're typing
      const box = part("now").querySelector(".reply input"), draft = box && box.dataset.id === (t && t.id) ? box.value : "",
            typing = box && document.activeElement === box;
      if (!t) { sel = null; part("now").innerHTML = `<h2>Now</h2>${src.empty(data)}`; return; }
      sel = t.id;
      const btn = (a) => a === "open" && !(t.sources || []).some((s) => s.link) ? ""
        : `<button data-a="${a}"${a === "done" ? ' class="primary"' : ""}>${a === "star" && t.star ? "Unstar" : NAMES[a]}<kbd>${KEYS[a]}</kbd></button>`;
      const ses = (t.sessions || []).map((s) => `<span class="chip">⌁ ${esc(s)}</span>`).join("");
      part("now").innerHTML = `<h2>Now</h2>
        <p class="title">${esc(t.title)}</p>
        ${t.next ? `<p class="next">→ ${esc(t.next)}</p>` : ""}${t.why ? `<p class="why">${esc(t.why)}</p>` : ""}
        ${chips(t)}
        ${(t.sources || []).length ? `<div class="links">${t.sources.map(link).join("")}</div>` : ""}
        ${ses ? `<div class="chips">${ses}</div>` : ""}
        ${src.reply ? notes(t) + `<form class="reply"><input data-id="${esc(t.id)}" autocomplete="off" aria-label="Reply to ${esc(ai)} about this task"
          placeholder="Reply to ${esc(ai)}: what's changed, who to ask, where…  (c)"><button>Send</button></form>` : ""}
        <div class="actions">${src.actions.map(btn).join("")}
          <div class="snooze hidden" data-part="snooze">
            ${src.minutes ? `<button data-snooze="m30">30 min</button><button data-snooze="m60">1 hour</button>` : ""}<button data-snooze="1">Tomorrow</button><button data-snooze="mon">Monday</button><button data-snooze="7">Next week</button>
            <input type="date" min="${esc(data.today)}" aria-label="Snooze until">
          </div></div>`;
      const input = part("now").querySelector(".reply input");
      if (input && draft) input.value = draft;
      if (input && typing) input.focus();
    }
    function rows(list, opts = {}) {
      if (!list.length) return `<p class="empty">${opts.empty || "Nothing."}</p>`;
      return list.map((t) => {
        const s0 = (t.sources || [])[0];
        const sub = [t.next ? "→ " + esc(t.next) : "", s0 ? link(s0) : "", opts.later ? (t.until && t.until.startsWith(data.today) ? "" : "on ") + esc(when(t)) : "",
                     t.rolled ? `<span style="color:var(--warn)">slipped ${t.rolled}×</span>` : "",
                     opts.closed ? (t.status === "dropped" ? "dropped" : t.closed_by === "claude" ? "done: " + esc(t.evidence) : "done") : ""]
          .filter(Boolean).join(" · ") + (t.asked ? ` · <span class="meta">${esc(ai)} is reading your note…</span>` : "");
        const quick = opts.closed ? `<button data-row="restore" title="Put it back">Undo</button>`
          : `<button data-row="done" title="Done" aria-label="Done">✓</button><button data-row="snooze1" title="Tomorrow" aria-label="Snooze to tomorrow">→</button>`;
        return `<div class="row${t.id === sel ? " sel" : ""}${opts.closed ? " closed" : ""}" data-id="${esc(t.id)}">${pri(t)}
          <div class="body"><div class="t">${esc(t.title)}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>
          <div class="quick">${quick}</div></div>`;
      }).join("");
    }
    function render() {
      if (!data) return;
      const g = groups();
      renderNow(g);
      const fill = (k, list, opts) => {
        const box = el.querySelector(`[data-list="${k}"]`); if (!box) return;
        box.innerHTML = rows(list, opts); el.querySelector(`[data-n="${k}"]`).textContent = list.length || "";
      };
      fill("quick", g.quick, { empty: "No quick items today." }); fill("deep", g.deep, { empty: "No deep work for today." });
      fill("waiting", g.waiting); fill("later", g.later, { later: true }); fill("handled", g.handled, { closed: true, empty: "Nothing yet." });
      part("side").innerHTML = src.side(data);
      part("foot").textContent = src.foot ? src.foot(data) : "";
      if (src.rendered) src.rendered(data);
    }

    async function act(t, action, value, label) {
      const before = { ...t };
      try { data = await src.act(t, action, value); } catch (e) { return toast(e.message || "That didn't work."); }
      if (label) { lastUndo = before; toast(`${label}: ${t.title}`, true); }
      if (sel === t.id && action !== "star") sel = null;
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
      const t = current(); if (!t || !src.actions.includes(a)) return;
      if (a === "open") { const s = (t.sources || []).find((x) => x.link); if (s) window.open(s.link, "_blank", "noopener"); return; }
      if (a === "snooze") { part("snooze")?.classList.toggle("hidden"); return; }
      act(t, a, null, a === "star" ? null : LABEL[a]);
    }
    part("now").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.a) nowAction(b.dataset.a);
      if (b.dataset.snooze && current()) act(current(), "snooze", snoozeDay(b.dataset.snooze), "Snoozed");
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
      const r = e.target.closest(".row"); if (!r || e.target.closest("a")) return;
      const t = data.tasks.find((x) => x.id === r.dataset.id); if (!t) return;
      const b = e.target.closest("button");
      if (b && b.dataset.row === "done") return act(t, "done", null, "Done");
      if (b && b.dataset.row === "snooze1") return act(t, "snooze", snoozeDay(1), "Snoozed");
      if (b && b.dataset.row === "restore") return act(t, "restore", { ...t, status: "open", done_at: null }, "Back on the list");
      sel = t.id; render(); scrollTo({ top: 0, behavior: "smooth" });
    });
    part("side").addEventListener("click", async (e) => {
      if (!src.sideClick) return;
      const out = await src.sideClick(e, data);
      if (out) { data = out; render(); }
    });
    part("add").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = part("add").querySelector("input"), text = input.value.trim(); if (!text) return;
      try { data = await src.add(text, part("add").querySelector("select").value); } catch (err) { return toast(err.message || "That didn't work."); }
      input.value = ""; input.blur(); render();  // the keys work on the list again
    });
    part("add").addEventListener("keydown", (e) => { if (e.key === "Escape") e.target.blur(); });
    document.addEventListener("keydown", (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || !data || !el.offsetParent) return;
      const g = groups(), list = [...g.quick, ...g.deep], i = list.findIndex((t) => t.id === sel), k = e.key;
      const action = Object.keys(KEYS).find((a) => KEYS[a] === k && src.actions.includes(a));
      if (k === "j" || k === "k") { const n = list[Math.max(0, Math.min(list.length - 1, i + (k === "j" ? 1 : -1)))]; if (n) { sel = n.id; render(); } }
      else if (action) nowAction(action);
      else if (k === "u") undo();
      else if (k === "n") part("add").querySelector("input").focus();
      else if (k === "c" && src.reply && part("now").querySelector(".reply input")) part("now").querySelector(".reply input").focus();
      else if (k === "r" && src.refresh) src.refresh();
      else if (k === "Escape") part("snooze")?.classList.add("hidden");
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
      try { data = await src.load(); } catch (e) { part("now").innerHTML = `<h2>Now</h2><p class="err">${esc(e.message || e)}</p>`; return; }
      render();
    }
    return { reload, render, get data() { return data; } };
  };
})();
