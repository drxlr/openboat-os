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
  const want = tasksFromAddress(sub);
  if (want) return tasksDetail(want.kind, want.id, want);
  return tasksList();
}

/* ── the address of one item ───────────────────────────────────────────────────────
   A fault is identified by the second it was filed, which is right — it is the key in the
   file and it never moves. Spelling it into a URL as `snag:2026-09-06 14:07:56` was not:
   a colon and a space both percent-escape, so the bar showed
   `snag%3A2026-09-06%2014%3A07%3A56` — unreadable, unpasteable into a message without
   mangling, and it made every link look machine-generated.

   Same identity, written for a URL: `snag-20260906-140756`, plus a slug of the title that
   is decoration only. The slug is never read back, so renaming a fault does not break a
   link somebody already sent, and an old link with a stale slug still opens the right
   entry. The pre-slug form is still understood, because links were sent today. */
function tasksSlug(text) {
  return String(text || "").toLowerCase()
    .replace(/[\u2018\u2019\u201c\u201d]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .split("-").slice(0, 6).join("-");
}

function tasksAddress(kind, id, title, code) {
  if (kind === "service") return String(id);
  const slug = tasksSlug(title);
  /* The handle, then the name for whoever reads the link. Only the handle is read back,
     so a renamed fault keeps every link ever sent for it. */
  if (code) return String(code) + (slug ? "-" + slug : "");
  const stamp = String(id || "").replace(/[^0-9]/g, "");
  if (stamp.length < 14) return "snag-" + encodeURIComponent(id);
  return "snag-" + stamp.slice(0, 8) + "-" + stamp.slice(8, 14) + (slug ? "-" + slug : "");
}

function tasksFromAddress(sub) {
  const raw = decodeURIComponent(String(sub || ""));
  if (!raw) return null;
  /* Six of the handle alphabet is a fault; a bare number is one from the hour this was
     briefly a counter. Everything else is a service item, named by its own key. */
  const hand = raw.match(/^([0-9a-hjkmnp-tv-z]{6})(?:-.*)?$/);
  if (hand) return { kind: "snag", code: hand[1] };
  const num = raw.match(/^(\d+)(?:-.*)?$/);
  if (num) return { kind: "snag", n: Number(num[1]) };
  /* Every shape this console has ever put in a link, because they have been sent. */
  if (raw.startsWith("snag:"))    return { kind: "snag", id: raw.slice(5) };
  if (raw.startsWith("service:")) return { kind: "service", id: raw.slice(8) };
  const m = raw.match(/^snag-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:-.*)?$/);
  if (m) return { kind: "snag",
                  id: `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` };
  if (raw.startsWith("service-")) return { kind: "service", id: raw.slice(8) };
  return { kind: "service", id: raw };
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
  /* A title set on purpose is the title. The reconstruction below is a repair for headings
     the recorder cut mid-word, and repairing a name somebody chose would undo it. */
  if (s && s.renamed) return String(s.title || "").trim() || "—";
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

/* A headline is the first sentence, wherever a fault is named.
   
   `tasksTitle` returns the note's first *line*, which is a repair for headings the recorder
   cut mid-word — but a note typed in one go has no line break until the paragraph ends, so
   the "title" of a carefully written fault is three sentences of diagnosis. That is right
   for the note and wrong for a heading, and it was wrong in both places a fault is named:
   the row, and the h1 of its own page. The rest is never lost — it is the note, directly
   below, whole. */
function tasksHeadline(t) {
  return tasksFirst(t, 120).replace(/\s*[.;,]+$/, "");
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

/* The crumb is the way back, not a second heading. It used to end with the fault's own
   name, which put the same sentence twice in the top two lines of every page — and on a
   short fault a third time, because the note underneath is that sentence. `here` is kept
   in the signature and used as the page's accessible label, so the trail still says where
   you are to a screen reader without printing it above the h1 that already does. */
function tasksPage(here) {
  const v = el("div", "t-page");
  const trail = crumbs([{ label: "Tasks", href: href("tasks") }]);
  if (here) trail.setAttribute("aria-label", "Breadcrumb — " + here);
  v.append(trail);
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
  /(https?:\/\/[^\s<>"'&]+)|(@part:[A-Za-z0-9._-]+)|((?:[A-Za-z0-9._-]+\/)*[A-Za-z0-9._-]+\.(?:pdf|md))/g;

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
    p.innerHTML = esc(chunk).replace(TASKS_MENTION, (m, url, part, file) => {
      if (url) return `<a href="${url}" target="_blank" rel="noopener">${url}</a>`;
      /* `@part:FVSGEL250LE5A00` in a note becomes a way into the drawer, where that part
         and its sellers are. Rendered as a button rather than a link because it goes
         nowhere — it opens a panel over the page you are already reading, and a href that
         does not navigate is a lie the browser tells the status bar. */
      if (part) {
        const num = part.slice("@part:".length);
        return `<button type="button" class="t-part" data-part="${esc(num)}">` +
               `<i class="bi bi-basket2"></i>${esc(num)}</button>`;
      }
      const known = index.get(file.toLowerCase()) ||
                    index.get(file.split("/").pop().toLowerCase());
      return known ? `<a href="${esc(href("docs", known))}">${file}</a>` : file;
    });
    p.querySelectorAll("button.t-part").forEach(b => {
      b.onclick = () => cartOpenAt(b.dataset.part);
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

/* `Done` is not a tidy-up filter, it is the point of keeping the file: a boat's value
   to whoever works on it next is the record of what has already been wrong with it.
   Without a way to ask for the closed ones, that history is written and never read. */
const TASKS_CHIPS = [["all", "All"], ["open", "Open"], ["done", "Done"],
                     ["ideas", "Ideas"], ["snags", "Snags"], ["routines", "Routines"],
                     ["service", "Service"]];

function tasksRows(snags, maint) {
  const out = [];
  ((snags && snags.snags) || []).forEach(s => out.push({
    kind: "snag", id: tasksAddress("snag", s.when, tasksTitle(s), s.code), code: s.code,
    title: tasksTitle(s), where: s.where, when: s.when,
    open: s.open, status: s.status, by: s.by, assigned: s.assigned || "",
    priority: s.priority || "", tags: s.tags || [], sort: s.kind || "fault", raw: s,
  }));
  /* A service item counts as open when the boat is actually owed it — `due` or `soon`.
     An item nobody has ever recorded is `unknown`, and unknown is not a debt: folding ten
     "we have no idea when the impeller was last changed" into the same number as a horn
     that does not work is how a count stops being read. They are all still here, under
     Service and under All, with their cycle on the row. */
  ((maint && maint.items) || []).forEach(m => out.push({
    kind: "service", id: tasksAddress("service", m.item), title: m.description || m.item,
    where: m.item, when: m.last, sort: m.routine ? "routine" : "service",
    open: m.verdict === "due" || m.verdict === "soon", verdict: m.verdict,
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
  /* `open` counts faults and service, never ideas — the whole reason the kind exists.
     A wish nobody has bought is not a thing wrong with the boat. */
  return { all: all.length,
           open: all.filter(r => r.open && r.sort !== "idea").length,
           done: all.filter(r => r.open === false).length,
           ideas: all.filter(r => r.sort === "idea").length,
           routines: all.filter(r => r.sort === "routine").length,
           snags: all.filter(r => r.kind === "snag" && r.sort !== "idea").length,
           service: all.filter(r => r.kind === "service" && r.sort !== "routine").length };
}

function tasksKeep(r, f) {
  if (f === "open")    return r.open && r.sort !== "idea";
  if (f === "done")    return r.open === false;
  if (f === "ideas")   return r.sort === "idea";
  if (f === "routines") return r.sort === "routine";
  if (f === "snags")   return r.kind === "snag" && r.sort !== "idea";
  if (f === "service") return r.kind === "service" && r.sort !== "routine";
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
  if (tasksFlash) { v.append(note("ok", tasksFlash)); tasksFlash = null; }

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

  /* Filing a fault is an anchor to `#tasks/new`, so the form has an address: a phone can
     bookmark it, and a link in a message opens the page with the form already up. */
  const file = el("a", "btn btn-primary text-nowrap");
  file.href = href("tasks", "new");
  file.innerHTML = '<i class="bi bi-camera me-1"></i>File a snag';
  v.append(toolbar("Filter what this boat owes…",
                   () => { state.sel = null; draw(); }, [chips, file]));
  v.append(card, tail);
  mount(v);
  if (state.sub === "new") setTimeout(() => tasksNewSnagModal(snags), 0);

  /* What the filter box searches. Stringifying the whole record searched the JSON's own
     key names too, so "body", "photos" and "true" each matched every row on the page. */
  all.forEach(r => {
    r.hay = [r.title, r.where, r.by, r.assigned, r.status, r.why, r.verdict, r.kind,
             ...(r.tags || []), r.priority && ("p" + r.priority),
             r.raw && r.raw.body, r.raw && r.raw.description, r.raw && r.raw.item,
             ...(((r.raw && r.raw.updates) || []).map(u => u && u.body))]
            .filter(Boolean).join(" \n ").toLowerCase();
  });

  let shown = [];

  function visible() {
    const q = state.q.trim().toLowerCase();
    return all.filter(r => tasksKeep(r, filter) && (!q || r.hay.includes(q)));
  }

  /* One fault, one row, and nothing repeated 27 times.

     The old row printed the word SNAG, the word `open`, and the age twice on every line —
     four pieces of identical furniture per fault, on a list where every fault is a snag and
     every snag is open. What that costs is the headline: by the time the eye reaches the
     sentence it has already read three things that said nothing. So the kind is an icon,
     the default status is drawn by the icon's colour rather than a badge, the age is
     printed once, and what is left at the front of the row is the fault itself.

     Everything on the right is present only when it exists. An unassigned fault shows no
     initial rather than an em dash, an unranked one shows no rank, and a fault nobody has
     tagged shows no tags — a column of placeholders is a column of noise. */

  /* Three things live in this list and they are not the same kind of thing. A fault is
     something wrong, a service item is something due, and an idea is something wanted —
     and the last of those must not look like the first. A shopping link drawn with the
     same tool icon as a flooding seacock is how "28 open" stops being a number anybody
     trusts. Different icon, different colour, counted separately. */
  const TASKS_KIND = { snag:    { icon: "bi-tools",          label: "Fault" },
                       idea:    { icon: "bi-bag",            label: "Idea — wanted, not broken" },
                       routine: { icon: "bi-arrow-repeat",   label: "Routine — it comes round again" },
                       service: { icon: "bi-calendar-check", label: "Service" } };

  /* A headline, not a sentence. The stored line is prose somebody typed on a phone — it
     ends in a full stop and it often runs on into the diagnosis — "…needs checking again.
     It ran until it was out of fuel, so there is air in the system: it turns over and…" —
     which is the whole note, not a headline. So the row takes the first sentence and stops
     there; the rest of what somebody wrote is on the fault's own page, whole. Trailing
     punctuation goes, and CSS makes the final cut at the real width. */
  const tasksHead = tasksHeadline;

  /* `where` is written as "generator — fuel supply and bleeding": a system and a detail.
     The system is the thing worth grouping by eye, so it is set as a chip and the detail
     stays as text beside it. A `where` with no dash is all system and no detail. */
  /* How often, out of the profile. This is the part that makes a service list a plan
     rather than a verdict: "every 200 h / 12 months" is true whether or not anybody has
     ever written down that it was done, and it is what somebody is looking for when they
     ask what the boat needs this winter. */
  function tasksCycle(m) {
    if (!m) return "";
    const bits = [];
    if (m.interval_hours)  bits.push(m.interval_hours + " h");
    if (m.interval_months) bits.push(m.interval_months + " mo");
    if (m.interval_days)   bits.push(m.interval_days + " d");
    if (m.per_outing)      return "every outing";
    return bits.length ? "every " + bits.join(" / ") : "";
  }

  function tasksWhere(where) {
    const raw = String(where || "").trim();
    if (!raw) return ["", ""];
    const cut = raw.split(/\s+[—–-]\s+/);
    return [cut[0].trim(), cut.slice(1).join(" — ").trim()];
  }

  /* A circle and an initial. The name is the title attribute rather than the label because
     the row has one job — showing whether this is on somebody — and four letters of a name
     do that no better than one while costing the headline the width. */
  /* Assigning from the list, not only from the fault's own page. A snag list is read
     down in one pass — "that one's Batuhan's, that one's mine" — and making each of those
     a page load is how a list ends up with 26 faults on nobody. So the circle is the
     control: it opens the same modal the detail page uses and writes the same follow-up.
     Unassigned shows a dashed outline rather than nothing, because an invisible control
     is not one. */
  function tasksFace(name, r, snags) {
    const who = String(name || "").trim();
    if (!r || r.kind !== "snag" || !snags || snags.error)
      return who ? el("span", "t-face", (who[0] || "?").toUpperCase()) : null;
    const f = el("button", "t-face" + (who ? "" : " t-face-none"),
                 who ? (who[0] || "?").toUpperCase() : "+");
    f.type = "button";
    f.title = who ? "Assigned to " + who + " — click to reassign" : "Assign this to somebody";
    f.setAttribute("aria-label", f.title);
    f.onclick = ev => {
      ev.preventDefault();
      ev.stopPropagation();
      tasksAssignModal(r.raw, snags, () => viewTasks());
    };
    return f;
  }

  /* Three ranks, and nothing for the unranked — which is most of them. A rank shown as
     "3" on every unranked fault would be a claim nobody made. */
  function tasksPriority(value) {
    const v = String(value || "").trim();
    if (!v) return null;
    const n = (v.match(/[123]/) || [])[0];
    const p = el("span", "t-pri t-pri-" + (n || "x"), n || v);
    p.title = { 1: "Priority 1 — stops the boat being used, or is unsafe",
                2: "Priority 2 — before the next trip",
                3: "Priority 3 — when somebody is aboard anyway" }[n] || ("Priority " + v);
    return p;
  }

  function tasksRow(r, i, snags) {
    const kind = TASKS_KIND[r.sort] || TASKS_KIND[r.kind] || TASKS_KIND.snag;
    const idea = r.sort === "idea";
    const closed = r.open === false;
    const word = tasksWord(r.status);

    const row = el("div", "t-row" + (closed ? " t-done" : "") +
                          (state.sel === i ? " t-sel" : ""));
    /* The row opens the entry, but a button cannot live inside an anchor — so the anchor
       covers the mark and the words, and the controls sit beside it. */
    const open_ = el("a", "t-open");
    open_.href = href("tasks", r.id);
    open_.onclick = () => { state.sel = i; };

    const mark = el("span", "t-kind" + (closed ? " t-kind-done"
                                      : idea ? " t-kind-idea"
                                      : r.sort === "routine" ? " t-kind-routine" : ""));
    mark.title = closed ? kind.label + " — closed" : kind.label;
    mark.append(el("i", "bi " + (closed ? "bi-check2" : kind.icon)));
    open_.append(mark);

    const main = el("span", "t-main");
    const head = el("span", "t-head");
    if (r.code) head.append(el("span", "t-n", r.code + " "));
    head.append(document.createTextNode(tasksHead(r.title)));
    main.append(head);

    const meta = el("span", "t-meta");
    const [system, detail] = tasksWhere(r.where);
    if (system) meta.append(el("span", "t-chip", system));
    const cycle = r.kind === "service" ? tasksCycle(r.raw) : "";
    if (cycle) meta.append(el("span", "t-chip t-chip-cycle", cycle));
    const steps = r.kind === "service" ? ((r.raw && r.raw.steps) || []).length : 0;
    if (steps) meta.append(el("span", "t-chip t-chip-cycle", steps + " steps"));
    for (const t of (r.tags || [])) meta.append(el("span", "t-chip t-chip-tag", t));
    const words = [detail, r.kind === "snag" ? r.by : r.why].filter(Boolean).join("  ·  ");
    if (words) meta.append(el("span", "t-words", words));
    main.append(meta);
    open_.append(main);
    row.append(open_);

    const side = el("span", "t-side");
    const pri = tasksPriority(r.priority);
    if (pri) side.append(pri);
    /* A badge only when the status is not the one every row shares. `open` is drawn by the
       icon; `review`, `fixed` and anything somebody invented are worth the ink. */
    if (r.kind === "service" || (word && word !== "open" && !idea) || closed)
      side.append(tasksBadge(r));
    const face = tasksFace(r.kind === "snag" ? r.assigned : "", r, snags);
    if (face) side.append(face);
    side.append(el("span", "t-when",
                   r.kind === "service" && !r.when ? "never recorded" : ago(r.when)));
    row.append(side);
    return row;
  }

  function tasksList(rows) {
    const list = el("div", "t-list");
    rows.forEach((r, i) => list.append(tasksRow(r, i, snags)));
    return list;
  }

  function draw() {
    shown = visible();
    wrap.textContent = "";
    tail.textContent = "";

    if (broken.length)
      tail.append(note("error",
        `${snags && snags.error ? "The snag list" : "The engine log"} could not be read: ` +
        `${broken[0].error}. This list is short by an unknown number of items.`));

    if (shown.length) {
      wrap.append(tasksList(shown));
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
    const row = wrap.querySelectorAll(".t-row")[state.sel];
    if (row) row.scrollIntoView({ block: "nearest" });
  };
  tasksKeys.open = () => {
    if (state.sel !== null && shown[state.sel]) nav("tasks", shown[state.sel].id);
  };

  draw();
}

/* ── a detail page ────────────────────────────────────────────────────────────────── */
async function tasksDetail(kind, id, want) {
  const run = ++tasksRun;
  tasksLoading(kind === "snag" ? "one fault" : "one service item");

  const [maint, snags, docs, kits] = await Promise.all(
    [api("/api/maintenance"), api("/api/snags"), api("/api/docs"), api("/api/kits")]);
  if (run !== tasksRun) return;

  /* "No snag is filed at that stamp" is a claim about this boat's SNAGS.md. A request
     that failed supports no such claim, and a link the owner just followed is the worst
     place to guess that a fault was never recorded. */
  if (kind === "snag") {
    if (snags && snags.error)
      return tasksMiss("not answered",
        `The snag list could not be read: ${snags.error}. This entry may well be filed; ` +
        `nobody could ask.`);
    const list = (snags && snags.snags) || [];
    const w = want || {};
    const s = w.code ? list.find(x => x.code === w.code)
            : w.n   ? list.find(x => x.n === w.n)
            :         list.find(x => x.when === id);
    if (!s) return tasksMiss("no such entry",
      w.code ? `Nothing on this boat has the handle ${w.code}. It may belong to another ` +
               `boat — a handle is unique, but the link says which boat to look in.`
      : w.n  ? `This boat has no task ${w.n}.`
      :        `No snag is filed at ${id}. It may have been renamed, or this link may be ` +
               `older than the file.`);
    return tasksSnagPage(s, snags, docs, kits);
  }

  if (maint && maint.error)
    return tasksMiss("not answered",
      `The maintenance table could not be read: ${maint.error}. This item may well be ` +
      `in the profile; nobody could ask.`);
  const m = ((maint && maint.items) || []).find(x => x.item === id);
  if (!m) return tasksMiss("no such item",
    `The profile names no maintenance item called ${id}. Items come from the ` +
    `[maintenance] table in this boat's profile.`);
  return tasksServicePage(m, maint, docs, kits);
}

/* ── the parts a job needs ────────────────────────────────────────────────────────── */

/* The page does not become a shop. A task page is the boat's record — what is wrong, what
   was written, what was photographed — and burying that under a price list would be
   trading the thing that is worth keeping for the thing that is easy to monetise.
 *
 * So the parts live in the drawer on the right, and all this leaves on the page is one
 * line saying they exist. Opening it is a deliberate act, and everything commercial —
 * sellers, prices, the basket — stays behind it. `cart.js` owns that drawer. */
function tasksKitCue(kit, cat) {
  cartSuggest(kit, cat);

  const n = kit.lines.filter(l => !l.optional).length;
  const p = pane("What it needs", `${n} PART${n === 1 ? "" : "S"}`, kit.title || "");
  const body = cardBody();

  const line = el("p", "mb-3 text-break");
  line.textContent = kit.note
    ? tasksFirst(kit.note.replace(/\s+/g, " "), 220)
    : `${n} part${n === 1 ? "" : "s"} written down for this job.`;
  body.append(line);

  const bar = el("div", "d-flex flex-wrap gap-3 align-items-center");
  const open = el("button", "btn btn-primary");
  open.type = "button";
  open.setAttribute("data-bs-toggle", "offcanvas");
  open.setAttribute("data-bs-target", "#cartpanel");
  open.innerHTML = `<i class="bi bi-basket2 me-2"></i>Parts and sellers`;
  bar.append(open);

  /* The figure, if there is an honest one, so the button is worth pressing. The rules for
     what counts are `cart.js`'s and are applied there — this only reads the answer, and
     re-reads it whenever a seller is chosen in the drawer, so the two can never disagree. */
  const summary = el("span", "text-body-secondary");
  const paint = () => {
    const priced = cartKitTotal(kit);
    summary.textContent = priced.total !== null
      ? `about ${cartMoney(priced.total)}${priced.basis} from ` +
        `${priced.sellers} seller${priced.sellers === 1 ? "" : "s"}`
      : priced.why;
  };
  paint();
  cartCueRefresh = paint;
  bar.append(summary);

  if (cat.placeholder)
    bar.append(el("span", "badge rounded-pill text-bg-warning", "placeholder prices"));

  body.append(bar);
  p.append(body);
  return p;
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

const TASKS_TAG_KEY = "openboat.console.tags";

function tasksKnownTags() {
  try {
    const raw = JSON.parse(localStorage.getItem(TASKS_TAG_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter(x => typeof x === "string").slice(0, 24) : [];
  } catch (e) { return []; }
}
function tasksRememberTags(list) {
  try {
    const kept = list.concat(tasksKnownTags().filter(x => !list.includes(x))).slice(0, 24);
    localStorage.setItem(TASKS_TAG_KEY, JSON.stringify(kept));
  } catch (e) { /* private window */ }
}

/* Ranking a fault and labelling it are the same kind of act — somebody deciding something
   about a fault rather than reporting one — so they share one control and one follow-up.
   Like every other write on this page it appends; the newest rank anybody wrote is the
   rank, and the file keeps who changed it and when. */
function tasksRankModal(s, snags) {
  const m = el("div", "modal fade");
  m.tabIndex = -1;
  const dlg = el("div", "modal-dialog modal-dialog-centered modal-fullscreen-sm-down");
  const box = el("div", "modal-content");

  const head = el("div", "modal-header");
  head.append(el("h2", "modal-title h5 mb-0", "Title, priority and tags"));
  const x = el("button", "btn-close");
  x.type = "button";
  x.setAttribute("data-bs-dismiss", "modal");
  x.setAttribute("aria-label", "Close");
  head.append(x);

  const body = el("div", "modal-body");
  body.append(el("p", "small text-body-secondary",
    "Appended to the boat's file as a follow-up with your name on it. Nothing already " +
    "written is changed."));
  const errBox = el("div", "alert alert-danger d-none");
  errBox.setAttribute("role", "alert");
  body.append(errBox);

  const uid = "t-rank-" + Date.now();

  const nameWrap = el("div", "mb-3");
  const nameLabel = el("label", "form-label", "Title");
  const name_ = el("input", "form-control");
  name_.type = "text";
  name_.id = uid + "-n";
  name_.maxLength = 70;
  name_.value = s.renamed ? String(s.title || "") : "";
  name_.placeholder = tasksFirst(s.filed_title || s.title || "", 70);
  nameLabel.htmlFor = name_.id;
  nameWrap.append(nameLabel, name_,
    el("div", "form-text",
       "What the list calls it. Leave it empty to keep the line it was filed under — that " +
       "heading never changes either way, this is only the name shown."));
  body.append(nameWrap);

  const priWrap = el("div", "mb-3");
  const priLabel = el("label", "form-label", "Priority");
  const pri = el("select", "form-select");
  pri.id = uid + "-p";
  priLabel.htmlFor = pri.id;
  [["", "Not ranked"],
   ["1", "1 — stops the boat being used, or is unsafe"],
   ["2", "2 — before the next trip"],
   ["3", "3 — when somebody is aboard anyway"]].forEach(([v, t]) => {
    const o = el("option", "", t);
    o.value = v;
    pri.append(o);
  });
  pri.value = String(s.priority || "").trim();
  priWrap.append(priLabel, pri);
  body.append(priWrap);

  const tagWrap = el("div", "mb-3");
  const tagLabel = el("label", "form-label", "Tags");
  const tag = el("input", "form-control");
  tag.type = "text";
  tag.id = uid + "-t";
  tag.value = (s.tags || []).join(", ");
  tag.placeholder = "electrical, before-passage";
  tag.setAttribute("list", uid + "-tl");
  tagLabel.htmlFor = tag.id;
  const datalist = el("datalist");
  datalist.id = uid + "-tl";
  tasksKnownTags().forEach(t => { const o = el("option"); o.value = t; datalist.append(o); });
  tagWrap.append(tagLabel, tag, datalist,
                 el("div", "form-text", "Comma-separated. Empty clears them."));
  body.append(tagWrap);

  const whoWrap = el("div");
  const whoLabel = el("label", "form-label", "Your name");
  const who = el("input", "form-control");
  who.type = "text";
  who.id = uid + "-b";
  who.value = tasksWho();
  who.autocomplete = "name";
  whoLabel.htmlFor = who.id;
  whoWrap.append(whoLabel, who);
  body.append(whoWrap);

  const foot = el("div", "modal-footer");
  const cancel = el("button", "btn btn-outline-secondary", "Cancel");
  cancel.type = "button";
  cancel.setAttribute("data-bs-dismiss", "modal");
  const send = el("button", "btn btn-primary", "Save");
  send.type = "button";
  foot.append(cancel, send);

  box.append(head, body, foot);
  dlg.append(box);
  m.append(dlg);

  send.onclick = async () => {
    const want = pri.value.trim();
    const list = tag.value.split(",").map(t => t.trim()).filter(Boolean);
    const had = (s.tags || []).join(", ");
    const callIt = name_.value.trim();
    const wasCalled = s.renamed ? String(s.title || "").trim() : "";
    if (want === String(s.priority || "").trim() && list.join(", ") === had &&
        callIt === wasCalled) {
      const inst = bootstrap.Modal.getInstance(m);
      if (inst) inst.hide(); else m.remove();
      return;
    }
    errBox.classList.add("d-none");
    send.disabled = cancel.disabled = true;
    const spin = el("span", "spinner-border spinner-border-sm me-2");
    spin.setAttribute("aria-hidden", "true");
    send.textContent = "";
    send.append(spin, document.createTextNode("Filing…"));

    const by = who.value.trim();
    /* "-" is how each field says *cleared*, out loud, rather than by being absent — the
       same convention `assigned` uses, and the reason a follow-up that says nothing about
       a rank leaves it alone. */
    const r = await snagPost(snags, {
      follow_up_to: s.when, by,
      priority: want || (String(s.priority || "").trim() ? "-" : ""),
      tags: list.length ? list.join(", ") : (had ? "-" : ""),
      title: callIt !== wasCalled ? callIt : "",
    });

    if (r && r.ok) {
      tasksRemember(by);
      tasksRememberTags(list);
      state.index = null;
      tasksFlash = "Saved as a follow-up in the boat's file.";
      const inst = bootstrap.Modal.getInstance(m);
      if (inst) inst.hide(); else m.remove();
      tasksDetail("snag", s.when);
      return;
    }

    send.disabled = cancel.disabled = false;
    send.textContent = "Save";
    errBox.textContent = (r && r.error) || "the snag service did not say what went wrong";
    errBox.classList.remove("d-none");
  };

  document.body.append(m);
  m.addEventListener("hidden.bs.modal", () => m.remove());
  new bootstrap.Modal(m).show();
}

function tasksRankControl(s, snags) {
  const b = el("button", "btn btn-outline-secondary btn-sm", "Rename, rank, tag");
  b.type = "button";
  b.onclick = () => tasksRankModal(s, snags);
  return b;
}

/* Who works on this boat, derived rather than maintained.
   
   A contact list somebody has to keep up to date is a contact list that is wrong by the
   second season. Every name that matters is already written in `SNAGS.md` — whoever filed
   a fault and whoever it was handed to — so the list is read out of the work itself, and
   the only names in it are names that have actually done something on this boat.

   Ordered by how recently each was used, so the three people currently around the boat sit
   at the top rather than somebody who painted the bilge in 2019. Names typed in this
   browser and the person signed in are folded in too: the first is how a new name survives
   until it reaches the file, the second is so handing yourself a job needs no typing. */
function tasksPeople(snags) {
  const seen = new Map();                      // lower-cased name → { name, when }
  const meet = (raw, when) => {
    const name = String(raw || "").trim();
    if (!name || name === "-") return;
    const key = name.toLowerCase();
    const had = seen.get(key);
    if (!had || String(when || "") > had.when) seen.set(key, { name, when: String(when || "") });
  };
  ((snags && snags.snags) || []).forEach(e => {
    meet(e.assigned, e.when);
    meet(e.by, e.when);
    (e.updates || []).forEach(u => { meet(u.assigned, u.when); meet(u.by, u.when); });
  });
  tasksAssignees().forEach(n => meet(n, ""));
  const me = (window.OB_USER || {}).name || "";
  if (me) meet(me, "");
  return [...seen.values()].sort((a, b) => (b.when || "").localeCompare(a.when || "") ||
                                           a.name.localeCompare(b.name))
                           .map(x => x.name);
}

function tasksAssignControl(s, snags) {
  const b = el("button", "btn btn-outline-secondary btn-sm",
               s.assigned ? "Reassign" : "Assign");
  b.type = "button";
  b.onclick = () => tasksAssignModal(s, snags);
  return b;
}

/* ── filing a fault from here ─────────────────────────────────────────────────────
   The same write the phone page makes, from inside the console: a note, where on the
   boat, photographs shrunk in the browser, and a name. It goes through `snagPost()`, so
   behind the gate the boat and the name are the session's, not the form's.            */

/* A phone photo is ~4 MB and boat wifi is bad; 1600 px on the long edge still shows a
   cracked fitting clearly, and it is the difference between filing from the end of a
   pontoon and spinning forever. */
function tasksShrink(file, max = 1600, quality = 0.82) {
  return new Promise((resolve, reject) => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", quality));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("cannot read " + file.name)); };
    img.src = url;
  });
}

const TASKS_MAX_PHOTOS = 8;

function tasksNewSnagModal(snags) {
  if (!snags || snags.error) {
    mount(note("error", "The snag service could not be read, so nothing can be filed: " +
                        ((snags && snags.error) || "no answer") + "."));
    return;
  }
  const photos = [];
  const m = el("div", "modal fade");
  m.tabIndex = -1;
  const dlg = el("div", "modal-dialog modal-dialog-scrollable modal-fullscreen-sm-down");
  const box = el("div", "modal-content");

  const head = el("div", "modal-header");
  head.append(el("h2", "modal-title h5 mb-0", "File a snag"));
  const x = el("button", "btn-close");
  x.type = "button";
  x.setAttribute("data-bs-dismiss", "modal");
  x.setAttribute("aria-label", "Close");
  head.append(x);

  const body = el("div", "modal-body");
  body.append(el("p", "small text-body-secondary",
    "What you saw, where, and a photograph if you have one. It is appended to the boat's " +
    "file as an open fault with your name and the time on it; nothing already written changes."));
  const errBox = el("div", "alert alert-danger d-none");
  errBox.setAttribute("role", "alert");
  body.append(errBox);

  const id = "t-new-" + Date.now();
  const field = (label, ctrl, help) => {
    const w = el("div", "mb-3");
    const l = el("label", "form-label", label);
    l.htmlFor = ctrl.id;
    w.append(l, ctrl);
    if (help) w.append(el("div", "form-text", help));
    return w;
  };

  const noteEl = el("textarea", "form-control form-control-lg");
  noteEl.id = id + "-note"; noteEl.rows = 4; noteEl.required = true;
  noteEl.placeholder = "what is wrong, as you would say it to the person fixing it";
  body.append(field("What you found", noteEl, "The first sentence becomes the fault's title."));

  const whereEl = el("input", "form-control");
  whereEl.id = id + "-where"; whereEl.type = "text"; whereEl.autocomplete = "off";
  whereEl.placeholder = "engine bay, port locker, transom…";
  body.append(field("Where on the boat", whereEl));

  /* Photographs: the picker is a hidden file input so the visible control can be one
     large button, and `capture` asks a phone for the camera rather than the gallery. */
  const shots = el("div", "d-flex flex-wrap gap-2 mb-2");
  const pick = el("input");
  pick.type = "file"; pick.accept = "image/*"; pick.multiple = true; pick.hidden = true;
  pick.setAttribute("capture", "environment");
  pick.id = id + "-pick";
  const pickBtn = el("button", "btn btn-outline-primary w-100");
  pickBtn.type = "button";
  pickBtn.innerHTML = '<i class="bi bi-camera me-2"></i>Add a photograph';
  const drawShots = () => {
    shots.textContent = "";
    photos.forEach((src, i) => {
      const fig = el("div", "position-relative");
      const img = el("img", "rounded border");
      img.src = src; img.alt = ""; img.style.width = "96px"; img.style.height = "96px";
      img.style.objectFit = "cover";
      const del = el("button", "btn btn-sm btn-light border position-absolute top-0 end-0 m-1 py-0 px-1");
      del.type = "button"; del.textContent = "×"; del.setAttribute("aria-label", "Remove photo");
      del.onclick = () => { photos.splice(i, 1); drawShots(); };
      fig.append(img, del);
      shots.append(fig);
    });
    pickBtn.disabled = photos.length >= TASKS_MAX_PHOTOS;
    pickBtn.innerHTML = '<i class="bi bi-camera me-2"></i>' +
      (photos.length ? `Add another (${photos.length} of ${TASKS_MAX_PHOTOS})` : "Add a photograph");
  };
  pickBtn.onclick = () => pick.click();
  pick.onchange = async e => {
    errBox.classList.add("d-none");
    for (const f of Array.from(e.target.files || [])) {
      if (photos.length >= TASKS_MAX_PHOTOS) break;
      try { photos.push(await tasksShrink(f)); }
      catch (err) { errBox.textContent = String(err.message || err); errBox.classList.remove("d-none"); }
    }
    pick.value = "";
    drawShots();
  };
  const photoWrap = el("div", "mb-3");
  photoWrap.append(el("label", "form-label", "Photographs"), shots, pick, pickBtn,
                   el("div", "form-text", "Shrunk in the browser before sending. Up to eight."));
  body.append(photoWrap);

  const byEl = el("input", "form-control");
  byEl.id = id + "-by"; byEl.type = "text"; byEl.autocomplete = "name";
  byEl.value = (window.OB_USER && window.OB_USER.name) || tasksWho();
  if (window.OB_USER) byEl.readOnly = true;
  body.append(field("Your name", byEl, window.OB_USER ? "From your login." : ""));

  const foot = el("div", "modal-footer");
  const cancel = el("button", "btn btn-outline-secondary", "Cancel");
  cancel.type = "button"; cancel.setAttribute("data-bs-dismiss", "modal");
  const send = el("button", "btn btn-primary", "File it");
  send.type = "button";
  foot.append(cancel, send);
  box.append(head, body, foot);
  dlg.append(box);
  m.append(dlg);

  const fail = line => { errBox.textContent = line; errBox.classList.remove("d-none"); };
  send.onclick = async () => {
    const text = noteEl.value.trim();
    if (!text) { noteEl.classList.add("is-invalid"); noteEl.focus(); return; }
    noteEl.classList.remove("is-invalid");
    errBox.classList.add("d-none");
    send.disabled = cancel.disabled = true;
    send.textContent = "Filing…";
    const mine = byEl.value.trim();
    const r = await snagPost(snags, { note: text, where: whereEl.value.trim(), photos, by: mine });
    if (r && r.ok) {
      tasksRemember(mine);
      state.index = null;
      tasksFlash = `Filed: ${tasksFirst(text, 80)}` +
                   (photos.length ? ` (${photos.length} photo${photos.length > 1 ? "s" : ""})` : "");
      const inst = bootstrap.Modal.getInstance(m);
      if (inst) inst.hide(); else m.remove();
      nav("tasks");
      return;
    }
    send.disabled = cancel.disabled = false;
    send.textContent = "File it";
    fail((r && r.error) || "the snag service did not say what went wrong");
  };

  document.body.append(m);
  m.addEventListener("hidden.bs.modal", () => {
    m.remove();
    /* Closing the form leaves `#tasks/new` behind, and a reload would reopen it. */
    if (state.view === "tasks" && state.sub === "new") nav("tasks");
  });
  m.addEventListener("shown.bs.modal", () => noteEl.focus());
  new bootstrap.Modal(m).show();
}

function tasksAssignModal(s, snags, after) {
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

  const uid = "t-assign-" + (s.when || "x").replace(/\W+/g, "");
  const people = tasksPeople(snags);

  /* Pick a person, or say it is somebody new. A free-text box on its own was quietly
     lossy: "Batuhan", "batuhan" and "Batuhan " are three people to a list that groups by
     name, and nobody notices until the count of who owes what is wrong. */
  const whoWrap = el("div", "mb-3");
  const whoLabel = el("label", "form-label", "Doing it");
  const pick = el("select", "form-select");
  pick.id = uid;
  whoLabel.htmlFor = pick.id;
  const NEW = "\u0000new";
  [["", "Nobody — leave it unassigned"]].concat(people.map(n => [n, n]))
    .concat([[NEW, "Somebody else…"]])
    .forEach(([v, t]) => { const o = el("option", "", t); o.value = v; pick.append(o); });
  pick.value = people.includes(held) ? held : (held ? NEW : "");

  /* Only in the way when it is needed. */
  const fresh = el("input", "form-control mt-2");
  fresh.type = "text";
  fresh.placeholder = "their name";
  fresh.autocomplete = "off";
  fresh.value = people.includes(held) ? "" : held;
  fresh.hidden = pick.value !== NEW;
  fresh.setAttribute("aria-label", "Name of somebody not on the list");
  pick.onchange = () => {
    fresh.hidden = pick.value !== NEW;
    if (!fresh.hidden) fresh.focus();
  };
  const chosen = () => (pick.value === NEW ? fresh.value : pick.value).trim();

  whoWrap.append(whoLabel, pick, fresh);
  /* The list is who has worked on this boat, which is not the same as who can log in to
     read it. Say where the second one is done rather than implying this does it. */
  const hint = el("div", "form-text");
  hint.append(document.createTextNode("Everybody who has filed or been handed a fault on " +
                                      "this boat. A name here is not a login — give " +
                                      "somebody an account on the "));
  const acct = el("a", "", "Account page");
  acct.href = "#account";
  hint.append(acct, document.createTextNode("."));
  whoWrap.append(hint);
  body.append(whoWrap);

  const noteWrap = el("div", "mb-3");
  const noteLabel = el("label", "form-label", "Note");
  const note_ = el("textarea", "form-control");
  note_.rows = 3;
  note_.placeholder = "what they are meant to do — optional";
  note_.id = uid + "-note";
  noteLabel.htmlFor = note_.id;
  noteWrap.append(noteLabel, note_,
    el("div", "form-text", "Optional. The name on its own is a whole entry."));
  body.append(noteWrap);

  const byWrap = el("div");
  const byLabel = el("label", "form-label", "Your name");
  const by = el("input", "form-control");
  by.type = "text";
  by.id = uid + "-by";
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
    const box_ = pick.value === NEW ? fresh : pick;
    if (to !== "-" && !name) { box_.classList.add("is-invalid"); box_.focus(); return; }
    pick.classList.remove("is-invalid");
    fresh.classList.remove("is-invalid");
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
      /* Back where it was opened from. Assigning from the list and being thrown onto the
         fault's page loses the reader's place in a list they were halfway down. */
      if (after) after(); else tasksDetail("snag", s.when);
      return;
    }
    send.disabled = back.disabled = cancel.disabled = false;
    fail((r && r.error) || "the snag service did not say what went wrong");
  };

  send.onclick = () => file(chosen(), `Assigned to ${chosen()}.`);
  back.onclick = () => file("-", "Handed back — this fault is on nobody.");

  document.body.append(m);
  m.addEventListener("hidden.bs.modal", () => m.remove());
  m.addEventListener("shown.bs.modal", () => (fresh.hidden ? pick : fresh).focus());
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

function tasksSnagPage(s, snags, docs, kits) {
  const index = tasksLinkIndex(docs);
  const title = tasksTitle(s);
  const v = tasksPage(tasksHeadline(title));

  if (tasksFlash) { v.append(note("ok", tasksFlash)); tasksFlash = null; }

  /* The whole status line, not just the word the badge carries: somebody wrote "fixed —
     replaced the striker plate" and the half after the dash is the part worth reading. */
  const full = String(s.status || "").trim();
  const h1 = el("span", "text-break");
  if (s.code) h1.append(el("span", "t-n", s.code + " "));
  h1.append(document.createTextNode(tasksHeadline(title)));
  const actions = [tasksSnagBadge(s.status, s.open), tasksStatusControl(s, snags)];
  if (s.open) actions.push(tasksAssignControl(s, snags), tasksRankControl(s, snags));
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

  /* What it will take to put right, when somebody has worked that out. A fault and the
     parts that answer it are the same thought, and separating them is how a boat ends up
     with a list of things that are wrong and a separate list of things to buy that nobody
     can join back together. */
  const kit = ((kits && kits.kits) || []).find(k => k.snag && k.snag === s.when);
  if (kit) v.append(tasksKitCue(kit, kits)); else cartSuggest(null, null);

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

function tasksServicePage(m, maint, docs, kits) {
  const index = tasksLinkIndex(docs);
  const v = tasksPage(tasksHeadline(m.description || m.item));
  const r = { kind: "service", verdict: m.verdict };

  v.append(pageHead(el("span", "text-break", tasksHeadline(m.description || m.item)), [
    m.item,
    m.last ? `last done ${m.last}` : "never recorded",
    m.last ? ago(m.last) : null,
    m.per_outing ? "counted per salt-water outing" : null,
  ], [tasksBadge(r)]));

  const why = pane("Why it is owed", "REASON");
  why.append(cardBody(tasksProse(m.why, index)));
  v.append(why);

  /* What the service consists of. An annual engine service is a dozen jobs, and the person
     with the spanner needs them as a list they can work down — not as a sentence with
     semicolons in it. Ticks are for reading, not for saving: this page writes nothing, and
     a service is recorded at a keyboard by somebody who knows the work happened. */
  const steps = (m.steps || []).filter(Boolean);
  if (steps.length) {
    const p = pane("What it consists of", `${steps.length} STEPS`);
    const list = el("ul", "list-group list-group-flush");
    steps.forEach(t => {
      const li = el("li", "list-group-item d-flex gap-2 align-items-baseline");
      li.append(el("i", "bi bi-square text-body-secondary"));
      li.append(el("span", "text-break", t));
      list.append(li);
    });
    p.append(list);
    v.append(p);
  }

  /* The parts, between what the job consists of and the counters behind it — which is the
     order the question actually gets asked in: what is it, what does it involve, what do I
     need to have in the box. A boat with no kits file simply has no pane here, which is the
     normal state on day one and not worth a message. */
  const kit = ((kits && kits.kits) || []).find(k => k.item === m.item);
  if (kit) v.append(tasksKitCue(kit, kits)); else cartSuggest(null, null);

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
