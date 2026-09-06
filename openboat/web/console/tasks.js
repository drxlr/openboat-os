/* OpenBoat console — the tasks view. Loaded by console.html after the shared furniture;
   it relies on the globals defined there (state, el, api, esc, ago, href, nav, mount…).
   No boat facts live here: everything on screen arrives from the API at run time.

   ── what this view is ───────────────────────────────────────────────────────────────
   One list, because a boat has one. The engine is owed a flush because it ran in salt
   water; the locker by the heads is owed a striker plate because somebody stood in front
   of it with a phone. Kept apart they are two lists nobody reads.

   Three addresses:

     #tasks                          everything, newest and most open first
     #tasks?f=open|snags|service     the same list, narrowed, and linkable so narrowed
     #tasks/snag:<when>              one fault, whole — note, photographs, follow-ups
     #tasks/service:<item>           one service item, with every counter behind it

   A row is a link rather than a click handler, so the middle button, the back button and
   a copied address all behave the way they do everywhere else. The detail pages replace
   the list rather than squeezing in underneath it: a snag's note runs to a couple of
   hundred words, carries photographs, and usually has something to say in the boat's own
   papers — none of which fits in half a pane.

   Nothing here writes, and nothing here pretends to. A snag is closed by a person editing
   a file and a service is recorded by a command typed at a keyboard; both pages say so,
   and say where. */

/* Renders are asynchronous and the hash can change while one is in flight. Every entry
   point takes a ticket and drops its output if a newer one has been issued since. */
let tasksRun = 0;

/* Whether anything in this tab has moved since it opened. A deep link opened cold has no
   console page behind it, so Escape has nothing to go back to and goes to the list. */
let tasksHops = 0;
window.addEventListener("hashchange", () => { tasksHops++; });

/* ── the router for this view ─────────────────────────────────────────────────────── */
async function viewTasks() {
  const sub = state.sub || "";
  if (sub.startsWith("snag:"))    return tasksDetail("snag", sub.slice(5));
  if (sub.startsWith("service:")) return tasksDetail("service", sub.slice(8));
  return tasksList();
}

/* ── small helpers ────────────────────────────────────────────────────────────────── */

/* A title is a whole sentence typed on a phone; the column is not that wide. Show the
   first sentence, and if that sentence is itself long, cut it at a word — never mid-word,
   which is how "replaced the striker pl…" got read as a different fault twice. */
function tasksFirst(text, max) {
  const s = String(text ?? "").trim().replace(/\s+/g, " ");
  if (!s) return "—";
  const stop = s.search(/[.!?](\s|$)/);
  let out = stop > 0 ? s.slice(0, stop + 1) : s;
  if (out.length > max) {
    const cut = out.lastIndexOf(" ", max);
    out = out.slice(0, cut > max * 0.5 ? cut : max).trim() + "…";
  }
  return out;
}

/* The recorder cuts a snag's title to a fixed width as it files it, which is how a fault
   ends up listed as "Locker will not latch and the striker plate is be". The note's own
   first line is that same sentence, whole,
   so when the stored title is a prefix of it the longer one is the honest one to show.
   This is not a guess: it is the same characters, un-cut. */
function tasksTitle(s) {
  const stored = String((s && s.title) || "").trim();
  const first = String((s && s.body) || "").replace(/\r\n?/g, "\n")
                  .split("\n").map(l => l.trim()).find(Boolean) || "";
  return first.startsWith(stored) && first.length > stored.length ? first : (stored || "—");
}

/* The three status colours, and not a fourth. `verdict` comes from the API — due, soon,
   ok, unknown — and is mapped here rather than restated: an item with no interval set has
   no verdict, and this page says that instead of inventing one. */
function tasksStatus(r) {
  if (r.kind === "snag")
    return r.open ? { cls: "warn", label: r.status || "open" }
                  : { cls: "ok", label: r.status || "fixed" };
  return { due:     { cls: "bad",  label: "due" },
           soon:    { cls: "warn", label: "soon" },
           ok:      { cls: "ok",   label: "in date" },
           unknown: { cls: "info", label: "no verdict" } }[r.verdict]
         || { cls: "info", label: r.verdict || "—" };
}

function tasksPill(r) {
  const s = tasksStatus(r);
  const p = el("span", "t-pill " + s.cls);
  p.append(el("span", "dot"));
  p.append(document.createTextNode(s.label));
  return p;
}

/* A meta strip: the pieces that are known, separated, and the pieces that are not simply
   left out rather than shown as an empty field. The separator lives inside the item it
   precedes rather than beside it, so a strip wrapping onto a phone never leaves a lone
   dot stranded at the end of a line. */
function tasksMeta(parts) {
  const row = el("div", "t-meta");
  parts.filter(Boolean).forEach((p, i) => {
    const item = el("span", "i");
    if (i) item.append(el("span", "sep", "· "));
    item.append(document.createTextNode(String(p)));
    row.append(item);
  });
  return row;
}

function tasksSection(title, side) {
  const s = el("div", "t-sec");
  const h = el("h2", "", title);
  if (side) h.append(el("span", "side", side));
  s.append(h);
  return s;
}

function tasksCrumbs(here) {
  const c = el("div", "t-crumbs");
  const a = el("a", "", "Tasks");
  a.href = href("tasks");
  c.append(a);
  c.append(el("span", "sep", "›"));
  c.append(el("span", "here", here));
  return c;
}

function tasksPage(crumb) {
  const v = el("div", "view plain");
  const scroll = el("div", "scroll t-page");
  const inner = el("div", "t-inner");
  inner.append(tasksCrumbs(crumb));
  scroll.append(inner);
  v.append(scroll);
  return { view: v, body: inner, scroll };
}

function tasksLoading(what) {
  const { view, body } = tasksPage(what);
  const n = el("div", "t-note");
  n.append(statusCell("info", "Reading…"));
  body.append(n);
  mount(view);
}

function tasksMiss(crumb, line) {
  const { view, body } = tasksPage(crumb);
  const n = el("div", "t-note");
  n.append(statusCell("bad", line));
  body.append(n);
  const back = el("a", "", "◂ back to the list");
  back.href = href("tasks");
  back.style.cssText = "display:inline-block;margin-top:14px;font-size:12px";
  body.append(back);
  mount(view);
}

/* ── the body of a note, as prose ─────────────────────────────────────────────────── */

/* A snag note is typed into a phone: blank lines separate thoughts, single newlines are
   where the thumb hit return. So blank lines make paragraphs and everything else keeps
   its break. Everything is escaped first; the only markup added afterwards is built from
   the escaped text, because a note quoting a fitting's part number is a note and not an
   instruction. */
const TASKS_MENTION =
  /(https?:\/\/[^\s<>"'&]+)|((?:[A-Za-z0-9._-]+\/)*[A-Za-z0-9._-]+\.(?:pdf|md))/g;

/* `docs` is the /api/docs answer. A mention of `manuals/pump-7j.pdf` in a note becomes a
   link to that document on the shelf when the boat actually has it, and stays plain text
   when it does not — a link to a document nobody named would be a promise this console
   cannot keep. */
function tasksLinkIndex(docs) {
  const index = new Map();
  ((docs && docs.documents) || []).forEach(d => {
    if (d.name) index.set(String(d.name).toLowerCase(), d.name);
    if (d.original) index.set(String(d.original).toLowerCase(), d.name);
  });
  return index;
}

function tasksProse(text, index) {
  const wrap = el("div", "t-prose");
  const body = String(text ?? "").replace(/\r\n?/g, "\n").trim();
  if (!body) {
    wrap.append(el("p", "", "No note was written — see the photograph."));
    return wrap;
  }
  body.split(/\n{2,}/).forEach(chunk => {
    const p = el("p");
    p.innerHTML = esc(chunk).replace(TASKS_MENTION, (m, url, file) => {
      if (url) return `<a href="${url}" target="_blank" rel="noopener">${url}</a>`;
      const known = index.get(file.toLowerCase()) ||
                    index.get(file.split("/").pop().toLowerCase());
      return known ? `<a href="${esc(href("docs", known))}">${file}</a>` : file;
    });
    wrap.append(p);
  });
  return wrap;
}

/* ── photographs ──────────────────────────────────────────────────────────────────── */

/* The photographs live with the snag service on its own port, not with the dashboard, for
   the reason set out in docs/SNAGS.md: the write surface is a separate process. If it is
   not running the tile says so in its own frame. A missing picture that looks like a
   missing picture is honest; one that looks like no picture was ever taken is not. */
function tasksPhotoUrl(snags, name) {
  const base = `${location.protocol}//${location.hostname}:${snags.photo_port}`;
  return `${base}/photo?boat=${encodeURIComponent(snags.boat || "")}` +
         `&name=${encodeURIComponent(name)}`;
}

function tasksGallery(snags, names) {
  const grid = el("div", "t-shots");
  const shots = names.map(n => ({ name: n, url: tasksPhotoUrl(snags, n) }));
  shots.forEach((shot, i) => {
    const a = el("a", "t-shot");
    a.href = shot.url;
    a.target = "_blank";
    a.rel = "noopener";
    a.title = shot.name;
    const img = el("img");
    img.src = shot.url;
    img.alt = shot.name;
    img.loading = "lazy";
    img.onerror = () => {
      img.remove();
      a.append(el("span", "t-miss",
        `${shot.name}\nneeds the snag service on :${snags.photo_port}`));
    };
    a.append(img);
    a.append(el("span", "t-n", `${i + 1}/${shots.length}`));
    a.onclick = ev => {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;
      ev.preventDefault();
      tasksLightbox(shots, i, snags.photo_port);
    };
    grid.append(a);
  });
  return grid;
}

/* ── the lightbox ─────────────────────────────────────────────────────────────────── */
let tasksLb = null;

function tasksLightbox(shots, start, port) {
  tasksCloseLb();
  let i = start;
  const box = el("div", "t-lb");
  const bar = el("div", "bar");
  const name = el("b");
  const of = el("span", "of");
  const mk = (label, path, fn) => {
    const b = el("button");
    b.title = label;
    b.setAttribute("aria-label", label);
    b.innerHTML = svg(path);
    b.onclick = fn;
    return b;
  };
  const prev = mk("Previous", "M15 5l-7 7 7 7", () => step(-1));
  const next = mk("Next", "M9 5l7 7-7 7", () => step(1));
  const shut = mk("Close", "M6 6l12 12M18 6L6 18", tasksCloseLb);
  bar.append(name, of, prev, next, shut);

  const stagebox = el("div", "stagebox");
  const foot = el("div", "foot", "Esc closes · ← → steps");
  box.append(bar, stagebox, foot);

  function step(d) {
    i = (i + d + shots.length) % shots.length;
    paint();
  }

  function paint() {
    const s = shots[i];
    name.textContent = s.name;
    of.textContent = `${i + 1} / ${shots.length}`;
    prev.disabled = next.disabled = shots.length < 2;
    stagebox.textContent = "";
    const img = el("img");
    img.src = s.url;
    img.alt = s.name;
    img.onerror = () => {
      stagebox.textContent = "";
      stagebox.append(el("div", "miss",
        `${s.name} is filed with this entry, and the service that holds it is not ` +
        `answering on :${port}. The photograph exists; this page cannot reach it.`));
    };
    stagebox.append(img);
  }

  box.onclick = ev => { if (ev.target === box || ev.target === stagebox) tasksCloseLb(); };
  /* Captured, and stopped here. Escape closes the lightbox and nothing else: without the
     stop it would close the picture and then be read a second time by the page's own
     Escape, which would send you back to the list you had not asked to leave. */
  const keys = ev => {
    if (!["Escape", "ArrowLeft", "ArrowRight"].includes(ev.key)) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (ev.key === "Escape") tasksCloseLb();
    else step(ev.key === "ArrowLeft" ? -1 : 1);
  };
  document.addEventListener("keydown", keys, true);
  tasksLb = { box, keys };
  paint();
  document.body.append(box);
  shut.focus();
}

function tasksCloseLb() {
  if (!tasksLb) return;
  document.removeEventListener("keydown", tasksLb.keys, true);
  tasksLb.box.remove();
  tasksLb = null;
}

/* ── asking the boat's papers about a fault ───────────────────────────────────────── */

/* The question is built from what the person wrote, not from a menu: the words of the
   title plus the first words of where it is, minus the words every sentence contains.
   Eight terms is enough to find the right section of a manual and short enough that one
   unusual word still carries weight. */
const TASKS_STOP = new Set(
  ("a an and are as at be been being but by can could did do does for from had has have " +
   "he her his i if in into is it its me my no not of on or our she should so some such " +
   "than that the their them then there these they this to was we were what when which " +
   "who why will with would you your").split(" "));

function tasksQuery(title, where) {
  const words = s => String(s ?? "").toLowerCase().split(/[^a-z0-9äöüß-]+/i)
                       .filter(w => w.length > 2 && !TASKS_STOP.has(w));
  const terms = [];
  const push = w => { if (terms.length < 8 && !terms.includes(w)) terms.push(w); };
  words(title).forEach(push);
  words(where).slice(0, 3).forEach(push);
  return terms.join(" ");
}

function tasksSnippet(text) {
  const s = String(text ?? "").replace(/\s+/g, " ").trim();
  if (s.length <= 260) return s;
  const cut = s.lastIndexOf(" ", 260);
  return s.slice(0, cut > 130 ? cut : 260).trim() + "…";
}

/* `skip` is the entry's own timestamp. The snag list is itself one of the boat's papers,
   so asking the library about a fault reliably returns the fault — the entry quoting
   itself back, at the top, ahead of the manual that might actually answer it. A passage
   carrying this entry's own stamp is this entry, so it is dropped. */
async function tasksPapers(section, title, where, docs, skip) {
  const q = tasksQuery(title, where);
  const holder = el("div");
  section.append(holder);
  const waiting = el("div", "t-dim", "Asking the papers…");
  holder.append(waiting);

  if (!q) {
    holder.textContent = "";
    holder.append(el("div", "t-dim",
      "There is nothing in this entry to ask the papers with."));
    return;
  }

  const r = await api("/api/ask?q=" + encodeURIComponent(q) + "&limit=9");
  holder.textContent = "";

  if (r && r.error) {
    const e = el("div", "t-note");
    e.append(statusCell("bad", `The library did not answer: ${r.error}`));
    holder.append(e);
    return;
  }

  const hits = ((r && r.passages) || [])
    .filter(h => !(skip && `${h.heading || ""} ${h.text || ""}`.includes(skip)));
  if (!hits.length) {
    holder.append(el("div", "t-dim",
      `Nothing in the papers mentions this. Asked ${nf(r && r.documents)} document(s) ` +
      `for “${q}”, and that is an answer rather than a failure — this boat's library ` +
      `does not cover it.`));
    return;
  }

  const titleOf = new Map(
    ((docs && docs.documents) || []).map(d => [d.name, d.title || d.name]));
  const list = el("div", "t-hits");
  hits.slice(0, 3).forEach(h => {
    const a = el("a", "t-hit");
    a.href = href("docs", `${h.doc}/L${h.line}`);
    const d = el("div", "d");
    d.append(el("b", "", titleOf.get(h.doc) || h.doc));
    d.append(el("span", "ln", `line ${nf(h.line)}`));
    a.append(d);
    a.append(el("div", "hd", h.heading || "(no heading)"));
    a.append(el("p", "sn", tasksSnippet(h.text)));
    list.append(a);
  });
  holder.append(list);
  const asked = el("div", "t-dim",
    `Asked for “${q}”. Every answer carries the file and the line it came out of, so it ` +
    `can be walked back to the paper and checked.`);
  asked.style.marginTop = "14px";
  holder.append(asked);
}

/* ── the list ─────────────────────────────────────────────────────────────────────── */

const TASKS_CHIPS = [["all", "All"], ["open", "Open"], ["snags", "Snags"],
                     ["service", "Service"]];

function tasksRows(snags, maint) {
  const out = [];
  ((snags && snags.snags) || []).forEach(s => out.push({
    kind: "snag", id: "snag:" + s.when, title: tasksTitle(s), where: s.where, when: s.when,
    open: s.open, status: s.status, by: s.by, raw: s,
  }));
  ((maint && maint.items) || []).forEach(m => out.push({
    kind: "service", id: "service:" + m.item, title: m.description || m.item,
    where: m.item, when: m.last, open: m.verdict !== "ok", verdict: m.verdict,
    why: m.why, raw: m,
  }));

  /* Open before closed; inside the open group a service the engine says is due outranks a
     fault somebody noticed, because the engine counted and the person guessed; then the
     newest first, with anything that has never been recorded at the bottom of its group
     rather than pretending to a date. */
  const rank = r => (r.open ? 0 : 1) * 10 +
                    (r.kind === "service" && r.verdict === "due" ? 0 : 1);
  return out.sort((a, b) => rank(a) - rank(b) ||
                            (b.when || "").localeCompare(a.when || ""));
}

function tasksCounts(all) {
  return { all: all.length,
           open: all.filter(r => r.open).length,
           snags: all.filter(r => r.kind === "snag").length,
           service: all.filter(r => r.kind === "service").length };
}

function tasksKeep(r, f) {
  if (f === "open")    return r.open;
  if (f === "snags")   return r.kind === "snag";
  if (f === "service") return r.kind === "service";
  return true;
}

async function tasksList() {
  const run = ++tasksRun;
  tasksLoading("everything the boat is owed");

  const [maint, snags] = await Promise.all([api("/api/maintenance"), api("/api/snags")]);
  if (run !== tasksRun) return;

  /* A count off a request that failed is not a count. "0 item(s), 0 still open in all"
     and "the boat owes nothing" are the same sentence on screen and opposite facts, and
     this is the page somebody checks before leaving the boat for the winter. */
  const broken = [snags, maint].filter(x => x && x.error);
  if (broken.length === 2)
    return tasksMiss("not answered",
      `The boat could not be read: ${broken[0].error}. Nothing can be said here about ` +
      `what it is owed — this is a statement about the connection, not about the boat.`);

  const all = tasksRows(snags, maint);
  const counts = tasksCounts(all);
  const filter = TASKS_CHIPS.some(([id]) => id === state.params.f) ? state.params.f : "all";

  const v = el("div", "view plain");
  const shell = el("div", "t-shell");
  const seg = el("div", "t-seg");
  TASKS_CHIPS.forEach(([id, label]) => {
    const a = el("a", "", label);
    a.href = href("tasks", null, id === "all" ? null : { f: id });
    a.setAttribute("aria-current", filter === id ? "true" : "false");
    a.append(el("span", "n", String(counts[id])));
    seg.append(a);
  });

  const wrap = el("div", "t-listwrap");
  shell.append(toolbar("Filter what this boat owes…",
                       () => { state.sel = null; draw(); }, [seg]));
  shell.append(wrap);
  v.append(shell);
  mount(v);

  /* What the filter box searches. Stringifying the whole record searched the JSON's own
     key names too, so "body", "photos" and "true" each matched every row on the page. */
  all.forEach(r => {
    r.hay = [r.title, r.where, r.by, r.status, r.why, r.verdict, r.kind,
             r.raw && r.raw.body, r.raw && r.raw.description, r.raw && r.raw.item,
             ...(((r.raw && r.raw.updates) || []).map(u => u && u.body))]
            .filter(Boolean).join(" \n ").toLowerCase();
  });

  let shown = [];

  function visible() {
    const q = state.q.trim().toLowerCase();
    return all.filter(r => tasksKeep(r, filter) && (!q || r.hay.includes(q)));
  }

  function draw() {
    shown = visible();
    wrap.textContent = "";

    if (broken.length) {
      const b = el("div", "t-note");
      b.append(statusCell("bad",
        `${snags && snags.error ? "The snag list" : "The engine log"} could not be read: ` +
        `${broken[0].error}. This list is short by an unknown number of items.`));
      wrap.append(b);
    }

    const head = el("div", "t-head");
    head.append(el("span", "", "SOURCE"));
    head.append(el("span", "", "WHAT"));
    head.append(el("span", "", "STATUS"));
    wrap.append(head);

    shown.forEach((r, i) => {
      const a = el("a", "t-row");
      a.href = href("tasks", r.id);
      a.setAttribute("aria-current", state.sel === i ? "true" : "false");
      a.append(el("span", "t-kind", r.kind));

      const mid = el("span");
      mid.append(el("span", "t-t", tasksFirst(r.title, 110)));
      const sub = r.kind === "snag"
        ? [r.where, r.by, ago(r.when)].filter(Boolean).join("  ·  ")
        : (r.why || "");
      if (sub) mid.append(el("span", "t-sub", sub));
      a.append(mid);

      const col3 = el("span", "t-col3");
      col3.append(tasksPill(r));
      col3.append(el("span", "t-when",
        r.kind === "service" && !r.when ? "never recorded" : ago(r.when)));
      a.append(col3);

      a.onclick = () => { state.sel = i; };
      wrap.append(a);
    });

    if (!shown.length && !broken.length) {
      const e = el("div", "t-note");
      e.innerHTML = state.q.trim()
        ? `Nothing in ${counts.all} item(s) matches <b>${esc(state.q)}</b>.`
        : `Nothing is filed under this filter.`;
      wrap.append(e);
    }

    if (broken.length) return;                   // the counts below would be half-truths
    const foot = el("div", "t-note");
    const narrowed = filter !== "all" || state.q.trim();
    foot.innerHTML =
      (narrowed ? `Showing <b>${shown.length}</b> of <b>${counts.all}</b> item(s), `
                : `<b>${counts.all}</b> item(s), `) +
      `<b>${counts.open}</b> still open in all. Snags come from whoever stood in front ` +
      `of the fault with a phone; service items come from the engine's own running ` +
      `hours. Pick one to read it whole.`;
    wrap.append(foot);

    if (maint && maint.engine_hours_source) {
      const src = el("div", "t-note");
      src.append(statusCell("info", maint.engine_hours_source));
      wrap.append(src);
    }
  }

  /* j and k walk the list and Enter opens the selected row, which is only reachable once
     the filter box has given the keyboard up — otherwise j is a letter. */
  tasksKeys.move = d => {
    if (!shown.length) return;
    state.sel = state.sel === null ? 0
              : Math.max(0, Math.min(shown.length - 1, state.sel + d));
    draw();
    const row = wrap.children[state.sel + 1];
    if (row) row.scrollIntoView({ block: "nearest" });
  };
  tasksKeys.open = () => {
    if (state.sel !== null && shown[state.sel]) nav("tasks", shown[state.sel].id);
  };

  draw();
}

/* ── a detail page ────────────────────────────────────────────────────────────────── */
async function tasksDetail(kind, id) {
  const run = ++tasksRun;
  tasksLoading(kind === "snag" ? "one fault" : "one service item");

  const [maint, snags, docs] = await Promise.all(
    [api("/api/maintenance"), api("/api/snags"), api("/api/docs")]);
  if (run !== tasksRun) return;

  /* "No snag is filed at that stamp" is a claim about this boat's SNAGS.md. A request
     that failed supports no such claim, and a link the owner just followed is the worst
     place to guess that a fault was never recorded. */
  if (kind === "snag") {
    if (snags && snags.error)
      return tasksMiss("not answered",
        `The snag list could not be read: ${snags.error}. This entry may well be filed; ` +
        `nobody could ask.`);
    const s = ((snags && snags.snags) || []).find(x => x.when === id);
    if (!s) return tasksMiss("no such entry",
      `No snag is filed at ${id}. It may have been renamed, or this link may be older ` +
      `than the file.`);
    return tasksSnagPage(s, snags, docs);
  }

  if (maint && maint.error)
    return tasksMiss("not answered",
      `The maintenance table could not be read: ${maint.error}. This item may well be ` +
      `in the profile; nobody could ask.`);
  const m = ((maint && maint.items) || []).find(x => x.item === id);
  if (!m) return tasksMiss("no such item",
    `The profile names no maintenance item called ${id}. Items come from the ` +
    `[maintenance] table in this boat's profile.`);
  return tasksServicePage(m, maint, docs);
}

function tasksSnagPage(s, snags, docs) {
  const index = tasksLinkIndex(docs);
  const title = tasksTitle(s);
  const { view, body } = tasksPage(tasksFirst(title, 90));
  const r = { kind: "snag", open: s.open, status: s.status };

  const hrow = el("div", "t-hrow");
  hrow.append(el("h1", "t-h1", title));
  hrow.append(tasksPill(r));
  body.append(hrow);
  body.append(tasksMeta([
    s.when ? `filed ${s.when}` : null,
    s.when ? ago(s.when) : null,
    s.by ? `by ${s.by}` : null,
    s.where || null,
  ]));

  const note = tasksSection("What was written", "NOTE");
  note.append(tasksProse(s.body, index));
  body.append(note);

  const photos = s.photos || [];
  const shots = tasksSection("Photographs",
                             photos.length ? `${photos.length} FILED` : "NONE");
  if (photos.length) shots.append(tasksGallery(snags, photos));
  else shots.append(el("div", "t-dim",
    "No photograph was filed with this entry — it was written, not photographed."));
  body.append(shots);

  const ups = (s.updates || []).slice()
                .sort((a, b) => String(a.when).localeCompare(String(b.when)));
  const tl = tasksSection("Follow-ups", ups.length ? `${ups.length}` : "NONE");
  if (ups.length) {
    const ul = el("ul", "t-tl");
    ups.forEach(u => {
      const li = el("li");
      const w = el("div", "t-w");
      w.append(document.createTextNode(u.when || "—"));
      if (u.by) { w.append(el("span", "sep", "·")); w.append(document.createTextNode(u.by)); }
      li.append(w);
      li.append(tasksProse(u.body, index));
      if ((u.photos || []).length) li.append(tasksGallery(snags, u.photos));
      ul.append(li);
    });
    tl.append(ul);
  } else {
    tl.append(el("div", "t-dim",
      "Nothing has been added since this was filed. It stands as it was written, " +
      "unverified, by whoever noticed it."));
  }
  body.append(tl);

  const papers = tasksSection("In the papers", "ASKED");
  body.append(papers);

  /* docs/SNAGS.md is the authority for this paragraph: `record()` in openboat/snag.py has
     no code path that edits an existing entry, so closing one is a deliberate act by a
     person at a desk rather than a tap on a phone in a wet pocket. */
  const foot = el("div", "t-foot");
  foot.innerHTML =
    `<b>How to close it.</b> Nothing on this page changes anything: the console reads, ` +
    `and the snag service only ever appends. Closing a snag is one line changed by hand ` +
    `in this boat's <code>SNAGS.md</code>, beside the <code>boat.toml</code> the profile ` +
    `points at — <code>**Status:** open</code> becomes ` +
    `<code>**Status:** fixed — replaced the striker plate, 2026-09-12</code>. Every entry ` +
    `also carries a standing mark that it is unverified: recorded from a phone at the ` +
    `moment of noticing, confirmed by nobody since.`;
  body.append(foot);

  mount(view);
  tasksPapers(papers, title, s.where, docs, s.when);
}

function tasksKvRow(tb, key, value, missing) {
  const tr = el("tr");
  tr.append(el("td", "", key));
  const td = el("td", value === null || value === undefined || value === "" ? "none" : "");
  td.textContent = (value === null || value === undefined || value === "")
                   ? (missing || "—") : String(value);
  tr.append(td);
  tb.append(tr);
}

function tasksServicePage(m, maint, docs) {
  const index = tasksLinkIndex(docs);
  const { view, body } = tasksPage(tasksFirst(m.description || m.item, 90));
  const r = { kind: "service", verdict: m.verdict };

  const hrow = el("div", "t-hrow");
  hrow.append(el("h1", "t-h1", m.description || m.item));
  hrow.append(tasksPill(r));
  body.append(hrow);
  body.append(tasksMeta([
    m.item,
    m.last ? `last done ${m.last}` : "never recorded",
    m.last ? ago(m.last) : null,
    m.per_outing ? "counted per salt-water outing" : null,
  ]));

  const why = tasksSection("Why it is owed", "REASON");
  why.append(tasksProse(m.why, index));
  body.append(why);

  const counters = tasksSection("The counters behind it", "SOURCED");
  const t = el("table", "t-kv");
  const tb = el("tbody");
  tasksKvRow(tb, "Item", m.item);
  tasksKvRow(tb, "Last done", m.last, "never recorded");
  tasksKvRow(tb, "Hours since",
             m.hours_since === null || m.hours_since === undefined
               ? null : `${Number(m.hours_since).toFixed(1)} h`);
  tasksKvRow(tb, "Days since", m.days_since === null || m.days_since === undefined
                               ? null : `${nf(m.days_since)} d`);
  tasksKvRow(tb, "Outings since", m.outings_since === null || m.outings_since === undefined
                                  ? null : nf(m.outings_since));
  tasksKvRow(tb, "Interval, hours", m.interval_hours ? `${nf(m.interval_hours)} h` : null,
             "no interval set");
  tasksKvRow(tb, "Interval, months", m.interval_months ? `${nf(m.interval_months)} months`
                                                       : null, "no interval set");
  tasksKvRow(tb, "Every outing", m.per_outing === null || m.per_outing === undefined
                                 ? null : (m.per_outing ? "yes" : "no"));
  tasksKvRow(tb, "Verdict", m.verdict);
  t.append(tb);
  counters.append(t);
  body.append(counters);

  /* The running-hours line is quoted exactly as maintenance.py wrote it. It is the one
     sentence that says how much of this engine's life the log actually covers, and
     paraphrasing it would be paraphrasing the boat's own uncertainty. */
  if (maint && maint.engine_hours_source) {
    const src = el("div", "t-dim");
    src.style.marginTop = "14px";
    src.textContent = maint.engine_hours_source;
    counters.append(src);
  }

  const papers = tasksSection("In the papers", "ASKED");
  body.append(papers);

  /* docs/JOBS.md is the authority here: recording a service resets the clock on an
     interval and everything downstream is computed from it, which is not the correct
     weight for a tap on a wet tablet. */
  const rec = tasksSection("Record it", "BY HAND");
  rec.append(el("div", "t-dim",
    "This page has no button, on purpose. Recording a service resets the clock on an " +
    "interval, and the next due date, the cooling trend and the season report are all " +
    "computed from it. It is typed at a keyboard by somebody who knows the work " +
    "actually happened:"));
  const cmd = el("pre", "t-cmd");
  cmd.textContent = `python3 -m openboat.maintenance --did ${m.item}` +
                    `\npython3 -m openboat.maintenance --did ${m.item} ` +
                    `--note "what was used, who did it"`;
  cmd.style.marginTop = "12px";
  rec.append(cmd);
  const tail = el("div", "t-dim",
    "Add --on YYYY-MM-DD if it happened on a day other than today, and " +
    "--history to see what has been done.");
  tail.style.marginTop = "12px";
  rec.append(tail);
  body.append(rec);

  mount(view);
  tasksPapers(papers, m.description || m.item, m.item, docs);
}

/* ── the keyboard ─────────────────────────────────────────────────────────────────── */

/* Registered once, at load, and it minds its own view. Escape on a detail page is the
   back button when there is somewhere to go back to, and the list when this tab was
   opened cold on a deep link. */
function tasksKeys(ev) {
  if (state.view !== "tasks" || tasksLb) return;
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((ev.target.tagName || ""));
  const detail = !!state.sub;

  if (ev.key === "Escape") {
    if (typing) { ev.target.blur(); return; }
    if (!detail) return;
    ev.preventDefault();
    if (tasksHops > 0) history.back(); else nav("tasks");
    return;
  }
  if (typing || detail) return;
  if (ev.key === "j" || ev.key === "ArrowDown") { ev.preventDefault(); tasksKeys.move(1); }
  else if (ev.key === "k" || ev.key === "ArrowUp") { ev.preventDefault(); tasksKeys.move(-1); }
  else if (ev.key === "Enter") { ev.preventDefault(); tasksKeys.open(); }
}
tasksKeys.move = () => {};
tasksKeys.open = () => {};
document.addEventListener("keydown", tasksKeys);
