/* OpenBoat console — the documents view. Loaded by console.html after the shared
   furniture; it relies on the globals defined there (state, el, api, href, nav, pane…).
   No boat facts live here: everything on screen arrives from the API at run time.

   Three pages, one address each:

       #docs                    the shelf
       #docs?q=impeller         a search across every passage
       #docs/pump-7j.md         the reader
       #docs/pump-7j.md/L212    the reader, scrolled to a line and holding it lit

   The reader is the point of the whole console. A citation that cannot be opened is a
   citation you have to trust, so every hit, every heading and every page number on these
   pages is a real link with a real address: middle-click works, back works, and a URL
   pasted into a message opens on the same line for whoever reads it.

   Two things here exist for a 150-page manual rather than for a note. Extracted documents
   are rendered one page at a time as the pages come into view, because building seven
   thousand lines up front is what made an operator's manual take the best part of a
   minute to open. And a document a person typed is rendered as markdown rather than
   dumped as text — but with the source line beside every block, because the line number
   is half of the citation and a document you cannot cite is a document you have to
   believe.                                                                            */

/* ── what this view remembers between renders ────────────────────────────────────────
   Not boat data — a manifest already fetched, the text of the last few documents opened,
   and where the reader currently is. `state.docs` is shared with the overview, so the
   manifest is read and written there rather than kept twice. */
const DOCS = {
  text: new Map(),          // document name → the /api/doc payload, last few only
  token: 0,                 // guards against a slow fetch landing after you have moved on
  timer: null,              // the search debounce
  caret: null,              // where the cursor was in the search box before a re-render
  mounted: null,            // the reader on screen: { name, root, goto, step, match }
  keys: false,              // the reader's keyboard handler is installed once
  moved: false,             // has anything navigated in this tab yet (Esc: back or up?)
  pdf: false,               // "show the original beside it" — a UI preference, not data
  io: null,                 // the lazy-page observer of the reader currently mounted
  hereIo: null,             // and the one that says which page you are looking at
};

try { DOCS.pdf = localStorage.getItem("openboat.console.pdf") === "1"; } catch (e) { }
window.addEventListener("hashchange", () => { DOCS.moved = true; });

/* Every fetch on this page has a loading, an empty and an error rendering. `api()` throws
   when the server is not there at all, which on a boat is a Tuesday rather than a fault. */
async function docsApi(path) {
  try { return (await api(path)) || { error: "the server sent nothing" }; }
  catch (e) { return { error: "the server is not answering" }; }
}

const DOCS_STOP = ["amp", "lt", "gt", "quot"];
const docsTerms = q => String(q || "").toLowerCase().split(/[^\p{L}\p{N}]+/u)
  .filter(w => w.length > 1 && !DOCS_STOP.includes(w));
const docsRx = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function docsNote(kind, text) {
  const e = el("div", "empty");
  e.append(statusCell(kind, text));
  return e;
}

/* ── the view picks its page off the address ─────────────────────────────────────── */
async function viewDocs() {
  if (state.sub) return docsReader(state.sub);
  DOCS.mounted = null;
  if (DOCS.io) { DOCS.io.disconnect(); DOCS.io = null; }
  if (DOCS.hereIo) { DOCS.hereIo.disconnect(); DOCS.hereIo = null; }
  const q = (state.params.q || "").trim();
  return q ? docsSearch(q) : docsShelf();
}

/* The search box writes `?q=` into the hash rather than filtering in place, so a search
   is a page with an address and the back button returns you to it. */
function docsFrame() {
  state.q = state.params.q || "";
  const v = el("div", "view docs-list");
  const tb = toolbar("Search this boat's papers — a symptom, a part, a word off a label…",
    val => {
      clearTimeout(DOCS.timer);
      const box = tb.querySelector("input");
      DOCS.timer = setTimeout(() => {
        const q = val.trim();
        DOCS.caret = box ? box.selectionStart : null;
        nav("docs", null, q ? { q } : undefined);
      }, 260);
    });
  const scroll = el("div", "docs-scroll");
  v.append(tb, scroll);
  const box = tb.querySelector("input");
  if (DOCS.caret !== null && box) {
    const at = DOCS.caret; DOCS.caret = null;
    setTimeout(() => { try { box.focus(); box.setSelectionRange(at, at); } catch (e) { } }, 40);
  }
  return { v, scroll };
}

async function docsManifest() {
  if (state.docs && state.docs.documents) return state.docs;
  const d = await docsApi("/api/docs");
  if (d.documents) state.docs = d;
  return d;
}

const docsMetaOf = name =>
  ((state.docs || {}).documents || []).find(d => d.name === name) || null;

function docsStatus(r) {
  if (!r.exists) return statusCell("bad", "named but not on disk");
  if (r.gaps) return statusCell("warn", `${nf(r.gaps)} of ${nf(r.pages)} pages need OCR`);
  if (r.pages) return statusCell("ok", `${nf(r.pages_with_text)}/${nf(r.pages)} pages read`);
  return statusCell("ok", "readable");
}

/* ── page one: the shelf ─────────────────────────────────────────────────────────── */
async function docsShelf() {
  const { v, scroll } = docsFrame();
  scroll.append(el("div", "empty", "Reading the shelf…"));
  mount(v);

  const t = ++DOCS.token;
  const d = await docsManifest();
  if (t !== DOCS.token || state.view !== "docs" || state.sub) return;
  scroll.textContent = "";

  if (d.error) { scroll.append(docsNote("bad", d.error)); return; }
  const rows = d.documents || [];
  if (!rows.length) {
    scroll.append(docsNote("warn", "The profile names no documents, so nothing can be " +
                                  "answered from paper yet."));
    return;
  }

  const shelf = el("div", "shelf");
  const head = el("div", "row hd");
  [["DOCUMENT", "doc"], ["PASSAGES", "num pass"], ["SIZE", "num size"],
   ["CHANGED", "num mod"], ["STATUS", "st"]]
    .forEach(([label, cls]) => head.append(el("div", "c " + cls, label)));
  shelf.append(head);

  /* Written first, extracted after. Somebody's own notes about their own boat outrank a
     manufacturer's PDF on the question of what is actually fitted, and inside each group
     the documents that are not on disk come first, because that is the row that changes
     what you should believe about an answer. */
  const groups = [
    ["TYPED BY HAND", rows.filter(r => !r.derived_from)],
    ["EXTRACTED FROM A PDF", rows.filter(r => r.derived_from)],
  ];
  for (const [label, set] of groups) {
    if (!set.length) continue;
    shelf.append(el("div", "grp", `${label} · ${nf(set.length)}`));
    set.sort((a, b) => (a.exists - b.exists) ||
                       String(a.title || a.name).localeCompare(String(b.title || b.name)));
    set.forEach(r => shelf.append(docsShelfRow(r)));
  }
  scroll.append(shelf);

  if (d.missing) {
    scroll.append(docsNote("bad",
      `${nf(d.missing)} document${d.missing > 1 ? "s are" : " is"} named in the profile and ` +
      `not on disk. Nothing can be answered from ${d.missing > 1 ? "them" : "it"}.`));
  }
  const foot = el("div", "empty");
  foot.innerHTML =
    `<b>${nf(d.count || 0)}</b> document(s), <b>${nf(d.passages || 0)}</b> retrievable ` +
    `passages. Search above to ask the papers a question — every answer arrives with the ` +
    `file and the line it came out of.` +
    (d.gaps ? ` <b>${nf(d.gaps)}</b> page(s) are scanned images with no text layer; they ` +
              `are listed, not hidden.` : "");
  scroll.append(foot);
}

function docsShelfRow(r) {
  const a = el("a", "row");
  a.href = href("docs", r.name);
  const c = el("div", "c doc");
  c.append(el("span", "t", r.title || r.name));
  c.append(el("span", "s", r.derived_from ? `${r.name}  ·  from ${r.derived_from}` : r.name));
  a.append(c);
  a.append(el("div", "c num pass", r.exists ? nf(r.passages) : "—"));
  a.append(el("div", "c num size", r.exists ? kb(r.bytes) : "—"));
  a.append(el("div", "c num mod", r.modified ? ago(r.modified) : "—"));
  const st = el("div", "c st");
  st.append(docsStatus(r));
  a.append(st);
  return a;
}

/* ── page two: a search ──────────────────────────────────────────────────────────── */
async function docsSearch(q) {
  const { v, scroll } = docsFrame();
  scroll.append(el("div", "empty", "Reading the papers…"));
  mount(v);

  const t = ++DOCS.token;
  const [r] = await Promise.all([
    docsApi("/api/ask?q=" + encodeURIComponent(q) + "&limit=40"),
    docsManifest(),
  ]);
  if (t !== DOCS.token || state.view !== "docs" || state.sub) return;
  scroll.textContent = "";

  if (r.error) { scroll.append(docsNote("bad", r.error)); return; }
  const hits = r.passages || [];
  if (!hits.length) {
    const e = el("div", "empty");
    e.innerHTML =
      `Nothing in ${nf(r.documents || 0)} document(s) matches <b>${esc(q)}</b>.<br><br>` +
      `That is an answer, not a failure — this boat's library does not contain it. Ask in ` +
      `either language; the query is widened both ways.`;
    scroll.append(e);
    return;
  }

  const terms = docsTerms(q);
  const order = [], byDoc = new Map();
  hits.forEach((h, i) => {
    if (!byDoc.has(h.doc)) { byDoc.set(h.doc, []); order.push(h.doc); }
    byDoc.get(h.doc).push({ h, rank: i + 1 });
  });

  const wrap = el("div", "hits");
  const lead = el("div", "empty");
  lead.innerHTML =
    `<b>${nf(hits.length)}</b> passage(s) in <b>${nf(order.length)}</b> document(s) for ` +
    `<b>${esc(q)}</b>, best first. Each one opens the file on the line it came from.`;
  wrap.append(lead);

  for (const name of order) {
    const meta = docsMetaOf(name) || {};
    const g = el("div", "hitgrp");
    const gh = el("div", "gh");
    const a = el("a", "", meta.title || name);
    a.href = href("docs", name);
    gh.append(a);
    const set = byDoc.get(name);
    gh.append(el("span", "s",
      `${nf(set.length)} hit${set.length > 1 ? "s" : ""} · ${name}`));
    g.append(gh);
    set.forEach(({ h, rank }) => g.append(docsHitRow(h, rank, terms, meta.title || "")));
    wrap.append(g);
  }
  scroll.append(wrap);
}

function docsHitRow(h, rank, terms, title) {
  const a = el("a", "hit");
  a.href = href("docs", h.doc + "/L" + h.line);
  const top = el("div", "top");
  let head = h.heading || "(no heading)";
  if (title && head.length > title.length && head.slice(0, title.length) === title) {
    head = head.slice(title.length).replace(/^\s*[—–-]\s*/, "") || head;
  }
  top.append(el("span", "h", head));
  top.append(el("span", "l", "line " + nf(h.line)));
  top.append(el("span", "r", `#${rank} · ${Number(h.score).toFixed(2)}`));
  a.append(top);
  const tx = el("div", "tx");
  tx.innerHTML = docsHighlight(docsSnippet(h.text, terms, 420), terms);
  a.append(tx);
  return a;
}

/* A window of the passage around the first term that matched, snapped to word boundaries.
   The whole passage is one click away; what belongs on a result row is the sentence. */
function docsSnippet(text, terms, width) {
  const t = String(text || "").replace(/[ \t]+/g, " ").replace(/\n{2,}/g, "\n").trim();
  if (t.length <= width) return t;
  const low = t.toLowerCase();
  let at = -1;
  for (const w of terms) { const i = low.indexOf(w); if (i >= 0 && (at < 0 || i < at)) at = i; }
  if (at < 0) at = 0;
  let from = Math.max(0, at - Math.floor(width * 0.3));
  if (from > 0) { const sp = t.indexOf(" ", from); if (sp > 0 && sp - from < 26) from = sp + 1; }
  let to = Math.min(t.length, from + width);
  if (to < t.length) { const sp = t.lastIndexOf(" ", to); if (sp > from + width * 0.6) to = sp; }
  return (from > 0 ? "… " : "") + t.slice(from, to).trim() + (to < t.length ? " …" : "");
}

function docsHighlight(s, terms) {
  const h = esc(s);
  if (!terms.length) return h;
  const rx = new RegExp("(" + terms.map(docsRx).join("|") + ")", "gi");
  return h.replace(rx, '<mark class="qmark">$1</mark>');
}

/* ── page three: the reader ──────────────────────────────────────────────────────── */
async function docsReader(sub) {
  const m = String(sub).match(/^(.*)\/L(\d+)$/);
  const name = m ? m[1] : sub;
  const line = m ? Number(m[2]) : null;

  // Already open: a new line is a scroll, not a rebuild. This is what makes stepping
  // through search hits in one manual feel like reading rather than like loading.
  const on = DOCS.mounted;
  if (on && on.name === name && document.contains(on.root)) { on.goto(line, true); return; }

  const t = ++DOCS.token;
  const v = el("div", "view docs-page-view");
  const scroll = el("div", "docs-scroll docs-reader");
  scroll.append(el("div", "empty", "Opening…"));
  v.append(scroll);
  mount(v);

  const cached = DOCS.text.get(name);
  const [doc] = await Promise.all([
    cached ? Promise.resolve(cached)
           : docsApi("/api/doc?name=" + encodeURIComponent(name)),
    docsManifest(),
  ]);
  if (t !== DOCS.token || state.view !== "docs" || state.sub !== sub) return;
  if (!doc.error && doc.exists) {
    DOCS.text.set(name, doc);
    for (const k of [...DOCS.text.keys()].slice(0, -3)) DOCS.text.delete(k);
  }

  const meta = doc.document || docsMetaOf(name) || { name };
  scroll.textContent = "";
  scroll.append(docsCrumb(meta.title || name));

  if (doc.error) return docsUnreadable(scroll, name, meta, doc.error);
  if (!doc.exists || !doc.text) return docsMissing(scroll, name, meta, doc);
  docsBuild(scroll, v, name, meta, String(doc.text).split("\n"), line);
}

function docsCrumb(title) {
  const c = el("div", "docs-crumb");
  const a = el("a", "", "Documents");
  a.href = href("docs");
  c.append(a, el("span", "", "›"), el("span", "now", title));
  return c;
}

/* Not on disk and not answered are opposite facts and used to render as the same page.
   "Named in the profile, and not on disk" is a claim about this boat's shelf; a request
   that failed supports no claim about the shelf at all, only about the connection. */
function docsUnreadable(scroll, name, meta, why) {
  scroll.append(el("h1", "docs-title", meta.title || name));
  const p = pane(name, "NOT ANSWERED");
  p.classList.add("full");
  const b = el("div", "empty");
  b.append(statusCell("bad", why));
  const t = el("div");
  t.textContent =
    " The document was not read, so nothing here says whether it is on disk or what is " +
    "in it. Try again when the server is answering.";
  b.append(t);
  p.append(b);
  scroll.append(p);
}

/* The most useful page on the whole console, and the one a tidier library would drop. */
function docsMissing(scroll, name, meta, doc) {
  scroll.append(el("h1", "docs-title", meta.title || name));
  const p = pane(name, "NOT ON DISK");
  p.classList.add("full");
  const b = el("div", "empty");
  b.append(statusCell("bad", "named in the profile, and not on disk"));
  const t = el("div");
  t.innerHTML =
    `<br>The profile names this document, so the library counts it — and nothing can be ` +
    `answered from it. A question whose answer is in here will come back saying it is not ` +
    `in the papers, which is honest and wrong.<br><br>` +
    `<b>Path</b><br>${esc(meta.path || doc.path || "not recorded")}<br><br>` +
    `Put the file there, or take the line out of the profile. Either is better than a ` +
    `shelf that looks complete.`;
  b.append(t);
  p.append(b);
  scroll.append(p);
}

/* ── the reader proper ───────────────────────────────────────────────────────────── */
function docsBuild(scroll, view, name, meta, lines, line) {
  const extracted = !!meta.derived_from;
  const pages = extracted ? docsPagesOf(lines) : [];
  const heads = extracted ? [] : docsHeadings(lines);

  /* header: what this is, and how much of it can be read */
  const h = el("div", "docs-h");
  h.append(el("h1", "docs-title", meta.title || name));
  const acts = el("div", "docs-acts");
  h.append(acts);
  scroll.append(h);
  scroll.append(docsMetaStrip(name, meta, lines.length, pages));

  const cols = el("div", "docs-cols");
  const rail = el("aside", "docs-rail");
  const body = el("div", "docs-body");
  const pdfCol = el("div", "docs-pdf");
  cols.append(rail, body, pdfCol);
  scroll.append(cols);

  /* the paper itself, beside its reading */
  let frame = null, applyPdf = null, here = pages.length ? pages[0].n : 1;
  const wide = () => window.matchMedia("(min-width:1280px)").matches;
  const paperUrl = pn => `/paper?name=${encodeURIComponent(name)}` + (pn ? `#page=${pn}` : "");
  if (meta.original) {
    const open = el("a", "docs-btn", "Original PDF ▸");
    open.href = paperUrl(null); open.target = "_blank"; open.rel = "noopener";
    acts.append(open);

    const tog = el("button", "docs-btn", "Show original");
    tog.setAttribute("aria-pressed", "false");
    tog.onclick = () => {
      if (!wide()) { window.open(paperUrl(here), "_blank", "noopener"); return; }
      DOCS.pdf = !DOCS.pdf;
      try { localStorage.setItem("openboat.console.pdf", DOCS.pdf ? "1" : "0"); } catch (e) { }
      applyPdf();
      // Opening the pane narrows the body, so every line rewraps and the pages that have
      // not been built yet are suddenly the wrong height. Go back to where the reader was
      // rather than letting the document slide out from under them.
      const at = anchors[curIdx];
      if (at) goto(extracted ? pages[curIdx].head : Number(at.dataset.line), true);
    };
    acts.append(tog);

    applyPdf = () => {
      const on = DOCS.pdf && wide();
      cols.classList.toggle("pdf", on);
      tog.setAttribute("aria-pressed", String(on));
      if (on && !frame) {
        frame = el("iframe");
        frame.title = "the original paper";
        frame.src = paperUrl(here);
        frame.dataset.page = String(here);
        pdfCol.append(frame);
      }
    };
    applyPdf();
    DOCS.applyPdf = applyPdf;
    if (!DOCS.mq) {
      DOCS.mq = window.matchMedia("(min-width:1280px)");
      DOCS.mq.addEventListener("change", () => { if (DOCS.applyPdf) DOCS.applyPdf(); });
    }
  }

  let pdfTimer = null;
  function syncPdf(pn) {
    if (!frame || !DOCS.pdf || String(pn) === frame.dataset.page) return;
    frame.dataset.page = String(pn);
    clearTimeout(pdfTimer);
    pdfTimer = setTimeout(() => {
      const u = paperUrl(pn);
      try { frame.contentWindow.location.replace(u); } catch (e) { frame.src = u; }
    }, 380);
  }

  /* ── the body ────────────────────────────────────────────────────────────────── */
  const md = el("div", "md");
  let blocks = [];
  if (extracted) {
    if (pages.length && pages[0].head > 1) {
      blocks = docsMarkdown(lines.slice(0, pages[0].head - 1));
      blocks.forEach(b => md.append(b));
      body.append(md);
    }
    pages.forEach((p, i) => { p.el = docsPageShell(p, name, meta, pages.length); body.append(p.el); });
  } else {
    blocks = docsMarkdown(lines);
    blocks.forEach(b => md.append(b));
    body.append(md);
  }

  /* Pages are built as they come into view. `px` is a guess at a line's height, replaced
     with a measurement as soon as one real page exists, so the scrollbar stops lying. */
  let px = 21, calibrated = 0, findQ = "";
  const pending = () => pages.filter(p => p.el && !p.el.dataset.done);
  const size = p => Math.max(26, (p.to - p.from + 1) * px);
  pages.forEach(p => { p.el.querySelector(".docs-page-b").style.minHeight = size(p) + "px"; });

  function renderPage(p) {
    if (!p.el || p.el.dataset.done) return;
    p.el.dataset.done = "1";
    const b = p.el.querySelector(".docs-page-b");
    b.style.minHeight = "";
    b.textContent = "";
    if (p.gap) b.append(docsGapCard(p, meta));
    if (p.to >= p.from) b.append(docsCode(lines, p.from, p.to));
    if (findQ) docsMark(b, findQ);
    if (calibrated < 4 && !p.gap && p.to - p.from > 8) {
      const measured = b.offsetHeight / (p.to - p.from + 1);
      if (measured > 6 && measured < 120) {
        px = (px * calibrated + measured) / (calibrated + 1);
        calibrated++;
        pending().forEach(q => {
          q.el.querySelector(".docs-page-b").style.minHeight = size(q) + "px";
        });
      }
    }
  }

  if (DOCS.io) DOCS.io.disconnect();
  DOCS.io = null;
  if (pages.length && "IntersectionObserver" in window) {
    DOCS.io = new IntersectionObserver(entries => {
      for (const e of entries) if (e.isIntersecting) {
        const p = pages[Number(e.target.dataset.i)];
        DOCS.io.unobserve(e.target);
        renderPage(p);
      }
    }, { root: scroll, rootMargin: "1400px 0px" });
    pages.forEach(p => DOCS.io.observe(p.el));
  } else {
    pages.forEach(renderPage);
  }

  /* ── which page or heading you are actually looking at ───────────────────────── */
  const anchors = extracted ? pages.map(p => p.el)
                            : blocks.filter(b => b.dataset.head);
  let curIdx = 0, hashTimer = null, lastLine = null;

  function setHere(i) {
    if (i < 0 || i >= anchors.length) return;
    curIdx = i;
    if (extracted) {
      here = pages[i].n;
      pages.forEach((p, k) => p.el.classList.toggle("here", k === i));
      railMark(String(pages[i].n));
      syncPdf(here);
    } else {
      railMark(anchors[i].dataset.line);
    }
    const n = extracted ? pages[i].head : Number(anchors[i].dataset.line);
    if (n === lastLine) return;
    lastLine = n;
    clearTimeout(hashTimer);
    hashTimer = setTimeout(() => {
      if (!document.contains(cols)) return;
      const target = href("docs", name + "/L" + n);
      if (location.hash !== target) history.replaceState(null, "", target);
    }, 420);
  }

  if (DOCS.hereIo) DOCS.hereIo.disconnect();
  DOCS.hereIo = null;
  if (anchors.length && "IntersectionObserver" in window) {
    const seen = new Set();
    DOCS.hereIo = new IntersectionObserver(entries => {
      for (const e of entries) {
        if (e.isIntersecting) seen.add(e.target); else seen.delete(e.target);
      }
      // The page you are reading is the last one to reach the top band, not the first:
      // the tail of the page above is still in it, and answering "96" while somebody
      // reads 97 puts the wrong page in the PDF beside them.
      let best = -1;
      seen.forEach(nd => {
        const i = anchors.indexOf(nd);
        if (i > best) best = i;
      });
      if (best >= 0) setHere(best);
    }, { root: scroll, rootMargin: "0px 0px -84% 0px" });
    anchors.forEach(nd => DOCS.hereIo.observe(nd));
  }

  /* ── the rail: an outline, and a find that reaches pages not yet built ───────── */
  const railIn = el("div", "docs-rail-in");
  const tog = el("button", "docs-rail-toggle");
  tog.append(el("span", "", extracted ? "Pages and find" : "Outline and find"));
  tog.append(el("span", "sp", "OPEN"));
  tog.onclick = () => {
    const open = rail.classList.toggle("open");
    tog.querySelector(".sp").textContent = open ? "CLOSE" : "OPEN";
  };
  rail.append(tog, railIn);

  const outSec = el("div", "docs-rs");
  outSec.append(el("h3", "", extracted ? "PAGES" : "OUTLINE"));
  const outl = el("div", extracted ? "pgrid" : "outl");
  outSec.append(outl);

  const railLinks = new Map();
  if (extracted) {
    pages.forEach((p, i) => {
      const a = el("a", p.gap ? "gap" : "", String(p.n));
      a.href = href("docs", name + "/L" + p.head);
      a.title = p.gap ? `page ${p.n} — a scanned image with no text layer` : `page ${p.n}`;
      a.onclick = ev => { ev.preventDefault(); goto(p.head, true); };
      outl.append(a);
      railLinks.set(String(p.n), a);
    });
  } else if (heads.length) {
    heads.forEach(hd => {
      const a = el("a", "l" + hd.level, hd.text);
      a.href = href("docs", name + "/L" + hd.line);
      a.title = hd.text;
      a.onclick = ev => { ev.preventDefault(); goto(hd.line, true); };
      outl.append(a);
      railLinks.set(String(hd.line), a);
    });
  } else {
    outl.append(el("div", "cnt", "No headings in this file."));
  }
  function railMark(key) {
    railLinks.forEach((a, k) => a.setAttribute("aria-current", k === key ? "true" : "false"));
    const cur = railLinks.get(key);
    if (cur && cur.parentNode && cur.parentNode.scrollHeight > cur.parentNode.clientHeight) {
      const box = cur.parentNode.getBoundingClientRect(), r = cur.getBoundingClientRect();
      if (r.top < box.top || r.bottom > box.bottom) cur.scrollIntoView({ block: "nearest" });
    }
  }

  const findSec = el("div", "docs-rs docs-find");
  findSec.append(el("h3", "", "FIND IN THIS DOCUMENT"));
  const find = el("input");
  find.type = "search";
  find.placeholder = "a word on the page…";
  findSec.append(find);
  const findCnt = el("div", "cnt", "");
  const findList = el("div", "fmatches");
  findSec.append(findCnt, findList);
  railIn.append(findSec, outSec);

  let matches = [], mIdx = -1, findTimer = null;
  find.oninput = () => { clearTimeout(findTimer); findTimer = setTimeout(runFind, 200); };
  const findStep = d => {
    if (matches.length) goMatch((mIdx < 0 ? (d > 0 ? -1 : 0) : mIdx) + d);
  };
  find.onkeydown = ev => {
    if (ev.key === "Enter") { ev.preventDefault(); findStep(ev.shiftKey ? -1 : 1); }
    else if (ev.key === "ArrowDown") { ev.preventDefault(); findStep(1); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); findStep(-1); }
    else if (ev.key === "Escape") { find.value = ""; runFind(); find.blur(); }
  };

  function runFind() {
    const q = findQ = find.value.trim();
    docsUnmark(body);
    findList.textContent = "";
    matches = []; mIdx = -1;
    if (!q) { findCnt.textContent = ""; return; }
    const low = q.toLowerCase();
    let total = 0;
    for (let i = 0; i < lines.length; i++) {
      if (!lines[i].toLowerCase().includes(low)) continue;
      total++;
      if (matches.length < 300) matches.push({ line: i + 1, text: lines[i].trim() });
    }
    findCnt.textContent = !total ? "No line in this document has it."
      : `${nf(total)} line(s)` + (total > matches.length ? ` — first ${nf(matches.length)}` : "")
        + " · Enter steps, [ and ] too";
    matches.forEach((mt, i) => {
      const a = el("a");
      a.href = href("docs", name + "/L" + mt.line);
      a.title = mt.text;
      a.append(el("span", "n", String(mt.line)));
      a.append(document.createTextNode(docsSnippet(mt.text, [low], 90)));
      a.onclick = ev => { ev.preventDefault(); goMatch(i); };
      findList.append(a);
    });
    docsMark(body, q);
  }

  function goMatch(i) {
    if (!matches.length) return;
    mIdx = ((i % matches.length) + matches.length) % matches.length;
    [...findList.children].forEach((a, k) =>
      a.setAttribute("aria-current", k === mIdx ? "true" : "false"));
    const a = findList.children[mIdx];
    if (a) a.scrollIntoView({ block: "nearest" });
    goto(matches[mIdx].line, true);
  }

  /* ── going to a line ─────────────────────────────────────────────────────────── */
  const pageOfLine = n => pages.find(p => n >= p.head && n <= p.to) || null;

  function lineEl(n) {
    if (extracted) {
      const exact = body.querySelector("#L" + n);
      if (exact) return exact;
      const p = pages.find(x => x.head === n);
      return (p && p.el) || null;
    }
    let best = null;
    for (const b of blocks) {
      const from = Number(b.dataset.line), to = Number(b.dataset.end);
      if (n >= from && n <= to) return b;
      if (from <= n && (!best || from > Number(best.dataset.line))) best = b;
    }
    return best;
  }

  function goto(n, quiet) {
    if (!n) return;
    if (extracted) {
      const p = pageOfLine(n) || pages[0];
      if (p) {
        const i = pages.indexOf(p);
        for (let k = Math.max(0, i - 1); k <= Math.min(pages.length - 1, i + 2); k++)
          renderPage(pages[k]);
        setHere(i);
      }
    }
    requestAnimationFrame(() => {
      const target = lineEl(n);
      if (!target) return;
      body.querySelectorAll(".hit,.inpassage")
          .forEach(x => x.classList.remove("hit", "inpassage"));
      const isPage = target.classList.contains("docs-page");
      target.scrollIntoView({ block: isPage ? "start" : "center" });
      docsSettle(scroll, target, isPage ? 0.05 : 0.36);
      target.classList.add("hit");
      if (extracted) {
        const p = pageOfLine(n);
        if (p && p.el) {
          pages.forEach(x => x.el.classList.remove("here"));
          p.el.classList.add("here");
          if (!isPage) p.el.classList.add("inpassage");
        }
      } else {
        // The passage a line belongs to is the heading-to-heading block around it.
        const from = Number(target.dataset.line);
        let openAt = 0, closeAt = Infinity;
        heads.forEach(hd => {
          if (hd.line <= from) openAt = Math.max(openAt, hd.line);
          else closeAt = Math.min(closeAt, hd.line);
        });
        blocks.forEach(b => {
          const l = Number(b.dataset.line);
          if (l >= openAt && l < closeAt && b !== target) b.classList.add("inpassage");
        });
        const at = anchors.findIndex(a => Number(a.dataset.line) === openAt);
        if (at >= 0) setHere(at);
      }
    });
  }

  /* ── keyboard, installed once and guarded by what is on screen ───────────────── */
  DOCS.mounted = {
    name, root: view, goto,
    step: d => {
      const i = Math.min(anchors.length - 1, Math.max(0, curIdx + d));
      if (!anchors[i]) return;
      goto(extracted ? pages[i].head : Number(anchors[i].dataset.line), true);
      setHere(i);
    },
    match: d => { if (matches.length) goMatch(mIdx < 0 ? (d > 0 ? 0 : -1) : mIdx + d); },
    find: () => { rail.classList.add("open"); find.focus(); find.select(); },
  };
  docsKeys();

  if (line) goto(line, true);
  else setHere(0);
}

function docsKeys() {
  if (DOCS.keys) return;
  DOCS.keys = true;
  document.addEventListener("keydown", ev => {
    const m = DOCS.mounted;
    if (!m || !document.contains(m.root)) return;
    const tag = ((ev.target || {}).tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.key === "j") { ev.preventDefault(); m.step(1); }
    else if (ev.key === "k") { ev.preventDefault(); m.step(-1); }
    else if (ev.key === "]") { ev.preventDefault(); m.match(1); }
    else if (ev.key === "[") { ev.preventDefault(); m.match(-1); }
    else if (ev.key === "/") { ev.preventDefault(); m.find(); }
    else if (ev.key === "Escape") { ev.preventDefault(); DOCS.moved ? history.back() : nav("docs"); }
  });
}

function docsSettle(scroll, target, frac) {
  let tries = 0, stop = false;
  const off = () => { stop = true; };
  scroll.addEventListener("wheel", off, { once: true, passive: true });
  scroll.addEventListener("touchstart", off, { once: true, passive: true });
  const fix = () => {
    if (stop || !document.contains(target)) return;
    const r = target.getBoundingClientRect(), s = scroll.getBoundingClientRect();
    const want = s.top + s.height * frac;
    if (Math.abs(r.top - want) > 4) scroll.scrollTop += r.top - want;
    if (++tries < 9) setTimeout(fix, 90);
  };
  fix();
}

/* ── the meta strip ──────────────────────────────────────────────────────────────── */
function docsMetaStrip(name, meta, lineCount, pages) {
  const s = el("div", "docs-meta");
  const bit = (label, value, cls) => {
    const x = el("span", cls || "");
    x.append(el("i", "", label));
    x.append(document.createTextNode(value));
    s.append(x);
  };
  bit("KIND", meta.derived_from ? `extracted from ${meta.derived_from}` : "typed by hand",
      "kind");
  bit("LINES", nf(meta.lines || lineCount));
  bit("PASSAGES", nf(meta.passages));
  if (meta.pages) bit("PAGES", `${nf(meta.pages_with_text)} of ${nf(meta.pages)} read`);
  else if (pages.length) bit("PAGES", nf(pages.length));
  if (meta.gaps) bit("NEEDS OCR", `${nf(meta.gaps)} page(s)`, "warn");
  if (meta.bytes) bit("SIZE", kb(meta.bytes));
  if (meta.modified) bit("CHANGED", `${ago(meta.modified)}  ·  ${meta.modified}`);
  bit("FILE", name);
  return s;
}

/* ── extracted documents: one section per page ───────────────────────────────────── */

/* `openboat.ingest` writes one `## <name> — page N` heading per page of the PDF, and
   writes a marked paragraph into a page that produced no text at all. Both are read back
   here, so the reader shows the same shape as the paper. */
const DOCS_PAGE_HEAD = /^##\s+(.*?)\s*[—-]\s*page\s+(\d+)\s*$/i;
const DOCS_NO_TEXT = /^\*\*No text layer on this page/;

function docsPagesOf(lines) {
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(DOCS_PAGE_HEAD);
    if (!m) continue;
    out.push({ n: Number(m[2]), head: i + 1, from: i + 2, to: lines.length, gap: false });
    if (out.length > 1) out[out.length - 2].to = i;
  }
  out.forEach((p, i) => {
    p.i = i;
    for (let k = p.from - 1; k < p.to; k++) {
      if (DOCS_NO_TEXT.test(lines[k] || "")) { p.gap = true; break; }
    }
  });
  return out;
}

function docsPageShell(p, name, meta, count) {
  const s = el("section", "docs-page");
  s.dataset.i = String(p.i);
  s.dataset.page = String(p.n);
  s.id = "P" + p.n;
  const h = el("div", "docs-page-h");
  h.append(el("b", "", `page ${nf(p.n)} of ${nf(meta.pages || count)}`));
  if (p.gap) h.append(statusCell("warn", "no text layer"));
  h.append(el("span", "sp"));
  if (meta.original) {
    const a = el("a", "", "open in the PDF ▸");
    a.href = `/paper?name=${encodeURIComponent(name)}#page=${p.n}`;
    a.target = "_blank"; a.rel = "noopener";
    h.append(a);
  }
  s.append(h);
  s.append(el("div", "docs-page-b"));
  return s;
}

function docsGapCard(p, meta) {
  const c = el("div", "docs-gap");
  c.append(document.createTextNode(
    `Page ${p.n} is a scanned image with no text layer — nothing can answer from it.`));
  const sub = el("span", "");
  sub.textContent =
    (meta.original ? `The paper has it; open page ${p.n} in the PDF and read it there. ` : "") +
    "It needs OCR before a question can reach it. The gap is listed rather than hidden, " +
    "because a page nothing knows about is worse when it looks like a page that was blank.";
  c.append(sub);
  return c;
}

/* Line-numbered text, the same shape as the shared `code()` helper but without its
   windowing: a page is small, and this view never needs to cut one in half. */
const DOCS_RICH = /https?:\/\/|`|\*\*|^\s{0,3}(?:#{1,6}\s|>)/;

function docsCode(lines, from, to) {
  const box = el("div", "code");
  const frag = document.createDocumentFragment();
  for (let n = from; n <= to; n++) {
    const line = lines[n - 1];
    if (line === undefined) break;
    const row = el("div", "ln");
    row.id = "L" + n;
    row.append(el("span", "no", String(n)));
    const tx = el("span", "tx");
    if (!DOCS_RICH.test(line)) {
      tx.textContent = line;                       // the common case, and the fast one
    } else {
      let html = esc(line);
      if (/^\s{0,3}#{1,6}\s/.test(line)) html = `<span class="h">${html}</span>`;
      else if (/^\s{0,3}>/.test(line)) html = `<span class="g">${html}</span>`;
      html = html
        .replace(/(https?:\/\/[^\s&<]+)/g, '<span class="u">$1</span>')
        .replace(/(`[^`]+`)/g, '<span class="c">$1</span>')
        .replace(/(\*\*[^*]+\*\*)/g, '<span class="q">$1</span>');
      tx.innerHTML = html;
    }
    row.append(tx);
    frag.append(row);
  }
  box.append(frag);
  return box;
}

/* ── documents a person typed: a small, safe markdown renderer ───────────────────────
   Everything is escaped before anything is transformed, and the only markup added
   afterwards is built out of the escaped text. A manual containing a `<script>` is a
   manual, not an instruction. There is no library behind this on purpose: the project
   ships no build step and no third-party JavaScript, and the subset a boat's notes
   actually use is small enough to read in one screen. */

const DOCS_LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const DOCS_FENCE = /^\s*(```|~~~)/;
const DOCS_HEAD = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const DOCS_HR = /^\s*([-*_])\s*(?:\1\s*){2,}$/;
const DOCS_ROW = /^\s*\|.*\|\s*$/;
const DOCS_RULE = /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/;

const docsIsBlock = l =>
  DOCS_FENCE.test(l) || DOCS_HEAD.test(l) || DOCS_HR.test(l) ||
  DOCS_LIST.test(l) || DOCS_ROW.test(l) || /^\s*>/.test(l);

function docsBlock(node, from, to) {
  const b = el("div", "mdb");
  b.dataset.line = String(from);
  b.dataset.end = String(to);
  b.id = "L" + from;
  b.append(el("span", "gut", String(from)));
  b.append(node);
  return b;
}

function docsMarkdown(lines) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const raw = lines[i];
    if (!raw.trim()) { i++; continue; }
    const start = i + 1;
    let m;

    if ((m = raw.match(DOCS_FENCE))) {
      const fence = m[1], buf = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence)) { buf.push(lines[i]); i++; }
      if (i < lines.length) i++;
      const pre = el("pre", "md-code");
      pre.textContent = buf.join("\n");
      out.push(docsBlock(pre, start, i));
      continue;
    }

    if ((m = raw.match(DOCS_HEAD))) {
      const lvl = m[1].length;
      const hd = el("h" + lvl);
      hd.innerHTML = docsInline(m[2]);
      i++;
      const b = docsBlock(hd, start, start);
      b.dataset.head = String(lvl);
      out.push(b);
      continue;
    }

    if (DOCS_HR.test(raw)) { out.push(docsBlock(el("div", "md-hr"), start, start)); i++; continue; }

    if (/^\s*>/.test(raw)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*>\s?/, "")); i++;
      }
      const q = el("blockquote");
      q.innerHTML = buf.join("\n").split(/\n\s*\n/).filter(x => x.trim())
        .map(x => `<p>${docsInline(x.replace(/\n/g, " ").trim())}</p>`).join("") || "&nbsp;";
      out.push(docsBlock(q, start, i));
      continue;
    }

    if (DOCS_ROW.test(raw) && DOCS_RULE.test(lines[i + 1] || "") &&
        (lines[i + 1] || "").includes("|")) {
      const cells = l => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|")
                          .map(s => s.trim());
      const t = el("table", "md-t"), th = el("thead"), tr = el("tr");
      cells(raw).forEach(v => { const c = el("th"); c.innerHTML = docsInline(v); tr.append(c); });
      th.append(tr); t.append(th);
      i += 2;
      const tb = el("tbody");
      while (i < lines.length && DOCS_ROW.test(lines[i])) {
        const r = el("tr");
        cells(lines[i]).forEach(v => { const c = el("td"); c.innerHTML = docsInline(v); r.append(c); });
        tb.append(r); i++;
      }
      t.append(tb);
      const w = el("div", "md-tw");
      w.append(t);
      out.push(docsBlock(w, start, i));
      continue;
    }

    if (DOCS_LIST.test(raw)) {
      const items = [];
      while (i < lines.length) {
        const li = lines[i].match(DOCS_LIST);
        if (li) {
          items.push({ indent: li[1].replace(/\t/g, "    ").length,
                       ord: /\d/.test(li[2]), text: li[3] });
          i++; continue;
        }
        if (items.length && lines[i].trim() && /^\s{2,}/.test(lines[i]) &&
            !docsIsBlock(lines[i])) {
          items[items.length - 1].text += " " + lines[i].trim(); i++; continue;
        }
        break;
      }
      out.push(docsBlock(docsListTree(items), start, i));
      continue;
    }

    const buf = [];
    while (i < lines.length && lines[i].trim() && !docsIsBlock(lines[i])) {
      buf.push(lines[i].trim()); i++;
    }
    const p = el("p");
    p.innerHTML = docsInline(buf.join(" "));
    out.push(docsBlock(p, start, i));
  }
  return out;
}

function docsListTree(items) {
  if (!items.length) return el("ul");
  const base = Math.min(...items.map(x => x.indent));
  const root = el(items[0].ord ? "ol" : "ul");
  let cur = null, child = [];
  const flush = () => { if (child.length && cur) { cur.append(docsListTree(child)); child = []; } };
  for (const it of items) {
    if (it.indent > base && cur) { child.push(it); continue; }
    flush();
    cur = el("li");
    cur.innerHTML = docsInline(it.text);
    root.append(cur);
  }
  flush();
  return root;
}

/* A URL out of a document is a string somebody else wrote. Only the schemes a document
   legitimately links with get through; everything else stays as the text it was. */
const docsSafeUrl = u => /^(https?:\/\/|mailto:|#|\.{0,2}\/(?!\/))/i.test(String(u));

function docsInline(s) {
  const keep = [];
  const hold = html => "\u0000" + (keep.push(html) - 1) + "\u0000";
  let h = esc(String(s ?? ""));
  h = h.replace(/`([^`]+)`/g, (m, a) => hold(`<code class="md-c">${a}</code>`));
  h = h.replace(/\[([^\]]*)\]\(([^)\s]+)\)/g, (m, txt, u) => docsSafeUrl(u)
        ? hold(`<a href="${u}" target="_blank" rel="noopener noreferrer">${txt}</a>`) : m);
  h = h.replace(/(^|[\s(])(https?:\/\/[^\s<)\]]+)/g, (m, pre, u) =>
        pre + hold(`<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`));
  h = h.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  h = h.replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<i>$2</i>");
  h = h.replace(/(^|[\s(])_([^_\n]+)_(?=[\s.,;:!?)]|$)/g, "$1<i>$2</i>");
  for (let pass = 0; pass < 4 && h.includes("\u0000"); pass++) {
    h = h.replace(/\u0000(\d+)\u0000/g, (m, k) => keep[Number(k)] ?? "");
  }
  return h;
}

/* ── find-in-document highlighting, over whatever is currently built ─────────────── */
function docsUnmark(root) {
  root.querySelectorAll("mark.fmark").forEach(m => {
    const p = m.parentNode;
    if (!p) return;
    p.replaceChild(document.createTextNode(m.textContent), m);
    p.normalize();
  });
}

function docsMark(root, needle) {
  const q = String(needle || "").toLowerCase();
  if (!q) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: nd => (nd.nodeValue && nd.nodeValue.toLowerCase().includes(q) &&
                       nd.parentNode && nd.parentNode.nodeName !== "MARK" &&
                       !nd.parentNode.classList.contains("no") &&
                       !nd.parentNode.classList.contains("gut"))
      ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
  });
  const found = [];
  let nd;
  while ((nd = walker.nextNode())) found.push(nd);
  for (const t of found) {
    const s = t.nodeValue, low = s.toLowerCase();
    const frag = document.createDocumentFragment();
    let at = 0, k;
    while ((k = low.indexOf(q, at)) >= 0) {
      if (k > at) frag.append(document.createTextNode(s.slice(at, k)));
      frag.append(el("mark", "fmark", s.slice(k, k + q.length)));
      at = k + q.length;
    }
    frag.append(document.createTextNode(s.slice(at)));
    if (t.parentNode) t.parentNode.replaceChild(frag, t);
  }
}

/* ── the outline of a document a person typed ────────────────────────────────────── */
function docsHeadings(lines) {
  const out = [];
  let fence = false;
  for (let i = 0; i < lines.length; i++) {
    if (DOCS_FENCE.test(lines[i])) { fence = !fence; continue; }
    if (fence) continue;
    const m = lines[i].match(DOCS_HEAD);
    if (m) out.push({ level: m[1].length, text: m[2].replace(/[*`_]/g, ""), line: i + 1 });
  }
  return out;
}
