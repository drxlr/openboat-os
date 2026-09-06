/* OpenBoat console — the overview view. Loaded by console.html after the shared
   furniture; it relies on the globals defined there (state, el, api, table, pane…).
   No boat facts live here: everything on screen arrives from the API. */

/* ── view: overview ──────────────────────────────────────────────────────────────── */
async function viewOverview() {
  const v = el("div", "view plain");
  const scroll = el("div", "scroll");
  v.append(scroll);
  mount(v);

  const [p, docs, snags, maint] = await Promise.all([
    state.profile ? Promise.resolve(state.profile) : api("/api/profile"),
    api("/api/docs"), api("/api/snags"), api("/api/maintenance"),
  ]);
  state.profile = p; state.docs = docs;

  const open  = snags.open_count || 0;
  const due   = (maint.items || []).filter(m => m.verdict === "due").length;
  const soon  = (maint.items || []).filter(m => m.verdict === "soon").length;
  const unkn  = (maint.items || []).filter(m => m.verdict === "unknown").length;

  const tiles = el("div", "tiles");
  const tile = (n, label, note, cls) => {
    const t = el("div", "tile");
    const b = el("b", cls || (n ? "" : "z"), String(n));
    t.append(b);
    t.append(el("span", "", label));
    if (note) t.append(el("em", "", note));
    tiles.append(t);
  };
  tile(nf(docs.count), "DOCUMENTS",
       `${nf(docs.passages)} passages` + (docs.missing ? ` · ${docs.missing} missing` : ""));
  tile(nf(docs.gaps || 0), "PAGES NEEDING OCR",
       docs.gaps ? "nothing can answer from them" : "every page is readable",
       docs.gaps ? "q" : "");
  tile(open, "OPEN SNAGS", `${(snags.snags || []).length} filed in all`, open ? "q" : "");
  tile(due, "SERVICE DUE", soon ? `${soon} coming up` : "counted in running hours",
       due ? "q" : "");
  tile(unkn, "NEVER RECORDED", unkn ? "no service has ever been logged" : "all accounted for",
       unkn ? "q" : "");

  scroll.append(tiles);

  const cols = el("div", "cols");

  const owed = el("div", "card");
  owed.innerHTML = `<h2>What the boat is owed<span class="side">TASKS</span></h2>`;
  const items = [
    ...(snags.snags || []).filter(s => s.open).map(s => ({
      t: s.title, s: `${s.where || "snag"} · ${ago(s.when)}` })),
    ...(maint.items || []).filter(m => m.verdict === "due" || m.verdict === "soon")
      .map(m => ({ t: m.item, s: m.why })),
  ];
  if (!items.length) owed.append(Object.assign(el("p"),
    { textContent: "Nothing open and nothing due. Either the boat is in good order or " +
                   "nobody has written anything down." }));
  else {
    const ul = el("ul");
    items.slice(0, 8).forEach(i => {
      const li = el("li");
      li.append(el("span", "t", i.t));
      li.append(el("span", "s", i.s));
      ul.append(li);
    });
    owed.append(ul);
    if (items.length > 8) owed.append(Object.assign(el("p"),
      { textContent: `and ${items.length - 8} more on the Tasks page.` }));
  }

  const prov = el("div", "card");
  prov.innerHTML = `<h2>Where the numbers come from<span class="side">PROVENANCE</span></h2>`;
  const pl = el("ul");
  [["Engine hours", maint.engine_hours_source || "not counted"],
   ["Documents", `${nf(docs.count)} file(s) named in the profile, re-read on every search`],
   ["Snags", `parsed out of the boat's own markdown, ${(snags.snags || []).length} entr` +
             `${(snags.snags || []).length === 1 ? "y" : "ies"}`],
   ["Measurements", (p.unsourced || []).length
      ? `${p.unsourced.length} value(s) carry no source: ${p.unsourced.join(", ")}`
      : "every measurement carries a source"]]
    .forEach(([k, s]) => {
      const li = el("li");
      li.append(el("span", "t", k));
      li.append(el("span", "s", s));
      pl.append(li);
    });
  prov.append(pl);

  const rule = el("div", "card");
  rule.innerHTML = `<h2>The rule this console follows<span class="side">READ-ONLY</span></h2>`;
  rule.append(Object.assign(el("p"), { innerHTML:
    "A silent wrong answer is worse than a crash.<br><br>" +
    "Nothing on this page is generated prose. Every passage is quoted out of a file " +
    "somebody wrote, with the line it came from, and a gap is shown as a gap rather than " +
    "filled in from the page next to it.<br><br>" +
    "Nothing here writes to the boat either. Snags are filed from the phone page on its " +
    "own service and its own port; a service record is written at a keyboard by somebody " +
    "who knows the work happened." }));

  cols.append(owed, prov, rule);
  scroll.append(cols);
}

