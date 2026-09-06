/* OpenBoat console — the boat view. Loaded by console.html after the shared
   furniture; it relies on the globals defined there (state, el, api, table, pane…).
   No boat facts live here: everything on screen arrives from the API. */

/* ── view: the boat ────────────────────────────────────────────────────────────────
   The measurements, the limits, the papers, what is actually arriving on the wire, and
   — with the same weight as any of it — what is not recorded at all. */
async function viewBoat() {
  const page = el("div");
  mount(page);
  page.append(note("loading", "Reading the profile and the live paths…"));

  const [p, papers, paths] = await Promise.all([
    state.profile && !state.profile.error ? Promise.resolve(state.profile)
                                          : api("/api/profile"),
    api("/api/papers"), api("/api/paths"),
  ]);
  if (!p.error) state.profile = p;
  page.textContent = "";

  if (p.error) {
    page.append(note("error", "The profile could not be read: " + p.error
                            + ". Nothing below would be about this boat."));
    return;
  }
  const ves = p.vessel || {};

  page.append(boatLivePaths(paths));

  /* ── measurements ─────────────────────────────────────────────────────────────── */
  const facts = el("div", "card h-100");
  facts.append(cardHead("Measurements", "Vessel"));
  facts.append(obKvBody([
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
  ]));

  /* The gaps, given the same weight as the facts. A boat that does not know its own draft
     is the ordinary case, and a panel that only lists what is filled in reads as a
     complete spec sheet — which is the lie this project is built to refuse. */
  const gaps = el("div", "card h-100");
  gaps.append(cardHead("What is not recorded", "Honest gaps"));
  const missing = [
    ["Berth", p.berth ? `${p.berth.name || "recorded"}` : null],
    ["Forecast point", p.forecast_point ? (p.forecast_point.name || "recorded") : null],
    ["Draft", ves.draft_m],
    ["Air draft", ves.air_draft_m],
    ["Sounder height above the keel", ves.transducer_to_keel_m],
    ["Displacement", ves.displacement_kg],
  ].filter(([, val]) => !val).map(([k]) => k);

  const list = el("div", "list-group list-group-flush");
  const gapRow = (label, why, tone) => {
    const li = el("div", "list-group-item d-flex align-items-center gap-2");
    const dot = el("i", "bi bi-circle-fill small " + tone);
    dot.setAttribute("aria-hidden", "true");
    li.append(dot, el("span", "fw-medium", label));
    li.append(el("span", "ms-auto small text-body-secondary text-end", why));
    list.append(li);
  };
  missing.forEach(m => gapRow(m, "not set", "text-warning-emphasis"));
  (p.unsourced || []).forEach(u => gapRow(u, "a value with no source",
                                          "text-info-emphasis"));
  if (!list.children.length)
    gaps.append(cardBody(el("p", "mb-0 text-body-secondary",
      "Nothing missing and nothing unsourced. Every measurement carries a source.")));
  else gaps.append(list);

  /* ── limits ───────────────────────────────────────────────────────────────────── */
  const lim = el("div", "card h-100");
  lim.append(cardHead("Limits this boat will go out in", "Weather"));
  const L = p.limits || {};
  lim.append(obKvBody([
    ["Wind", L.max_wind_kn ? L.max_wind_kn + " kn" : ""],
    ["Gust", L.max_gust_kn ? L.max_gust_kn + " kn" : ""],
    ["Wave", L.max_wave_m ? L.max_wave_m + " m" : ""],
    ["Rain", L.max_rain_mm ? L.max_rain_mm + " mm/h" : ""],
    ["Daylight only", L.daylight === undefined ? "" : (L.daylight ? "yes" : "no")],
  ]));

  const cols = el("div", "row g-3");
  const half = node => { const c = el("div", "col-12 col-lg-6"); c.append(node); cols.append(c); };
  const full = node => { const c = el("div", "col-12"); c.append(node); cols.append(c); };
  half(facts);
  half(gaps);
  half(lim);
  half(boatBands(p));
  full(boatPapers(papers));
  page.append(cols);
}

/* ── what is actually arriving ─────────────────────────────────────────────────────
   The profile says which path each reading should come from. This says which paths are
   turning up, what they carry and how old it is — the difference between "the boat has
   a depth sounder" and "the depth sounder said something in the last ten seconds". */
function boatLivePaths(paths) {
  const card = el("div", "card mb-3");
  const head = el("div", "card-header d-flex flex-wrap align-items-center gap-2");
  head.append(el("h2", "h6 mb-0 fw-semibold", "Live paths"));
  const filter = el("input", "form-control form-control-sm ob-filter ms-auto");
  filter.type = "search";
  filter.placeholder = "filter…";
  filter.setAttribute("aria-label", "Filter the live paths");
  head.append(filter);
  head.append(el("span", "side", paths.error ? "Offline"
                                             : `${(paths.paths || []).length} paths`));
  card.append(head);

  const slot = el("div");
  card.append(slot);

  if (paths.error) {
    slot.append(cardBody(obFlat(note("error", "No live data: " + paths.error + "."))));
    filter.disabled = true;
    return card;
  }
  const all = paths.paths || [];
  if (!all.length) {
    slot.append(cardBody(obFlat(note("empty", paths.online === false
      ? "The boat is not on the network. Nothing is arriving, which is not the same as "
      + "everything reading zero."
      : "The bridge is up but no path has carried a value yet."))));
    filter.disabled = true;
    return card;
  }

  const draw = () => {
    const q = filter.value.trim().toLowerCase();
    const rows = q ? all.filter(x => String(x.path).toLowerCase().includes(q)
                                  || String(x.source || "").toLowerCase().includes(q))
                   : all;
    slot.textContent = "";
    const t = table([
      { label: "Path", w: "32%",
        get: r => el("span", "font-monospace small", r.path) },
      { label: "Value", w: "26%", get: r => boatPathValue(r.value) },
      { label: "Unit", w: "9%", prio: "low", get: r => r.unit || "—" },
      { label: "Source", w: "20%", prio: "low", cls: "text-body-secondary",
        get: r => r.source || "—" },
      { label: "Age", cls: "num text-nowrap", w: "13%", get: r => since(r.timestamp) },
    ], rows, null, null);
    t.querySelector("table").classList.add("table-sm", "ob-paths");
    slot.append(t);
    if (q && !rows.length) slot.append(cardBody(obFlat(note("empty", "No path matches that."))));
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
  const card = el("div", "card h-100");
  const names = Object.keys(p.bands || {});
  card.append(cardHead("Alarm bands", names.length ? `${names.length} readings` : "None set"));
  if (!names.length) {
    card.append(cardBody(el("p", "mb-0 text-body-secondary",
      "No bands set — a panel with no band cannot go red. Every reading on the helm will "
      + "show its number and no verdict until this boat's profile says what counts as "
      + "wrong.")));
    return card;
  }
  const rows = [];
  names.forEach(name => (p.bands[name] || []).forEach(b => {
    const [low, high, sev] = Array.isArray(b) ? b : [null, null, null];
    rows.push({ name, low, high, sev });
  }));
  card.append(table([
    { label: "Reading", w: "42%", get: r => r.name },
    { label: "Band", cls: "num text-nowrap", w: "34%",
      get: r => `${r.low === null || r.low === undefined ? "—" : nf(r.low)} – ` +
                `${r.high === null || r.high === undefined ? "—" : nf(r.high)}` },
    { label: "Verdict", w: "24%",
      get: r => statusCell(r.sev, r.sev || "unnamed") },
  ], rows, null, null));
  return card;
}

/* ── the ship's papers ─────────────────────────────────────────────────────────────
   Dates that cost money when they pass. Under sixty days is amber, because sixty days
   is roughly what a renewal takes when the office answers slowly. */
function boatPapers(papers) {
  const card = el("div", "card");
  if (papers.error) {
    card.append(cardHead("Papers", "Unreadable"));
    card.append(cardBody(obFlat(
      note("error", "The papers could not be read: " + papers.error + "."))));
    return card;
  }
  const list = papers.papers || [];
  card.append(cardHead("Papers",
    papers.expiring ? papers.expiring + " expiring" : "Ship's documents"));
  if (!list.length) {
    card.append(cardBody(el("p", "mb-0 text-body-secondary",
      "No papers recorded. Registration, insurance, radio licence and the survey all have "
      + "dates that matter and none of them are known here.")));
    return card;
  }
  const daysLeft = iso => {
    if (!iso) return null;
    const d = new Date(String(iso).replace(" ", "T"));
    if (isNaN(d)) return null;
    return Math.ceil((d - Date.now()) / 86400000);
  };
  card.append(table([
    { label: "Paper", w: "34%", get: x => x.name || x.title || x.kind || "(unnamed)" },
    { label: "Kind", w: "18%", prio: "low", cls: "text-body-secondary",
      get: x => x.kind || "—" },
    { label: "Expires", cls: "num text-nowrap", w: "24%", get: x => x.expires || "no date" },
    { label: "Left", cls: "num text-nowrap", w: "20%", get: x => {
        const d = daysLeft(x.expires);
        if (d === null) return "—";
        const s = el("span", d < 0 ? "text-danger-emphasis fw-semibold"
                          : d < 60 ? "text-warning-emphasis fw-semibold" : "");
        s.textContent = d < 0 ? `${-d} d over` : `${d} d`;
        return s;
      } },
  ], list, null, null));
  return card;
}
