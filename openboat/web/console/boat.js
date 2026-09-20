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

  const [p, papers, paths, snags] = await Promise.all([
    state.profile && !state.profile.error ? Promise.resolve(state.profile)
                                          : api("/api/profile"),
    api("/api/papers"), api("/api/paths"),
    // Only for its origin: the limits form writes through the snag service, which is the
    // machine's write surface. The boat's own server matches exactly one POST on purpose.
    api("/api/snags"),
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
  const lim = boatLimits(p, snags);

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

/* ── the skipper's limits, and the one form on this page ──────────────────────────────
 *
 * Everything else about a boat is a measurement somebody sourced, and none of it is
 * editable here: a draught is a fact about the hull and a browser is the wrong place to
 * change one. The limits are different in kind. They are a *preference* — where this
 * skipper turns back — and they legitimately change with the crew, the sea area, or a
 * guest who is not enjoying it. So they are the one thing with a form.
 *
 * The card leads with whether anybody actually set them. `passage_window` and the whole
 * "can we go out on Saturday" answer are computed off these five numbers, and a profile
 * carrying another boat's figures reads exactly like a considered one until something on
 * the screen says otherwise. That sentence is the point of this card; the numbers are
 * secondary.
 */
function boatLimits(p, snags) {
  const card = el("div", "card h-100");
  const L = p.limits || {};
  const body = el("div");

  const head = cardHead("Limits this boat will go out in", "Weather");
  card.append(head, body);

  const dl = Array.isArray(L.daylight) ? L.daylight : null;
  const show = () => {
    body.textContent = "";

    if (L.unverified) {
      /* Not a styling choice. A number nobody chose, presented as this boat's limit, is
         the silent wrong answer this project is not allowed to give — and it is worse
         here than anywhere because it is the input to a go/no-go. */
      body.append(cardBody(el("div", "alert alert-warning mb-0",
        "Nobody has set these. They are the package defaults, and they may have been " +
        "carried over from another boat — a 15 kn limit ends an afternoon on an 8 m " +
        "sports cruiser and is a pleasant day on a 17 m yacht. Every passage window and " +
        "every “can we go out” answer is computed from them, so set them before " +
        "reading one.")));
    }

    body.append(obKvBody([
      ["Wind", L.max_wind_kn ? L.max_wind_kn + " kn" : ""],
      ["Gust", L.max_gust_kn ? L.max_gust_kn + " kn" : ""],
      ["Wave", L.max_wave_m ? L.max_wave_m + " m" : ""],
      ["Rain", L.max_rain_mm ? L.max_rain_mm + " mm/h" : ""],
      /* Was rendered as yes/no off a two-element array, so it read "yes" whatever the
         hours were. It is a window, and the window is the useful thing. */
      ["Daylight", dl ? `${String(dl[0]).padStart(2, "0")}:00–` +
                        `${String(dl[1]).padStart(2, "0")}:00` : ""],
    ]));

    const foot = el("div", "card-body pt-0 d-flex flex-wrap gap-3 align-items-center");
    const edit = el("button", "btn btn-sm " + (L.unverified ? "btn-primary" : "btn-outline-secondary"));
    edit.type = "button";
    edit.textContent = L.unverified ? "Set them" : "Edit";
    edit.onclick = () => form();
    foot.append(edit);
    foot.append(el("span", "small text-body-secondary",
                   L.source ? "Set by " + L.source : "Never set"));
    body.append(foot);
  };

  const FIELDS = [
    ["max_wind_kn", "Wind", "kn", 0.5],
    ["max_gust_kn", "Gust", "kn", 0.5],
    ["max_wave_m", "Wave", "m", 0.1],
    ["max_rain_mm", "Rain", "mm/h", 0.1],
  ];

  const form = () => {
    body.textContent = "";
    const wrap = el("div", "card-body");
    wrap.append(el("p", "small text-body-secondary",
      "Where you turn back, not what the boat can survive. The cost of a missed nice day " +
      "is nothing; the cost of a frightened guest is the rest of the season."));

    const inputs = {};
    FIELDS.forEach(([key, label, unit, step]) => {
      const row = el("div", "mb-2");
      const lab = el("label", "form-label small mb-1", `${label} (${unit})`);
      lab.htmlFor = "lim-" + key;
      const inp = el("input", "form-control");
      inp.id = "lim-" + key;
      inp.type = "number"; inp.step = String(step); inp.min = "0";
      inp.value = L[key] === undefined || L[key] === null ? "" : L[key];
      inputs[key] = inp;
      row.append(lab, inp);
      wrap.append(row);
    });

    const hours = el("div", "row g-2 mb-2");
    [["daylight_from_h", "Daylight from"], ["daylight_to_h", "to"]].forEach(([key, label], i) => {
      const col = el("div", "col-6");
      const lab = el("label", "form-label small mb-1", label);
      lab.htmlFor = "lim-" + key;
      const inp = el("input", "form-control");
      inp.id = "lim-" + key;
      inp.type = "number"; inp.min = "0"; inp.max = "23"; inp.step = "1";
      inp.value = dl ? dl[i] : "";
      inputs[key] = inp;
      col.append(lab, inp);
      hours.append(col);
    });
    wrap.append(hours);

    const msg = el("div", "small mb-2");
    msg.hidden = true;
    wrap.append(msg);

    const bar = el("div", "d-flex gap-2");
    const save = el("button", "btn btn-primary");
    save.type = "button"; save.textContent = "Save";
    const cancel = el("button", "btn btn-outline-secondary");
    cancel.type = "button"; cancel.textContent = "Cancel";
    cancel.onclick = () => show();
    bar.append(save, cancel);
    wrap.append(bar);
    body.append(wrap);

    save.onclick = async () => {
      save.disabled = true; save.textContent = "Saving…";
      msg.hidden = true;
      const payload = {};
      Object.entries(inputs).forEach(([k, inp]) => {
        /* `valueAsNumber`, not `Number(value)`. On a German-locale browser a number input
           renders 0.8 as "0,8", and parsing that string gives NaN — which would arrive at
           the server as a missing field and silently leave the old limit in place. The
           property is locale-independent by specification. */
        if (String(inp.value).trim() === "") return;
        const n = inp.valueAsNumber;
        if (Number.isFinite(n)) payload[k] = n;
      });
      if (!Object.keys(payload).length) {
        msg.className = "small mb-2 text-danger-emphasis";
        msg.textContent = "Nothing to save — every field was left empty.";
        msg.hidden = false;
        save.disabled = false; save.textContent = "Save";
        return;
      }
      const by = (window.OB_USER && (window.OB_USER.name || window.OB_USER.email)) || "";
      const r = await snagPost(snags, { ...payload, by }, "/api/limits");
      save.disabled = false; save.textContent = "Save";
      if (!r || r.error) {
        /* The server's sentence, verbatim. It names the field and says why — "the gust
           limit is below the wind limit, which would make the wind limit meaningless" is
           worth more than "invalid input", and rewording it here would lose that. */
        msg.className = "small mb-2 text-danger-emphasis";
        msg.textContent = (r && r.error) || "The limits were not saved.";
        msg.hidden = false;
        return;
      }
      Object.assign(L, r.limits || {});
      /* The cached profile drives other views; leaving it stale would have the helm
         answering off the old numbers until a reload. */
      if (state.profile && state.profile.limits) state.profile.limits = { ...L };
      show();
    };
  };

  show();
  return card;
}
