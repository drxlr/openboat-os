/* OpenBoat console — the docs view. Loaded by console.html after the shared
   furniture; it relies on the globals defined there (state, el, api, table, pane…).
   No boat facts live here: everything on screen arrives from the API. */

/* ── view: documents ─────────────────────────────────────────────────────────────── */
async function viewDocs() {
  const v = el("div", "view");
  const list = el("div"); list.style.minHeight = "0"; list.style.overflow = "hidden";
  list.style.display = "grid";
  const detail = el("div", "split");

  let timer = null;
  v.append(toolbar("Search this boat's papers — a symptom, a part, a word off a label…",
                   () => { clearTimeout(timer); timer = setTimeout(runDocs, 260); }));
  v.append(list);
  v.append(detail);
  mount(v);

  async function runDocs() {
    if (state.q.trim()) {
      const r = await api("/api/ask?q=" + encodeURIComponent(state.q) + "&limit=25");
      state.hits = r.passages || [];
      state.askedOf = r.documents;
      drawHits(r);
    } else {
      state.hits = null;
      if (!state.docs) state.docs = await api("/api/docs");
      drawLibrary();
    }
  }

  function drawLibrary() {
    const d = state.docs || {};
    const rows = d.documents || [];
    list.textContent = "";
    list.append(table([
      { label: "KIND", cls: "k", w: "8.5em", get: r =>
          !r.exists ? "missing" : r.derived_from ? "extracted" : "written" },
      { label: "DOCUMENT", cls: "w", get: r => {
          const s = el("span", "trunc", r.title || r.name); s.title = r.name; return s; } },
      { label: "SOURCE", w: "26%", get: r => {
          const s = el("span", "trunc",
            r.derived_from ? "from " + r.derived_from : "typed by hand");
          return s; } },
      { label: "PASSAGES", cls: "num", w: "7em", get: r => r.exists ? nf(r.passages) : "—" },
      { label: "SIZE", cls: "num", w: "6em", get: r => r.exists ? kb(r.bytes) : "—" },
      { label: "STATUS", cls: "st", w: "17em", get: r => {
          if (!r.exists) return statusCell("bad", "named but not on disk");
          if (r.gaps)    return statusCell("warn", `${r.gaps} of ${r.pages} pages need OCR`);
          if (r.pages)   return statusCell("ok", `${r.pages_with_text}/${r.pages} pages read`);
          return statusCell("ok", "readable");
        } },
    ], rows, (row, i) => { state.sel = i; drawLibrary(); showDoc(row.name, null); },
       state.sel));
    if (d.missing) {
      const w = el("div", "empty");
      w.append(statusCell("bad",
        `${d.missing} document${d.missing > 1 ? "s are" : " is"} named in the profile and ` +
        `not on disk. Nothing can be answered from ${d.missing > 1 ? "them" : "it"}.`));
      list.append(w);
    }
    if (state.sel === null) idleDocs(d);
  }

  function drawHits(r) {
    const rows = state.hits || [];
    list.textContent = "";
    list.append(table([
      { label: "SCORE", cls: "num", w: "6em", get: h => h.score.toFixed(2) },
      { label: "DOCUMENT", cls: "k", w: "26%", get: h => {
          const s = el("span", "trunc", h.doc); s.title = h.path; return s; } },
      { label: "SECTION", cls: "w", get: h => {
          const s = el("span", "trunc", h.heading || "(no heading)"); return s; } },
      { label: "LINE", cls: "num", w: "5em", get: h => h.line },
      { label: "RANK", cls: "st", w: "5.5em", get: (h, i) =>
          statusCell(i === 0 ? "ok" : h.score > 20 ? "info" : "", `#${i + 1}`) },
    ], rows, (h, i) => { state.sel = i; drawHits(r); showPassage(h); }, state.sel));

    if (!rows.length) {
      list.textContent = "";
      const e = el("div", "empty");
      e.innerHTML =
        `Nothing in ${nf(r.documents || 0)} document(s) matches <b>${esc(state.q)}</b>.` +
        `<br><br>That is an answer, not a failure — this boat's library does not contain ` +
        `it. Ask in either language; the query is widened both ways.`;
      list.append(e);
      detail.textContent = "";
    }
  }

  function idleDocs(d) {
    detail.textContent = "";
    const p = pane("The library", "SHELF");
    p.classList.add("full");
    const b = el("div", "empty");
    b.innerHTML =
      `<b>${nf(d.count || 0)}</b> document(s), <b>${nf(d.passages || 0)}</b> retrievable ` +
      `passages.<br><br>Pick a row to read it, or search above to ask the papers a ` +
      `question. Every answer arrives with the file and the line it came out of, so it ` +
      `can be walked back to the paper and checked.` +
      (d.gaps ? `<br><br>${nf(d.gaps)} page(s) are scanned images with no text layer. ` +
                `They are listed, not hidden — nothing can answer from them until they ` +
                `are put through OCR.` : "");
    p.append(b);
    detail.append(p);
  }

  async function showPassage(h) {
    detail.textContent = "";
    const left = pane(h.doc, "PASSAGE", h.where);
    let tab = state.tab.p || "Passage";
    const render = () => {
      [...left.querySelectorAll(".tabs,.body")].forEach(n => n.remove());
      left.append(tabs(["Passage", "Details"], tab, t => { state.tab.p = tab = t; render(); }));
      const body = el("div", "body");
      if (tab === "Details") {
        body.append(kv([
          ["Section", h.heading || "(no heading)"],
          ["Document", h.doc],
          ["Line", h.line],
          ["Citation", h.where],
          ["BM25 score", h.score],
          ["Characters", nf(h.text.length)],
        ]));
      } else {
        body.append(code(h.text));
      }
      left.append(body);
    };
    render();
    detail.append(left);

    const right = pane(h.doc, "DOCUMENT");
    detail.append(right);
    const doc = await api("/api/doc?name=" + encodeURIComponent(h.doc));
    fillDocPane(right, doc, h.line);
  }

  async function showDoc(name, line) {
    detail.textContent = "";
    const meta = (state.docs.documents || []).find(d => d.name === name) || {};
    const left = pane(meta.title || name, "DOCUMENT", meta.name);
    let tab = state.tab.d || "Details";
    const render = () => {
      [...left.querySelectorAll(".tabs,.body")].forEach(n => n.remove());
      const names = ["Details", "Sections"];
      if (meta.original) names.push("Original");
      left.append(tabs(names, tab, t => { state.tab.d = tab = t; render(); }));
      const body = el("div", "body");
      if (tab === "Details") {
        body.append(kv([
          ["Title", meta.title],
          ["File", meta.name],
          ["Kind", meta.derived_from
            ? "extracted text — the PDF is the original"
            : "written by hand"],
          ["Extracted from", meta.derived_from],
          ["Original beside it", meta.original],
          ["Pages", meta.pages],
          ["Pages with text", meta.pages_with_text],
          ["Pages needing OCR", meta.gaps],
          ["Retrievable passages", nf(meta.passages)],
          ["Lines", nf(meta.lines)],
          ["Size", meta.exists ? kb(meta.bytes) : "not on disk"],
          ["Last changed", meta.modified ? `${meta.modified}  (${ago(meta.modified)})` : ""],
          ["Path", meta.path],
        ]));
        if (meta.derived_from) {
          const note = el("div", "empty");
          note.innerHTML =
            `This text was pulled out of a PDF by a machine. If a passage reads wrongly, ` +
            `the paper is right — re-ingest rather than editing the markdown, or the ` +
            `correction is lost the next time it runs.`;
          body.append(note);
        }
      } else if (tab === "Sections") {
        body.append(el("div", "empty", "Loading…"));
      } else {
        const note = el("div", "empty");
        note.innerHTML =
          `<a href="/paper?name=${encodeURIComponent(name)}" target="_blank" ` +
          `rel="noopener">Open ${esc(meta.original)} ▸</a><br><br>` +
          `Served straight from beside the markdown. This is the document of record; ` +
          `everything in the pane to the right is derived from it.`;
        body.append(note);
      }
      left.append(body);
    };
    render();
    detail.append(left);

    const right = pane(name, "CONTENT");
    detail.append(right);
    const doc = await api("/api/doc?name=" + encodeURIComponent(name));
    fillDocPane(right, doc, line);

    // The section list needs the text, so it is filled once the document has landed.
    if (tab === "Sections") {
      const body = left.querySelector(".body");
      body.textContent = "";
      const heads = String(doc.text || "").split("\n")
        .map((l, i) => ({ l, n: i + 1 }))
        .filter(x => /^#{1,6}\s/.test(x.l));
      if (!heads.length) { body.append(el("div", "empty", "No headings in this file.")); }
      const ul = el("div");
      heads.forEach(x => {
        const depth = x.l.match(/^#+/)[0].length;
        const b = el("button", "", x.l.replace(/^#+\s*/, ""));
        b.style.cssText = `display:block;text-align:left;width:100%;padding:5px 0 5px ${
          (depth - 1) * 14}px;color:var(--${depth === 1 ? "ink" : "mute"})`;
        b.onclick = () => {
          const t = right.querySelector("#L" + x.n);
          if (t) { t.scrollIntoView({ block: "center" }); t.classList.add("hit"); }
        };
        ul.append(b);
      });
      body.append(ul);
    }
  }

  function fillDocPane(p, doc, line) {
    [...p.querySelectorAll(".tabs,.body,.empty")].forEach(n => n.remove());
    if (doc.error || !doc.exists) {
      const e = el("div", "empty");
      e.append(statusCell("bad", doc.error || "not on disk"));
      p.append(e);
      return;
    }
    const lines = String(doc.text).split("\n").length;
    const badge = statusCell("ok", `${nf(lines)} lines · ${nf(doc.document.passages)} passages`);
    p.append(tabs([`Text (${kb(doc.text.length)})`], `Text (${kb(doc.text.length)})`,
                  () => {}, badge));
    const body = el("div", "body");
    body.append(code(doc.text, line));
    p.append(body);
    if (line) setTimeout(() => {
      const t = p.querySelector("#L" + line);
      if (t) t.scrollIntoView({ block: "center" });
    }, 20);
  }

  runDocs();
}

