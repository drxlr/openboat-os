/* OpenBoat console — the boat view. Loaded by console.html after the shared
   furniture; it relies on the globals defined there (state, el, api, table, pane…).
   No boat facts live here: everything on screen arrives from the API. */

/* ── view: the boat ────────────────────────────────────────────────────────────────
   The measurements, the limits, the papers, what is actually arriving on the wire, and
   — with the same weight as any of it — what is not recorded at all. */
async function viewBoat() {
  const v = el("div", "view plain");
  const scroll = el("div", "scroll");
  v.append(scroll);
  mount(v);
  scroll.append(note("loading", "Reading the profile and the live paths…"));

  const [p, papers, paths] = await Promise.all([
    state.profile && !state.profile.error ? Promise.resolve(state.profile)
                                          : api("/api/profile"),
    api("/api/papers"), api("/api/paths"),
  ]);
  if (!p.error) state.profile = p;
  scroll.textContent = "";

  if (p.error) {
    scroll.append(note("error", "The profile could not be read: " + p.error
                              + ". Nothing below would be about this boat."));
    return;
  }
  const ves = p.vessel || {};

  scroll.append(boatLivePaths(paths));

  const cols = el("div", "cols");

  /* ── measurements ─────────────────────────────────────────────────────────────── */
  const facts = el("div", "card");
  facts.append(cardHead("Measurements", "VESSEL"));
  const fb = el("div"); fb.style.padding = "4px 15px 12px";
  fb.append(kv([
    ["Name", ves.name],
    ["Kind", ves.kind],
    ["Length overall", ves.length_m ? ves.length_m + " m" : ""],
    ["Beam", ves.beam_m ? ves.beam_m + " m" : ""],
    ["Draft", ves.draft_m ? ves.draft_m + " m" : ""],
    ["Air draft", ves.air_draft_m ? ves.air_draft_m + " m" : ""],
    ["Displacement", ves.displacement_kg ? nf(ves.displacement_kg) + " kg" : ""],
    ["Sounder to keel", ves.transducer_to_keel_m ? ves.transducer_to_keel_m + " m" : ""],
    ["Engine", ves.engine_kw ? ves.engine_kw + " kW" : ""],
    ["Timezone", p.timezone],
    ["Profile", p.profile],
  ], null));
  facts.append(fb);

  /* The gaps, given the same weight as the facts. A boat that does not know its own draft
     is the ordinary case, and a panel that only lists what is filled in reads as a
     complete spec sheet — which is the lie this project is built to refuse. */
  const gaps = el("div", "card");
  gaps.append(cardHead("What is not recorded", "HONEST GAPS"));
  const missing = [
    ["Berth", p.berth ? `${p.berth.name || "recorded"}` : null],
    ["Forecast point", p.forecast_point ? (p.forecast_point.name || "recorded") : null],
    ["Draft", ves.draft_m],
    ["Air draft", ves.air_draft_m],
    ["Sounder height above the keel", ves.transducer_to_keel_m],
    ["Displacement", ves.displacement_kg],
  ].filter(([, val]) => !val).map(([k]) => k);

  const ul = el("ul");
  missing.forEach(m => {
    const li = el("li");
    li.append(statusCell("warn", m));
    li.append(el("span", "s", "not set"));
    ul.append(li);
  });
  (p.unsourced || []).forEach(u => {
    const li = el("li");
    li.append(statusCell("info", u));
    li.append(el("span", "s", "a value with no source"));
    ul.append(li);
  });
  if (!ul.children.length)
    gaps.append(Object.assign(el("p"), { textContent:
      "Nothing missing and nothing unsourced. Every measurement carries a source." }));
  else gaps.append(ul);

  /* ── limits ───────────────────────────────────────────────────────────────────── */
  const lim = el("div", "card");
  lim.append(cardHead("Limits this boat will go out in", "WEATHER"));
  const lb = el("div"); lb.style.padding = "4px 15px 12px";
  const L = p.limits || {};
  lb.append(kv([
    ["Wind", L.max_wind_kn ? L.max_wind_kn + " kn" : ""],
    ["Gust", L.max_gust_kn ? L.max_gust_kn + " kn" : ""],
    ["Wave", L.max_wave_m ? L.max_wave_m + " m" : ""],
    ["Rain", L.max_rain_mm ? L.max_rain_mm + " mm/h" : ""],
    ["Daylight only", L.daylight === undefined ? "" : (L.daylight ? "yes" : "no")],
  ], null));
  lim.append(lb);

  cols.append(facts, gaps, lim, boatBands(p), boatPapers(papers));
  scroll.append(cols);
}

/* ── what is actually arriving ─────────────────────────────────────────────────────
   The profile says which path each reading should come from. This says which paths are
   turning up, what they carry and how old it is — the difference between "the boat has
   a depth sounder" and "the depth sounder said something in the last ten seconds". */
function boatLivePaths(paths) {
  const card = el("div", "card");
  const h = el("h2");
  h.append(document.createTextNode("Live paths"));
  const filter = el("input", "minisearch");
  filter.type = "search";
  filter.placeholder = "filter…";
  filter.setAttribute("aria-label", "Filter the live paths");
  h.append(filter);
  h.append(el("span", "side", paths.error ? "OFFLINE" : `${(paths.paths || []).length} PATHS`));
  card.append(h);

  const slot = el("div");
  card.append(slot);

  if (paths.error) {
    slot.append(note("error", "No live data: " + paths.error + "."));
    filter.disabled = true;
    return card;
  }
  const all = paths.paths || [];
  if (!all.length) {
    slot.append(note("empty", paths.online === false
      ? "The boat is not on the network. Nothing is arriving, which is not the same as "
      + "everything reading zero."
      : "The bridge is up but no path has carried a value yet."));
    filter.disabled = true;
    return card;
  }

  const draw = () => {
    const q = filter.value.trim().toLowerCase();
    const rows = q ? all.filter(x => String(x.path).toLowerCase().includes(q)
                                  || String(x.source || "").toLowerCase().includes(q))
                   : all;
    slot.textContent = "";
    slot.append(table([
      { label: "Path",   cls: "k", w: "32%", get: r => r.path },
      { label: "Value",  cls: "w", w: "26%", get: r => boatPathValue(r.value) },
      { label: "Unit",   w: "9%",  prio: "low", get: r => r.unit || "—" },
      { label: "Source", w: "20%", prio: "low",
        get: r => el("span", "trunc", r.source || "—") },
      { label: "Age",    cls: "num", w: "13%", get: r => since(r.timestamp) },
    ], rows, () => {}, null));
    if (q && !rows.length) slot.append(note("empty", "No path matches that."));
  };
  filter.oninput = draw;
  draw();
  return card;
}

/* Signal K carries scalars, strings and small objects (a position, a length with an
   `overall`). Rendering the object as [object Object] is the kind of quiet nonsense that
   makes a page stop being trusted, so every shape gets spelled out. */
function boatPathValue(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number")
    return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(4)));
  if (typeof v === "string" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map(boatPathValue).join(", ");
  return Object.entries(v).map(([k, x]) => `${k} ${boatPathValue(x)}`).join("  ");
}

/* ── alarm bands ───────────────────────────────────────────────────────────────────
   [[low, high, severity], …] per reading, in the reading's own unit. The helm turns a
   value amber or red with these and with nothing else, so an empty table is a fact
   worth printing rather than a panel worth hiding. */
function boatBands(p) {
  const card = el("div", "card");
  const names = Object.keys(p.bands || {});
  card.append(cardHead("Alarm bands", names.length ? `${names.length} READINGS` : "NONE SET"));
  if (!names.length) {
    card.append(Object.assign(el("p"), { textContent:
      "No bands set — a panel with no band cannot go red. Every reading on the helm will "
      + "show its number and no verdict until this boat's profile says what counts as "
      + "wrong." }));
    return card;
  }
  const rows = [];
  names.forEach(name => (p.bands[name] || []).forEach(b => {
    const [low, high, sev] = Array.isArray(b) ? b : [null, null, null];
    rows.push({ name, low, high, sev });
  }));
  card.append(table([
    { label: "Reading", cls: "k", w: "42%", get: r => r.name },
    { label: "Band", cls: "w", w: "34%",
      get: r => `${r.low === null || r.low === undefined ? "—" : nf(r.low)} – ` +
                `${r.high === null || r.high === undefined ? "—" : nf(r.high)}` },
    { label: "Verdict", cls: "st", w: "24%",
      get: r => { const pill = el("span", "pill " + (r.sev || "info"));
                  pill.append(document.createTextNode(r.sev || "unnamed"));
                  return pill; } },
  ], rows, () => {}, null));
  return card;
}

/* ── the ship's papers ─────────────────────────────────────────────────────────────
   Dates that cost money when they pass. Under sixty days is amber, because sixty days
   is roughly what a renewal takes when the office answers slowly. */
function boatPapers(papers) {
  const card = el("div", "card");
  if (papers.error) {
    card.append(cardHead("Papers", "UNREADABLE"));
    card.append(note("error", "The papers could not be read: " + papers.error + "."));
    return card;
  }
  const list = papers.papers || [];
  card.append(cardHead("Papers",
    papers.expiring ? papers.expiring + " EXPIRING" : "SHIP'S DOCUMENTS"));
  if (!list.length) {
    card.append(Object.assign(el("p"), { textContent:
      "No papers recorded. Registration, insurance, radio licence and the survey all have "
      + "dates that matter and none of them are known here." }));
    return card;
  }
  const daysLeft = iso => {
    if (!iso) return null;
    const d = new Date(String(iso).replace(" ", "T"));
    if (isNaN(d)) return null;
    return Math.ceil((d - Date.now()) / 86400000);
  };
  card.append(table([
    { label: "Paper", cls: "k", w: "34%",
      get: x => el("span", "trunc", x.name || x.title || x.kind || "(unnamed)") },
    { label: "Kind", w: "18%", prio: "low",
      get: x => el("span", "trunc", x.kind || "—") },
    { label: "Expires", cls: "st w", w: "24%", get: x => x.expires || "no date" },
    { label: "Left", cls: "num", w: "20%", get: x => {
        const d = daysLeft(x.expires);
        if (d === null) return "—";
        const s = el("span", d < 0 ? "bad" : d < 60 ? "warn" : "");
        s.textContent = d < 0 ? `${-d} d over` : `${d} d`;
        return s;
      } },
  ], list, () => {}, null));
  return card;
}
