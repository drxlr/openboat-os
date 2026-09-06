/* OpenBoat console — the overview view. Loaded by console.html after the shared
   furniture; it relies on the globals defined there (state, el, api, table, pane…).
   No boat facts live here: everything on screen arrives from the API. */

/* ── view: overview ──────────────────────────────────────────────────────────────
   Every number on this page is a question somebody is about to ask twice, so every
   number is a link to the page that answers it. A count you cannot click is a count
   you have to go and look up, and looking it up is where the page stopped being read. */
async function viewOverview() {
  const v = el("div", "view plain");
  const scroll = el("div", "scroll");
  v.append(scroll);
  mount(v);
  scroll.append(note("loading", "Reading the shelf, the snags and the engine log…"));

  const [p, docs, snags, maint, live, paths] = await Promise.all([
    state.profile && !state.profile.error ? Promise.resolve(state.profile)
                                          : api("/api/profile"),
    api("/api/docs"), api("/api/snags"), api("/api/maintenance"),
    api("/api/state"), api("/api/paths"),
  ]);
  if (!p.error) state.profile = p;
  state.docs = docs;
  scroll.textContent = "";

  const broken = [docs, snags, maint].filter(x => x.error);
  if (broken.length) {
    scroll.append(note("error",
      "The console could not read the boat: " + broken[0].error + ". "
      + "Nothing below is a statement about the boat — it is a statement about this "
      + "connection."));
  }

  /* ── the counts ───────────────────────────────────────────────────────────────── */
  const open  = snags.open_count || 0;
  const items = maint.items || [];
  const due   = items.filter(m => m.verdict === "due").length;
  const soon  = items.filter(m => m.verdict === "soon").length;
  const unkn  = items.filter(m => m.verdict === "unknown").length;

  const tiles = el("div", "tiles");
  const tile = (n, label, foot, cls, to) => {
    const t = el(to ? "a" : "div", "tile");
    if (to) { t.href = to; }
    t.append(el("b", cls || (n ? "" : "z"), String(n)));
    t.append(el("span", "", label));
    if (foot) t.append(el("em", "", foot));
    tiles.append(t);
  };
  /* A count the API did not give is an em dash, never a zero. "0 OPEN SNAGS" from a
     server that never answered is the exact shape of a confident wrong answer: it says
     the boat is fine, and what actually happened is that nobody asked it. */
  if (docs.error) {
    tile("—", "DOCUMENTS", "the shelf could not be read", "z", href("docs"));
    tile("—", "PAGES NEEDING OCR", "not known", "z", href("docs"));
  } else {
    tile(nf(docs.count), "DOCUMENTS",
         `${nf(docs.passages)} passages` + (docs.missing ? ` · ${docs.missing} missing` : ""),
         "", href("docs"));
    tile(nf(docs.gaps || 0), "PAGES NEEDING OCR",
         docs.gaps ? "nothing can answer from them" : "every page is readable",
         docs.gaps ? "q" : "", href("docs"));
  }
  if (snags.error)
    tile("—", "OPEN SNAGS", "the snag list could not be read", "z",
         href("tasks", null, { f: "open" }));
  else
    tile(open, "OPEN SNAGS", `${(snags.snags || []).length} filed in all`,
         open ? "q" : "", href("tasks", null, { f: "open" }));
  if (maint.error) {
    tile("—", "SERVICE DUE", "the engine log could not be read", "z",
         href("tasks", null, { f: "service" }));
    tile("—", "NEVER RECORDED", "not known", "z", href("tasks", null, { f: "service" }));
  } else {
    tile(due, "SERVICE DUE", soon ? `${soon} coming up` : "counted in running hours",
         due ? "q" : "", href("tasks", null, { f: "service" }));
    tile(unkn, "NEVER RECORDED",
         unkn ? "no service has ever been logged" : "all accounted for",
         unkn ? "q" : "", href("tasks", null, { f: "service" }));
  }
  scroll.append(tiles);

  /* The boat being ashore is the ordinary case, and it gets a sentence rather than a
     panel of dashes: a panel of dashes reads as instruments that are broken. */
  if (!live.error && live.online === false)
    scroll.append(note("warn", "The boat is not on the network, so there is nothing live "
                             + "to show. Everything below was written down."));

  const cols = el("div", "cols");

  /* ── what is owed ─────────────────────────────────────────────────────────────── */
  const owed = el("div", "card");
  owed.append(cardHead("What the boat is owed", "TASKS", href("tasks")));
  const owedRows = [
    ...(snags.snags || []).filter(s => s.open).map(s => {
      /* A snag's title is the first line somebody typed on a phone, and it stops
         wherever their thumb did. Cut it at the sentence instead, and only add the body
         underneath when the body says something the title has not already said. */
      const titled = sentence(s.title, 130);
      const body = sentence(s.body, 140);
      /* When the body opens with the same words, the title is a prefix of it that some
         thumb stopped early, and the body is the sentence that was actually finished.
         Prefer the finished one — "striker plate is be" is not a thing anybody wrote. */
      const same = body && titled
                && body.slice(0, 24).toLowerCase() === titled.slice(0, 24).toLowerCase();
      const head = same && body.length > titled.length ? body
                 : (titled || body || "an untitled snag");
      return {
        t: head,
        s: `${s.where || "snag"} · ${ago(s.when)}`
           + (body && body !== head && !same ? ` — ${body}` : ""),
        to: href("tasks", "snag:" + s.when),
      };
    }),
    ...items.filter(m => m.verdict === "due" || m.verdict === "soon").map(m => ({
      t: m.item,
      s: sentence(m.why, 130) || m.verdict,
      to: href("tasks", "service:" + m.item),
    })),
  ];
  if (snags.error || maint.error) {
    owed.append(note("error", "This list is unknown, not empty. "
      + (snags.error || maint.error) + "."));
  } else if (!owedRows.length) {
    owed.append(Object.assign(el("p"), { textContent:
      "Nothing open and nothing due. Either the boat is in good order or nobody has "
      + "written anything down." }));
  } else {
    const ul = el("ul");
    owedRows.slice(0, 8).forEach(i => {
      const li = el("li");
      const a = el("a", "row");
      a.href = i.to;
      a.append(el("span", "t", i.t));
      a.append(el("span", "s", i.s));
      li.append(a);
      ul.append(li);
    });
    owed.append(ul);
    if (owedRows.length > 8) {
      const more = el("p");
      const a = el("a", "", `and ${owedRows.length - 8} more on the Tasks page ▸`);
      a.href = href("tasks");
      more.append(a);
      owed.append(more);
    }
  }

  /* ── live ─────────────────────────────────────────────────────────────────────── */
  const cards = [owed];
  if (!live.error && live.online) {
    const lv = el("div", "card");
    lv.append(cardHead("Live", "SIGNAL K", href("boat")));
    const lb = el("div");
    lb.style.padding = "4px 15px 4px";
    const u = (val, unit, dp) =>
      val === null || val === undefined ? "—"
        : (dp === undefined ? String(val) : Number(val).toFixed(dp)) + (unit ? " " + unit : "");
    lb.append(kv([
      ["Speed over ground", u(live.sog_kn, "kn", 1)],
      ["Course over ground", u(live.cog_deg, "°", 0)],
      ["Heading", u(live.heading_deg, "°", 0)],
      ["Depth", u(live.depth_m, "m", 1)],
      ["Wind, apparent", live.wind_apparent_kn === null || live.wind_apparent_kn === undefined
        ? "—" : `${Number(live.wind_apparent_kn).toFixed(1)} kn`
                + (live.wind_apparent_deg === null || live.wind_apparent_deg === undefined
                   ? "" : ` at ${Number(live.wind_apparent_deg).toFixed(0)}°`)],
      ["Engine", u(live.rpm, "rpm", 0)],
      ["Coolant", u(live.coolant_c, "°C", 1)],
      ["Battery", u(live.volts, "V", 1)],
    ], null));
    lv.append(lb);
    /* The age is the honest part. A reading with no age is a reading you cannot tell
       from a reading taken an hour ago with the engine since switched off. */
    const stamps = (paths.paths || []).map(x => x.timestamp).filter(Boolean).sort();
    if (stamps.length)
      lv.append(note("info", `from Signal K, ${since(stamps[stamps.length - 1])}`));
    cards.push(lv);
  }

  /* ── provenance ───────────────────────────────────────────────────────────────── */
  const prov = el("div", "card");
  prov.append(cardHead("Where the numbers come from", "PROVENANCE", href("boat")));
  const pl = el("ul");
  const n = snags.snags ? snags.snags.length : 0;
  [["Engine hours", maint.error ? "the engine log could not be read"
                                : (maint.engine_hours_source || "not counted")],
   ["Documents", docs.error ? "the shelf could not be read"
       : `${nf(docs.count)} file(s) named in the profile, re-read on every search`],
   ["Snags", snags.error ? "the snag list could not be read"
       : `parsed out of the boat's own markdown, ${n} entr${n === 1 ? "y" : "ies"}`],
   ["Measurements", p.error ? "the profile could not be read"
      : (p.unsourced || []).length
      ? `${p.unsourced.length} value(s) carry no source: ${p.unsourced.join(", ")}`
      : "every measurement carries a source"]]
    .forEach(([k, sline]) => {
      const li = el("li");
      li.append(el("span", "t", k));
      li.append(el("span", "s", sline));
      pl.append(li);
    });
  prov.append(pl);

  const rule = el("div", "card");
  rule.append(cardHead("The rule this console follows", "READ-ONLY"));
  rule.append(Object.assign(el("p"), { innerHTML:
    "A silent wrong answer is worse than a crash.<br><br>" +
    "Nothing on this page is generated prose. Every passage is quoted out of a file " +
    "somebody wrote, with the line it came from, and a gap is shown as a gap rather than " +
    "filled in from the page next to it.<br><br>" +
    "Nothing here writes to the boat either. Snags are filed from the phone page on its " +
    "own service and its own port; a service record is written at a keyboard by somebody " +
    "who knows the work happened." }));

  cards.push(prov, rule);
  cards.forEach(c => cols.append(c));
  scroll.append(cols);
}
