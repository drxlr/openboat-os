/* OpenBoat console — the tasks view. Loaded by console.html after the shared furniture;
   it relies on the globals defined there (state, el, api, esc, ago, href, nav, mount,
   toolbar, statusCell, pane, cardBody, kv, crumbs, pageHead, note…).
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

   Everything on screen is a stock Bootstrap component — a table, cards, badges, a modal —
   because the person reading it is in a bad mood in February and has used those a thousand
   times before. Selection and hover are the shell's tint: this file paints no highlight of
   its own, and the one that reads as a slab of colour is the one nobody can read.

   Nothing here writes to the dashboard behind this page, and nothing here rewrites anything
   at all. What a person can do from a fault's page — move its status, hand it to somebody,
   give somebody outside a link to it, take that link back — is in every case one follow-up
   appended to the boat's own file through the snag service, by way of `snagPost()` in the
   shell. A service is still recorded by a command typed at a keyboard, and that page says
   so and says where. */

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

/* A title is a whole sentence typed on a phone; the row is not that tall. Show the first
   sentence, and if that sentence is itself long, cut it at a word — never mid-word,
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
   first line is that same sentence, whole, so when the stored title is a prefix of it the
   longer one is the honest one to show. This is not a guess: it is the same characters,
   un-cut. */
function tasksTitle(s) {
  const stored = String((s && s.title) || "").trim();
  const first = String((s && s.body) || "").replace(/\r\n?/g, "\n")
                  .split("\n").map(l => l.trim()).find(Boolean) || "";
  return first.startsWith(stored) && first.length > stored.length ? first : (stored || "—");
}

/* A status line is written by people as well as by the service, so it arrives as anything
   from `review` to `fixed — replaced the striker plate, 2026-09-12`. The first word is the
   status; the rest is what somebody wanted to say about it, and it belongs on the page
   rather than squeezed into a badge. */
function tasksWord(status) {
  return String(status || "").trim().toLowerCase().split(/[^a-z]+/).filter(Boolean)[0] || "";
}

const TASKS_CLOSED = ["fixed", "done", "closed"];

/* The status badges, and not a fourth colour. `open` is the phone's default, `review` says
   somebody has an idea worth looking at before a tool comes out, and the closed words end
   it. `open` is left undecided when the caller does not know it — a follow-up's own status
   is the word it carries, not its parent's. */
function tasksSnagBadge(status, open) {
  const w = tasksWord(status);
  if (w === "review") return statusCell("info", "review");
  if (open === false || TASKS_CLOSED.includes(w)) return statusCell("ok", w || "fixed");
  return statusCell("warn", w || "open");
}

/* `verdict` comes from the API — due, soon, ok, unknown — and is mapped here rather than
   restated: an item with no interval set has no verdict, and this page says that instead
   of inventing one. */
function tasksBadge(r) {
  if (r.kind === "snag") return tasksSnagBadge(r.status, r.open);
  const s = { due:     { cls: "bad",  label: "due" },
              soon:    { cls: "warn", label: "soon" },
              ok:      { cls: "ok",   label: "in date" },
              unknown: { cls: "info", label: "no verdict" } }[r.verdict]
          || { cls: "info", label: r.verdict || "—" };
  return statusCell(s.cls, s.label);
}

/* A dimmed value, for the counters a boat has never had set. Passed as a node so `kv()`
   keeps the row instead of dropping it: "no interval set" is the answer. */
const tasksDim = t => el("span", "text-body-secondary", t);
const tasksNum = t => el("span", "num", t);

function tasksPage(here) {
  const v = el("div", "t-page");
  v.append(crumbs([{ label: "Tasks", href: href("tasks") }, { label: here }]));
  return v;
}

function tasksLoading(what) {
  const v = tasksPage(what);
  v.append(note("loading", "Reading…"));
  mount(v);
}

function tasksMiss(crumb, line) {
  const v = tasksPage(crumb);
  v.append(note("error", line));
  const back = el("a", "btn btn-outline-secondary btn-sm");
  back.href = href("tasks");
  back.innerHTML = '<i class="bi bi-arrow-left me-1"></i>Back to the list';
  v.append(back);
  mount(v);
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
    wrap.append(el("p", "mb-0 text-body-secondary",
                   "No note was written — see the photograph."));
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
  wrap.lastChild.classList.add("mb-0");
  return wrap;
}

/* ── photographs ──────────────────────────────────────────────────────────────────── */

/* The photographs live with the snag service on its own port, not with the dashboard, for
   the reason set out in docs/SNAGS.md: the write surface is a separate process. If it is
   not running the tile says so in its own frame. A missing picture that looks like a
   missing picture is honest; one that looks like no picture was ever taken is not. */
function tasksPhotoUrl(snags, name) {
  const base = snagOrigin(snags);
  return `${base}/photo?boat=${encodeURIComponent(snags.boat || "")}` +
         `&name=${encodeURIComponent(name)}`;
}

function tasksGallery(snags, names) {
  const row = el("div", "row g-3");
  const shots = names.map(n => ({ name: n, url: tasksPhotoUrl(snags, n) }));
  shots.forEach((shot, i) => {
    const col = el("div", "col-6 col-md-4");
    const a = el("a", "card h-100 text-decoration-none link-body-emphasis overflow-hidden");
    a.href = shot.url;
    a.target = "_blank";
    a.rel = "noopener";
    a.title = shot.name;
    const frame = el("div", "ratio ratio-4x3 bg-body-tertiary");
    const img = el("img", "card-img-top object-fit-cover");
    img.src = shot.url;
    img.alt = shot.name;
    img.loading = "lazy";
    img.onerror = () => {
      /* Never a broken image icon. The tile says which service is not answering, because
         "the photograph exists and this page cannot reach it" is a different fact from
         "nobody took one". */
      frame.textContent = "";
      const miss = el("div",
        "d-flex flex-column justify-content-center text-center p-3 small text-body-secondary");
      miss.append(el("div", "text-break", shot.name));
      miss.append(el("div", "mt-1", `needs the snag service at ${snagOrigin(snags)}`));
      frame.append(miss);
    };
    frame.append(img);
    a.append(frame);
    a.append(el("div", "card-body p-2 small text-body-secondary",
                `${i + 1} / ${shots.length}`));
    a.onclick = ev => {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;
      ev.preventDefault();
      tasksLightbox(shots, i, snagOrigin(snags));
    };
    col.append(a);
    row.append(col);
  });
  return row;
}

/* ── the lightbox ─────────────────────────────────────────────────────────────────── */

/* A Bootstrap modal, built when it is opened and removed from the DOM when it closes, so
   nothing accumulates behind a page somebody walks away from. Escape is Bootstrap's own;
   the arrows are ours, captured so the page's own keys never see them. */
let tasksLb = null;

function tasksLightbox(shots, start, where) {
  let i = start;

  const m = el("div", "modal fade");
  m.tabIndex = -1;
  m.setAttribute("aria-label", "Photograph");
  const dlg = el("div",
    "modal-dialog modal-xl modal-fullscreen-sm-down modal-dialog-centered");
  const box = el("div", "modal-content");

  const head = el("div", "modal-header");
  const name = el("h2", "modal-title h6 mb-0 text-break me-2");
  const of = el("span", "small text-body-secondary text-nowrap ms-auto me-2 num");
  const shut = el("button", "btn-close");
  shut.type = "button";
  shut.setAttribute("data-bs-dismiss", "modal");
  shut.setAttribute("aria-label", "Close");
  head.append(name, of, shut);

  const body = el("div",
    "modal-body bg-body-tertiary d-flex align-items-center justify-content-center p-2");

  const foot = el("div", "modal-footer justify-content-between");
  const hint = el("span", "small text-body-secondary", "Esc closes · ← → steps");
  const btns = el("div", "d-flex gap-2");
  const arrow = (label, icon, d) => {
    const b = el("button", "btn btn-outline-secondary");
    b.type = "button";
    b.title = label;
    b.setAttribute("aria-label", label);
    b.innerHTML = `<i class="bi ${icon}"></i>`;
    b.onclick = () => step(d);
    return b;
  };
  const prev = arrow("Previous", "bi-chevron-left", -1);
  const next = arrow("Next", "bi-chevron-right", 1);
  btns.append(prev, next);
  foot.append(hint, btns);

  box.append(head, body, foot);
  dlg.append(box);
  m.append(dlg);

  function step(d) {
    i = (i + d + shots.length) % shots.length;
    paint();
  }

  function paint() {
    const s = shots[i];
    name.textContent = s.name;
    of.textContent = `${i + 1} / ${shots.length}`;
    prev.disabled = next.disabled = shots.length < 2;
    body.textContent = "";
    const img = el("img", "t-lb-img mw-100 d-block");
    img.src = s.url;
    img.alt = s.name;
    img.onerror = () => {
      body.textContent = "";
      body.append(el("div", "text-body-secondary text-center p-4",
        `${s.name} is filed with this entry, and the service that holds it is not ` +
        `answering at ${where}. The photograph exists; this page cannot reach it.`));
    };
    body.append(img);
  }

  /* Captured, and stopped here. The arrows step the picture and nothing else: without the
     stop they would also be read by the page's own keys, which walk the list underneath
     the picture somebody is looking at. */
  const keys = ev => {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    ev.preventDefault();
    ev.stopPropagation();
    step(ev.key === "ArrowLeft" ? -1 : 1);
  };

  paint();
  document.body.append(m);
  document.addEventListener("keydown", keys, true);
  m.addEventListener("hidden.bs.modal", () => {
    document.removeEventListener("keydown", keys, true);
    tasksLb = null;
    m.remove();
  });
  tasksLb = m;
  new bootstrap.Modal(m).show();
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
async function tasksPapers(holder, title, where, docs, skip) {
  const q = tasksQuery(title, where);
  holder.textContent = "";
  holder.append(cardBody(note("loading", "Asking the papers…")));

  if (!q) {
    holder.textContent = "";
    holder.append(cardBody(el("p", "mb-0 text-body-secondary",
      "There is nothing in this entry to ask the papers with.")));
    return;
  }

  const r = await api("/api/ask?q=" + encodeURIComponent(q) + "&limit=9");
  holder.textContent = "";

  if (r && r.error) {
    holder.append(cardBody(note("error", `The library did not answer: ${r.error}`)));
    return;
  }

  const hits = ((r && r.passages) || [])
    .filter(h => !(skip && `${h.heading || ""} ${h.text || ""}`.includes(skip)));
  if (!hits.length) {
    holder.append(cardBody(el("p", "mb-0 text-body-secondary",
      `Nothing in the papers mentions this. Asked ${nf(r && r.documents)} document(s) ` +
      `for “${q}”, and that is an answer rather than a failure — this boat's library ` +
      `does not cover it.`)));
    return;
  }

  const titleOf = new Map(
    ((docs && docs.documents) || []).map(d => [d.name, d.title || d.name]));
  const list = el("div", "list-group list-group-flush");
  hits.slice(0, 3).forEach(h => {
    const a = el("a", "list-group-item list-group-item-action py-3");
    a.href = href("docs", `${h.doc}/L${h.line}`);
    const top = el("div", "d-flex flex-wrap align-items-baseline gap-2");
    top.append(el("span", "fw-semibold text-break", titleOf.get(h.doc) || h.doc));
    top.append(el("small", "text-body-secondary text-nowrap ms-auto num",
                  `line ${nf(h.line)}`));
    a.append(top);
    a.append(el("div", "small text-body-secondary text-break",
                h.heading || "(no heading)"));
    a.append(el("p", "small mb-0 mt-2", tasksSnippet(h.text)));
    list.append(a);
  });
  holder.append(list);
  holder.append(cardBody(el("p", "mb-0 small text-body-secondary",
    `Asked for “${q}”. Every answer carries the file and the line it came out of, so it ` +
    `can be walked back to the paper and checked.`)));
}

/* ── the list ─────────────────────────────────────────────────────────────────────── */

const TASKS_CHIPS = [["all", "All"], ["open", "Open"], ["snags", "Snags"],
                     ["service", "Service"]];

function tasksRows(snags, maint) {
  const out = [];
  ((snags && snags.snags) || []).forEach(s => out.push({
    kind: "snag", id: "snag:" + s.when, title: tasksTitle(s), where: s.where, when: s.when,
    open: s.open, status: s.status, by: s.by, assigned: s.assigned || "", raw: s,
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

/* A sentence built out of counted things, as nodes: the numbers are bold, the rest is
   prose, and nothing is assembled out of a string that could carry markup. */
function tasksSentence(parts) {
  const p = el("p", "small text-body-secondary mt-3 mb-0");
  parts.forEach(x => p.append(x instanceof Node ? x : document.createTextNode(String(x))));
  return p;
}
const tasksB = t => el("b", "text-body", String(t));

const TASKS_FOOT =
  "Snags come from whoever stood in front of the fault with a phone; service items come " +
  "from the engine's own running hours. Pick one to read it whole.";

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

  const v = el("div");

  /* The chips are anchors, not buttons, because a filtered list is a place you can send
     somebody: the filter is in the address and a copied link arrives narrowed.

     Their counts obey the same rule as the sentence at the foot: one of the two sources
     failing makes every one of them short by an unknown number, and "Snags 0" beside an
     error line reads as the boat having none. An em dash, and the error says why. */
  const counted = !broken.length;
  const chips = el("div", "btn-group t-chips");
  chips.setAttribute("role", "group");
  chips.setAttribute("aria-label", "Filter");
  TASKS_CHIPS.forEach(([id, label]) => {
    const a = el("a", "btn btn-outline-secondary" + (filter === id ? " active" : ""));
    a.href = href("tasks", null, id === "all" ? null : { f: id });
    if (filter === id) a.setAttribute("aria-current", "true");
    a.append(document.createTextNode(label));
    const b = el("span", "badge rounded-pill text-bg-light border ms-2 num",
                 counted ? String(counts[id]) : "—");
    if (!counted) b.title = "not counted — one of the two sources could not be read";
    a.append(b);
    chips.append(a);
  });

  const card = el("div", "card overflow-hidden");
  const wrap = el("div");
  card.append(wrap);
  const tail = el("div");

  v.append(toolbar("Filter what this boat owes…",
                   () => { state.sel = null; draw(); }, [chips]));
  v.append(card, tail);
  mount(v);

  /* What the filter box searches. Stringifying the whole record searched the JSON's own
     key names too, so "body", "photos" and "true" each matched every row on the page. */
  all.forEach(r => {
    r.hay = [r.title, r.where, r.by, r.assigned, r.status, r.why, r.verdict, r.kind,
             r.raw && r.raw.body, r.raw && r.raw.description, r.raw && r.raw.item,
             ...(((r.raw && r.raw.updates) || []).map(u => u && u.body))]
            .filter(Boolean).join(" \n ").toLowerCase();
  });

  let shown = [];

  function visible() {
    const q = state.q.trim().toLowerCase();
    return all.filter(r => tasksKeep(r, filter) && (!q || r.hay.includes(q)));
  }

  /* What it is, who has it, where it stands. The first column is never the one that hides
     on a phone: the leading bar the shell paints on a selected row lives on the first cell,
     and a first column narrow enough to disappear would take the bar with it. The source is
     a word in front of the title rather than a column of its own.

     The title is an anchor inside a row that is also clickable: the whole row opens the
     entry, and the anchor is what makes the address copyable and the middle button work. */
  const TASKS_COLS = [
    { label: "What", get: (r, i) => {
        const c = el("div");
        const line = el("div");
        line.append(el("span", "small text-body-secondary text-uppercase me-2", r.kind));
        const a = el("a", "fw-medium text-break link-body-emphasis text-decoration-none",
                     tasksFirst(r.title, 110));
        a.href = href("tasks", r.id);
        a.onclick = ev => { ev.stopPropagation(); state.sel = i; };
        line.append(a);
        c.append(line);
        const sub = r.kind === "snag"
          ? [r.where, r.by, ago(r.when)].filter(Boolean).join("  ·  ")
          : (r.why || "");
        if (sub) c.append(el("div", "small text-body-secondary mt-1", sub));
        return c;
      } },
    /* Who it is on. Low priority, so it is the first thing to go on a phone — on a
       four-inch screen what the fault is and whether it is open are the two columns worth
       the width, and the name is on the entry's own page anyway. */
    { label: "Assigned", w: "9rem", prio: "low", get: r => {
        if (r.kind !== "snag") return "";
        return r.assigned
          ? el("span", "text-break", r.assigned)
          : el("span", "text-body-secondary", "—");
      } },
    { label: "Status", w: "9rem", cls: "text-end", get: r => {
        const c = el("div");
        c.append(tasksBadge(r));
        c.append(el("div", "small text-body-secondary mt-1",
          r.kind === "service" && !r.when ? "never recorded" : ago(r.when)));
        return c;
      } },
  ];

  function draw() {
    shown = visible();
    wrap.textContent = "";
    tail.textContent = "";

    if (broken.length)
      tail.append(note("error",
        `${snags && snags.error ? "The snag list" : "The engine log"} could not be read: ` +
        `${broken[0].error}. This list is short by an unknown number of items.`));

    if (shown.length) {
      wrap.append(table(TASKS_COLS, shown,
                        (r, i) => { state.sel = i; nav("tasks", r.id); }, state.sel));
    } else if (!broken.length) {
      const e = el("div", "p-3 text-body-secondary");
      if (state.q.trim()) {
        e.append(document.createTextNode(`Nothing in ${counts.all} item(s) matches `));
        e.append(el("b", "", state.q));
        e.append(document.createTextNode("."));
      } else {
        e.textContent = "Nothing is filed under this filter.";
      }
      wrap.append(e);
    }

    if (broken.length) return;                   // the counts below would be half-truths
    const narrowed = filter !== "all" || state.q.trim();
    tail.append(tasksSentence(narrowed
      ? ["Showing ", tasksB(shown.length), " of ", tasksB(counts.all), " item(s), ",
         tasksB(counts.open), " still open in all. ", TASKS_FOOT]
      : [tasksB(counts.all), " item(s), ", tasksB(counts.open),
         " still open in all. ", TASKS_FOOT]));

    if (maint && maint.engine_hours_source)
      tail.append(el("p", "small text-body-secondary mt-2 mb-0", maint.engine_hours_source));
  }

  /* j and k walk the list and Enter opens the selected row, which is only reachable once
     the filter box has given the keyboard up — otherwise j is a letter. */
  tasksKeys.move = d => {
    if (!shown.length) return;
    state.sel = state.sel === null ? 0
              : Math.max(0, Math.min(shown.length - 1, state.sel + d));
    draw();
    const row = wrap.querySelectorAll("tbody tr")[state.sel];
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

/* ── changing a fault's status ────────────────────────────────────────────────────── */

/* The one thing on this page that writes, and it does not write here: it appends a
   follow-up through the snag service on its own port, the way the phone page files. See
   docs/SNAGS.md "Append-only, on purpose". Nothing is rewritten and nothing is reopened
   from a browser — the fault's status is the newest thing anybody wrote about it.

   Who is typing is remembered between visits. A name is a courtesy on a shared boat, not
   a credential, and re-typing it every time is how it stops being written at all. */
const TASKS_BY_KEY = "openboat.console.by";

function tasksWho() {
  try { return localStorage.getItem(TASKS_BY_KEY) || ""; } catch (e) { return ""; }
}
function tasksRemember(who) {
  try { localStorage.setItem(TASKS_BY_KEY, who); } catch (e) { /* private window */ }
}

/* What the entry is told after a write lands. It survives one re-render, because the page
   is rebuilt from a fresh /api/snags rather than patched in place. */
let tasksFlash = null;

const TASKS_MOVES = {
  review: { label: "Needs review", title: "Mark as review",
            placeholder: "the idea, or what to look at",
            asks: "Say what to look at — the service files a follow-up, and a follow-up " +
                  "with nothing written on it is refused." },
  fixed:  { label: "Fixed", title: "Mark as fixed",
            placeholder: "what was done",
            asks: "Say what was done — closing a fault without that is refused." },
};

/* From open you can send it to review or close it; from review you can close it. Nothing
   here reopens anything: that is a hand edit, and the card at the foot of the page says
   where. */
function tasksMovesFrom(word) {
  if (TASKS_CLOSED.includes(word)) return [];
  if (word === "review") return ["fixed"];
  return ["review", "fixed"];
}

function tasksStatusControl(s, snags) {
  const word = tasksWord(s.status) || (s.open ? "open" : "fixed");
  const moves = tasksMovesFrom(word);
  if (!moves.length)
    return el("span", "small text-body-secondary align-self-center",
              "closed — reopen by editing the file");

  const group = el("div", "btn-group");
  const b = el("button", "btn btn-outline-primary btn-sm dropdown-toggle", "Change status");
  b.type = "button";
  b.setAttribute("data-bs-toggle", "dropdown");
  b.setAttribute("aria-expanded", "false");
  const menu = el("ul", "dropdown-menu dropdown-menu-end");
  moves.forEach(to => {
    const li = el("li");
    const item = el("button", "dropdown-item", TASKS_MOVES[to].label);
    item.type = "button";
    item.onclick = () => tasksStatusModal(s, snags, to);
    li.append(item);
    menu.append(li);
  });
  group.append(b, menu);
  return group;
}

function tasksStatusModal(s, snags, to) {
  const move = TASKS_MOVES[to];

  const m = el("div", "modal fade");
  m.tabIndex = -1;
  const dlg = el("div", "modal-dialog modal-dialog-centered modal-fullscreen-sm-down");
  const box = el("div", "modal-content");

  const head = el("div", "modal-header");
  head.append(el("h2", "modal-title h5 mb-0", move.title));
  const x = el("button", "btn-close");
  x.type = "button";
  x.setAttribute("data-bs-dismiss", "modal");
  x.setAttribute("aria-label", "Close");
  head.append(x);

  const body = el("div", "modal-body");
  const said = el("p", "small text-body-secondary",
    "This is appended to the boat's file as a follow-up, with your name on it. Nothing " +
    "already written is changed.");
  body.append(said);

  const errBox = el("div", "alert alert-danger d-none");
  errBox.setAttribute("role", "alert");
  body.append(errBox);

  const noteWrap = el("div", "mb-3");
  const noteLabel = el("label", "form-label", "Note");
  const note_ = el("textarea", "form-control");
  note_.rows = 4;
  note_.placeholder = move.placeholder;
  note_.id = "t-note-" + to;
  noteLabel.htmlFor = note_.id;
  const bad = el("div", "invalid-feedback", move.asks);
  noteWrap.append(noteLabel, note_, bad);
  body.append(noteWrap);

  const whoWrap = el("div");
  const whoLabel = el("label", "form-label", "Your name");
  const who = el("input", "form-control");
  who.type = "text";
  who.id = "t-by-" + to;
  who.value = tasksWho();
  who.autocomplete = "name";
  whoLabel.htmlFor = who.id;
  whoWrap.append(whoLabel, who);
  body.append(whoWrap);

  const foot = el("div", "modal-footer");
  const cancel = el("button", "btn btn-outline-secondary", "Cancel");
  cancel.type = "button";
  cancel.setAttribute("data-bs-dismiss", "modal");
  const send = el("button", "btn btn-primary", move.title);
  send.type = "button";
  foot.append(cancel, send);

  box.append(head, body, foot);
  dlg.append(box);
  m.append(dlg);

  const fail = line => {
    errBox.textContent = line;
    errBox.classList.remove("d-none");
  };

  send.onclick = async () => {
    /* The service refuses an entry with neither a note nor a photograph, and refuses a
       close with nothing said about what was done. Both are worth catching here, where the
       words are still in the box, rather than after a round trip. */
    const text = note_.value.trim();
    note_.classList.toggle("is-invalid", !text);
    if (!text) { note_.focus(); return; }
    errBox.classList.add("d-none");

    send.disabled = cancel.disabled = true;
    const spin = el("span", "spinner-border spinner-border-sm me-2");
    spin.setAttribute("aria-hidden", "true");
    send.textContent = "";
    send.append(spin, document.createTextNode("Filing…"));

    const by = who.value.trim();
    const r = await snagPost(snags, { follow_up_to: s.when, status: to, note: text, by });

    if (r && r.ok) {
      tasksRemember(by);
      /* ⌘K holds its own copy of the snag list, and a stale one would offer the fault at
         the status it used to have. Drop it and let the next question ask the boat. */
      state.index = null;
      tasksFlash = `Filed as ${to}. It is a follow-up in the boat's file, appended just now.`;
      const inst = bootstrap.Modal.getInstance(m);
      if (inst) inst.hide(); else m.remove();
      tasksDetail("snag", s.when);
      return;
    }

    send.disabled = cancel.disabled = false;
    send.textContent = move.title;
    fail((r && r.error) || "the snag service did not say what went wrong");
  };

  document.body.append(m);
  m.addEventListener("hidden.bs.modal", () => m.remove());
  m.addEventListener("shown.bs.modal", () => note_.focus());
  new bootstrap.Modal(m).show();
}

/* ── handing a fault to somebody ──────────────────────────────────────────────────── */

/* A fault sitting on a list is nobody's. Writing a name against it is the smallest thing
   that changes that, and like everything else here it is a follow-up appended to the boat's
   file — the newest name anybody wrote is the one it is on.

   The names people type are remembered in this browser, because on a boat the same three or
   four names come round again and again and re-typing one is how it stops being written. */
const TASKS_ASSIGNEE_KEY = "openboat.console.assignee";

function tasksAssignees() {
  try {
    const raw = JSON.parse(localStorage.getItem(TASKS_ASSIGNEE_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter(x => typeof x === "string").slice(0, 12) : [];
  } catch (e) { return []; }
}
function tasksRememberAssignee(name) {
  if (!name) return;
  try {
    const kept = [name].concat(tasksAssignees().filter(x => x !== name)).slice(0, 12);
    localStorage.setItem(TASKS_ASSIGNEE_KEY, JSON.stringify(kept));
  } catch (e) { /* private window */ }
}

function tasksAssignControl(s, snags) {
  const b = el("button", "btn btn-outline-secondary btn-sm",
               s.assigned ? "Reassign" : "Assign");
  b.type = "button";
  b.onclick = () => tasksAssignModal(s, snags);
  return b;
}

function tasksAssignModal(s, snags) {
  const held = s.assigned || "";

  const m = el("div", "modal fade");
  m.tabIndex = -1;
  const dlg = el("div", "modal-dialog modal-dialog-centered modal-fullscreen-sm-down");
  const box = el("div", "modal-content");

  const head = el("div", "modal-header");
  head.append(el("h2", "modal-title h5 mb-0", held ? "Hand it to somebody else" : "Hand it to somebody"));
  const x = el("button", "btn-close");
  x.type = "button";
  x.setAttribute("data-bs-dismiss", "modal");
  x.setAttribute("aria-label", "Close");
  head.append(x);

  const body = el("div", "modal-body");
  body.append(el("p", "small text-body-secondary",
    "A name against the fault, appended to the boat's file as a follow-up. It is who is " +
    "doing it, not who is accountable for it — nothing here notifies anybody."));

  const errBox = el("div", "alert alert-danger d-none");
  errBox.setAttribute("role", "alert");
  body.append(errBox);

  const whoWrap = el("div", "mb-3");
  const whoLabel = el("label", "form-label", "Doing it");
  const who = el("input", "form-control");
  who.type = "text";
  who.id = "t-assign-" + (s.when || "x").replace(/\W+/g, "");
  who.value = held;
  who.autocomplete = "off";
  /* The names already used on this boat, offered rather than imposed: a datalist suggests
     and still lets somebody type a name nobody has used before. */
  const list = el("datalist");
  list.id = who.id + "-names";
  tasksAssignees().forEach(n => { const o = el("option"); o.value = n; list.append(o); });
  who.setAttribute("list", list.id);
  whoLabel.htmlFor = who.id;
  whoWrap.append(whoLabel, who, list);
  body.append(whoWrap);

  const noteWrap = el("div", "mb-3");
  const noteLabel = el("label", "form-label", "Note");
  const note_ = el("textarea", "form-control");
  note_.rows = 3;
  note_.placeholder = "what they are meant to do — optional";
  note_.id = who.id + "-note";
  noteLabel.htmlFor = note_.id;
  noteWrap.append(noteLabel, note_,
    el("div", "form-text", "Optional. The name on its own is a whole entry."));
  body.append(noteWrap);

  const byWrap = el("div");
  const byLabel = el("label", "form-label", "Your name");
  const by = el("input", "form-control");
  by.type = "text";
  by.id = who.id + "-by";
  by.value = tasksWho();
  by.autocomplete = "name";
  byLabel.htmlFor = by.id;
  byWrap.append(byLabel, by);
  body.append(byWrap);

  const foot = el("div", "modal-footer");
  const cancel = el("button", "btn btn-outline-secondary", "Cancel");
  cancel.type = "button";
  cancel.setAttribute("data-bs-dismiss", "modal");
  const back = el("button", "btn btn-outline-secondary", "Hand it back");
  back.type = "button";
  const send = el("button", "btn btn-primary", "Assign");
  send.type = "button";
  foot.append(cancel);
  if (held) foot.append(back);
  foot.append(send);

  box.append(head, body, foot);
  dlg.append(box);
  m.append(dlg);

  const fail = line => { errBox.textContent = line; errBox.classList.remove("d-none"); };

  /* `assigned` is sent as "-" to hand a fault back to nobody. An empty string would be a
     field somebody could send by accident; the dash is a thing you have to mean. */
  const file = async (to, label) => {
    const name = to === "-" ? "" : String(to || "").trim();
    if (to !== "-" && !name) { who.classList.add("is-invalid"); who.focus(); return; }
    who.classList.remove("is-invalid");
    errBox.classList.add("d-none");
    send.disabled = back.disabled = cancel.disabled = true;
    const mine = by.value.trim();
    const r = await snagPost(snags, { follow_up_to: s.when, assigned: to === "-" ? "-" : name,
                                      note: note_.value.trim(), by: mine });
    if (r && r.ok) {
      tasksRemember(mine);
      if (to !== "-") tasksRememberAssignee(name);
      state.index = null;
      tasksFlash = label;
      const inst = bootstrap.Modal.getInstance(m);
      if (inst) inst.hide(); else m.remove();
      tasksDetail("snag", s.when);
      return;
    }
    send.disabled = back.disabled = cancel.disabled = false;
    fail((r && r.error) || "the snag service did not say what went wrong");
  };

  send.onclick = () => file(who.value, `Assigned to ${who.value.trim()}.`);
  back.onclick = () => file("-", "Handed back — this fault is on nobody.");

  document.body.append(m);
  m.addEventListener("hidden.bs.modal", () => m.remove());
  m.addEventListener("shown.bs.modal", () => who.focus());
  new bootstrap.Modal(m).show();
}

/* ── a link for the person who is actually fixing it ──────────────────────────────── */

/* The person with the spanner has no account and should not need one. A share link opens
   one fault — its note, its photographs, its history — and offers two answers back. It is
   minted by the gate, which is the only component here that has a login to check a role
   against, and it is recorded as a follow-up in the boat's own file so that who was given
   a way in is readable by a person and can be taken back by one.

   Served plainly there is no gate, no account and no `OB_USER`, so none of this appears. */
function tasksMayShare() {
  const me = window.OB_USER;
  return !!me && (me.role === "owner" || me.role === "admin");
}

function tasksShareButton(s, snags) {
  const b = el("button", "btn btn-outline-secondary btn-sm");
  b.type = "button";
  b.innerHTML = '<i class="bi bi-link-45deg me-1"></i>Share with the person fixing it';
  b.onclick = () => tasksShareModal(s, snags);
  return b;
}

function tasksShareModal(s, snags) {
  const m = el("div", "modal fade");
  m.tabIndex = -1;
  const dlg = el("div", "modal-dialog modal-dialog-centered modal-fullscreen-sm-down");
  const box = el("div", "modal-content");

  const head = el("div", "modal-header");
  head.append(el("h2", "modal-title h5 mb-0", "Share this fault"));
  const x = el("button", "btn-close");
  x.type = "button";
  x.setAttribute("data-bs-dismiss", "modal");
  x.setAttribute("aria-label", "Close");
  head.append(x);

  const body = el("div", "modal-body");
  body.append(el("p", "small text-body-secondary",
    "A link to this one fault, with its photographs, and a form for them to answer on. " +
    "Whoever holds the link needs no account and can reach nothing else on the boat."));

  const errBox = el("div", "alert alert-danger d-none");
  errBox.setAttribute("role", "alert");
  body.append(errBox);

  const form = el("div");
  const labWrap = el("div", "mb-3");
  const labLabel = el("label", "form-label", "Who is it for");
  const label = el("input", "form-control");
  label.type = "text";
  label.id = "t-share-label";
  label.placeholder = "the name you will recognise it by";
  label.autocomplete = "off";
  labLabel.htmlFor = label.id;
  labWrap.append(labLabel, label,
    el("div", "form-text", "Written into the boat's file beside the link, so that six " +
                           "weeks later it says who was given one."));
  const dayWrap = el("div");
  const dayLabel = el("label", "form-label", "Good for");
  const days = el("select", "form-select");
  days.id = "t-share-days";
  [[7, "a week"], [30, "a month"], [90, "three months"]].forEach(([n, word]) => {
    const o = el("option", "", `${n} days — ${word}`);
    o.value = String(n);
    if (n === 30) o.selected = true;
    days.append(o);
  });
  dayLabel.htmlFor = days.id;
  dayWrap.append(dayLabel, days);
  form.append(labWrap, dayWrap);
  body.append(form);

  /* Where the finished link appears. Read-only rather than disabled: a disabled field
     cannot be selected, and selecting the text is the fallback when the clipboard is not
     available — which on an unencrypted origin it is not. */
  const out = el("div", "d-none");
  const outLabel = el("label", "form-label", "The link");
  const group = el("div", "input-group");
  const url = el("input", "form-control");
  url.type = "text";
  url.readOnly = true;
  url.id = "t-share-url";
  outLabel.htmlFor = url.id;
  const copy = el("button", "btn btn-outline-secondary", "Copy");
  copy.type = "button";
  group.append(url, copy);
  const wa = el("a", "btn btn-outline-secondary btn-sm mt-2");
  wa.target = "_blank";
  wa.rel = "noopener";
  wa.innerHTML = '<i class="bi bi-whatsapp me-1"></i>Send it on WhatsApp';
  const untilLine = el("div", "form-text");
  const waWrap = el("div", "mt-2");
  waWrap.append(wa);
  out.append(outLabel, group, untilLine, waWrap);
  body.append(out);

  const foot = el("div", "modal-footer");
  const cancel = el("button", "btn btn-outline-secondary", "Cancel");
  cancel.type = "button";
  cancel.setAttribute("data-bs-dismiss", "modal");
  const send = el("button", "btn btn-primary", "Make the link");
  send.type = "button";
  const done = el("button", "btn btn-primary d-none", "Done");
  done.type = "button";
  done.setAttribute("data-bs-dismiss", "modal");
  foot.append(cancel, send, done);

  box.append(head, body, foot);
  dlg.append(box);
  m.append(dlg);

  const fail = line => { errBox.textContent = line; errBox.classList.remove("d-none"); };

  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(url.value);
      copy.textContent = "Copied";
      setTimeout(() => { copy.textContent = "Copy"; }, 1600);
    } catch (e) {
      url.focus();
      url.select();
      copy.textContent = "Press ⌘C";
    }
  };

  send.onclick = async () => {
    errBox.classList.add("d-none");
    send.disabled = cancel.disabled = true;
    send.textContent = "Making it…";
    const r = await snagPost(snags, { when: s.when, label: label.value.trim(),
                                      days: Number(days.value) || 30 }, "/api/share");
    if (r && r.url) {
      state.index = null;
      form.classList.add("d-none");
      out.classList.remove("d-none");
      url.value = r.url;
      untilLine.textContent = `Stops working on ${String(r.until || "").slice(0, 10)}. ` +
                              `It is listed on this page, and you can take it back there.`;
      wa.href = "https://wa.me/?text=" +
                encodeURIComponent(`${tasksTitle(s)}: ${r.url}`);
      send.classList.add("d-none");
      cancel.classList.add("d-none");
      done.classList.remove("d-none");
      /* The page behind the modal is now out of date — the new link belongs in the list
         of links, which is drawn from the file. Redraw once this closes. */
      m.addEventListener("hidden.bs.modal", () => tasksDetail("snag", s.when));
      url.focus();
      url.select();
      return;
    }
    send.disabled = cancel.disabled = false;
    send.textContent = "Make the link";
    fail((r && r.error) || "the link could not be made — this needs the gate, and an owner");
  };

  document.body.append(m);
  m.addEventListener("hidden.bs.modal", () => m.remove());
  m.addEventListener("shown.bs.modal", () => label.focus());
  new bootstrap.Modal(m).show();
}

/* Every link ever made for this fault, live or not. A revoked one stays on the list on
   purpose: "this was shared and then taken back" is a thing worth being able to read. */
function tasksSharePane(s, snags) {
  const shares = s.shares || [];
  if (!shares.length && !tasksMayShare()) return null;

  const p = pane("Given to somebody outside", shares.length ? String(shares.length) : "NONE");
  if (shares.length) {
    const list = el("div", "list-group list-group-flush");
    shares.forEach(g => {
      const li = el("div", "list-group-item d-flex flex-wrap align-items-center gap-2 py-3");
      const who = el("div", "flex-grow-1");
      who.style.minWidth = "0";
      who.append(el("div", "fw-medium text-break", g.label || "somebody"));
      who.append(el("div", "small text-body-secondary text-break",
                    (g.revoked ? "taken back" : `open until ${String(g.until || "").slice(0, 10)}`) +
                    `  ·  link ${g.id}`));
      li.append(who);
      li.append(g.revoked ? statusCell("muted", "revoked") : statusCell("ok", "live"));
      if (!g.revoked && tasksMayShare()) {
        const off = el("button", "btn btn-outline-secondary btn-sm", "Revoke");
        off.type = "button";
        off.onclick = async () => {
          off.disabled = true;
          off.textContent = "Revoking…";
          const r = await snagPost(snags, {
            follow_up_to: s.when, unshare: g.id,
            note: `Took back the link given to ${g.label || "somebody"}.`,
            by: tasksWho() });
          if (r && r.ok) {
            state.index = null;
            tasksFlash = "That link no longer opens anything.";
            tasksDetail("snag", s.when);
            return;
          }
          off.disabled = false;
          off.textContent = "Revoke";
          li.append(el("div", "w-100 small text-danger mt-2",
                       (r && r.error) || "the snag service did not say what went wrong"));
        };
        li.append(off);
      }
      list.append(li);
    });
    p.append(list);
  } else {
    p.append(cardBody(el("p", "mb-0 text-body-secondary",
      "Nobody outside has been given this fault. A link opens this one entry and nothing " +
      "else, expires on its own, and can be taken back here.")));
  }
  return p;
}

/* The events one follow-up carries besides its words: who it was handed to, and a link
   given or taken back. Returned as nodes so nothing is assembled out of a string. */
function tasksEvents(u) {
  const out = [];
  const line = t => el("div", "small text-body-secondary mt-2", t);
  if (u.assigned === "-") out.push(line("handed back to nobody"));
  else if (u.assigned) out.push(line("assigned to " + u.assigned));
  (u.share || []).forEach(raw => {
    const bits = String(raw).split(" ");
    out.push(line("shared with " + (bits.slice(2).join(" ") || "somebody") +
                  " until " + String(bits[1] || "").slice(0, 10)));
  });
  (u.unshare || []).forEach(id => out.push(line("link " + id + " taken back")));
  return out;
}

function tasksSnagPage(s, snags, docs) {
  const index = tasksLinkIndex(docs);
  const title = tasksTitle(s);
  const v = tasksPage(tasksFirst(title, 90));

  if (tasksFlash) { v.append(note("ok", tasksFlash)); tasksFlash = null; }

  /* The whole status line, not just the word the badge carries: somebody wrote "fixed —
     replaced the striker plate" and the half after the dash is the part worth reading. */
  const full = String(s.status || "").trim();
  const h1 = el("span", "text-break", title);
  const actions = [tasksSnagBadge(s.status, s.open), tasksStatusControl(s, snags)];
  if (s.open) actions.push(tasksAssignControl(s, snags));
  if (s.open && tasksMayShare()) actions.push(tasksShareButton(s, snags));
  v.append(pageHead(h1, [
    s.when ? `filed ${s.when}` : null,
    s.when ? ago(s.when) : null,
    s.by ? `by ${s.by}` : null,
    s.where || null,
    s.assigned ? `with ${s.assigned}` : null,
    full.split(/\s+/).length > 1 ? full : null,
  ], actions));

  const written = pane("What was written", "NOTE");
  written.append(cardBody(tasksProse(s.body, index)));
  v.append(written);

  const photos = s.photos || [];
  const shots = pane("Photographs", photos.length ? `${photos.length} FILED` : "NONE");
  shots.append(cardBody(photos.length
    ? tasksGallery(snags, photos)
    : el("p", "mb-0 text-body-secondary",
         "No photograph was filed with this entry — it was written, not photographed.")));
  v.append(shots);

  const ups = (s.updates || []).slice()
                .sort((a, b) => String(a.when).localeCompare(String(b.when)));
  const tl = pane("Follow-ups", ups.length ? String(ups.length) : "NONE");
  if (ups.length) {
    const list = el("div", "list-group list-group-flush");
    ups.forEach(u => {
      const li = el("div", "list-group-item py-3");
      const w = el("div", "small text-body-secondary mb-2 d-flex flex-wrap align-items-center gap-2");
      const when = el("span");
      when.append(document.createTextNode(u.when || "—"));
      if (u.by) when.append(document.createTextNode(" · " + u.by));
      w.append(when);
      /* A follow-up that says nothing about status carries no badge, because it changed
         nothing: silence is "no change", not "still open". */
      if (tasksWord(u.status)) w.append(tasksSnagBadge(u.status));
      li.append(w);
      li.append(tasksProse(u.body, index));
      /* What else this entry did, as events under its words. A follow-up whose whole
         content is "it is Jo's now" has nothing to read otherwise. */
      tasksEvents(u).forEach(line => li.append(line));
      if ((u.photos || []).length) {
        const g = tasksGallery(snags, u.photos);
        g.classList.add("mt-3");
        li.append(g);
      }
      list.append(li);
    });
    tl.append(list);
  } else {
    tl.append(cardBody(el("p", "mb-0 text-body-secondary",
      "Nothing has been added since this was filed. It stands as it was written, " +
      "unverified, by whoever noticed it.")));
  }
  v.append(tl);

  const given = tasksSharePane(s, snags);
  if (given) v.append(given);

  const papers = pane("In the papers", "ASKED");
  const holder = el("div");
  papers.append(holder);
  v.append(papers);

  /* docs/SNAGS.md "Append-only, on purpose" is the authority for this card: `record()` in
     openboat/snag.py has no code path that edits an existing entry, so a status change is
     a new line at the end of the file and reopening is somebody at a desk with an editor. */
  const close = pane("Changing this, and undoing it", "APPENDED");
  const p = el("p", "mb-0 small text-body-secondary");
  const bit = t => el("code", "", t);
  p.append(document.createTextNode(
    "Nothing on this page rewrites anything. Marking this fault review or fixed appends a " +
    "follow-up through the snag service on its own port, with a name and a note on it, and " +
    "the fault's status is the newest thing anybody wrote about it. Closing one without " +
    "saying what was done is refused. Reopening is done the way faults used to be closed: " +
    "by hand in this boat's "));
  p.append(bit("SNAGS.md"));
  p.append(document.createTextNode(", beside the "));
  p.append(bit("boat.toml"));
  p.append(document.createTextNode(" the profile points at, where "));
  p.append(bit("**Status:** fixed"));
  p.append(document.createTextNode(" becomes "));
  p.append(bit("**Status:** open"));
  p.append(document.createTextNode(
    " again. Every entry also carries a standing mark that it is unverified: recorded from " +
    "a phone at the moment of noticing, confirmed by nobody since."));
  close.append(cardBody(p));
  v.append(close);

  mount(v);
  tasksPapers(holder, title, s.where, docs, s.when);
}

function tasksServicePage(m, maint, docs) {
  const index = tasksLinkIndex(docs);
  const v = tasksPage(tasksFirst(m.description || m.item, 90));
  const r = { kind: "service", verdict: m.verdict };

  v.append(pageHead(el("span", "text-break", m.description || m.item), [
    m.item,
    m.last ? `last done ${m.last}` : "never recorded",
    m.last ? ago(m.last) : null,
    m.per_outing ? "counted per salt-water outing" : null,
  ], [tasksBadge(r)]));

  const why = pane("Why it is owed", "REASON");
  why.append(cardBody(tasksProse(m.why, index)));
  v.append(why);

  /* Sourced or absent: a counter the API did not give shows as the sentence that says so,
     never as a zero. */
  const has = x => x !== null && x !== undefined;
  const counters = pane("The counters behind it", "SOURCED");
  counters.append(cardBody(kv([
    ["Item", m.item || tasksDim("—")],
    ["Last done", m.last || tasksDim("never recorded")],
    ["Hours since", has(m.hours_since) ? tasksNum(`${Number(m.hours_since).toFixed(1)} h`)
                                       : tasksDim("—")],
    ["Days since", has(m.days_since) ? tasksNum(`${nf(m.days_since)} d`) : tasksDim("—")],
    ["Outings since", has(m.outings_since) ? tasksNum(nf(m.outings_since)) : tasksDim("—")],
    ["Interval, hours", m.interval_hours ? tasksNum(`${nf(m.interval_hours)} h`)
                                         : tasksDim("no interval set")],
    ["Interval, months", m.interval_months ? tasksNum(`${nf(m.interval_months)} months`)
                                           : tasksDim("no interval set")],
    ["Interval, days", m.interval_days ? tasksNum(`${nf(m.interval_days)} days`)
                                       : tasksDim("no interval set")],
    ["Every outing", has(m.per_outing) ? (m.per_outing ? "yes" : "no") : tasksDim("—")],
    ["Verdict", m.verdict || tasksDim("—")],
  ], null)));

  /* The running-hours line is quoted exactly as maintenance.py wrote it. It is the one
     sentence that says how much of this engine's life the log actually covers, and
     paraphrasing it would be paraphrasing the boat's own uncertainty. */
  if (maint && maint.engine_hours_source)
    counters.append(cardBody(el("p", "mb-0 small text-body-secondary",
                                maint.engine_hours_source)));
  v.append(counters);

  const papers = pane("In the papers", "ASKED");
  const holder = el("div");
  papers.append(holder);
  v.append(papers);

  /* docs/JOBS.md is the authority here: recording a service resets the clock on an
     interval and everything downstream is computed from it, which is not the correct
     weight for a tap on a wet tablet. */
  const rec = pane("Record it", "BY HAND");
  const cmd = el("pre", "bg-body-tertiary p-3 rounded border mb-0 small");
  cmd.textContent = `python3 -m openboat.maintenance --did ${m.item}` +
                    `\npython3 -m openboat.maintenance --did ${m.item} ` +
                    `--note "what was used, who did it"`;
  rec.append(cardBody(
    el("p", "text-body-secondary",
       "This page has no button, on purpose. Recording a service resets the clock on an " +
       "interval, and the next due date, the cooling trend and the season report are all " +
       "computed from it. It is typed at a keyboard by somebody who knows the work " +
       "actually happened:"),
    cmd,
    el("p", "small text-body-secondary mt-3 mb-0",
       "Add --on YYYY-MM-DD if it happened on a day other than today, and " +
       "--history to see what has been done.")));
  v.append(rec);

  mount(v);
  tasksPapers(holder, m.description || m.item, m.item, docs);
}

/* ── the keyboard ─────────────────────────────────────────────────────────────────── */

/* Registered once, at load, and it minds its own view. Escape on a detail page is the
   back button when there is somewhere to go back to, and the list when this tab was
   opened cold on a deep link. Nothing fires while a modal is up: the picture and the
   search sheet have their own keys. */
function tasksKeys(ev) {
  if (state.view !== "tasks" || tasksLb || overlay()) return;
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
