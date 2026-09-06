/* OpenBoat console — the boat view. Loaded by console.html after the shared
   furniture; it relies on the globals defined there (state, el, api, table, pane…).
   No boat facts live here: everything on screen arrives from the API. */

/* ── view: the boat ──────────────────────────────────────────────────────────────── */
async function viewBoat() {
  const v = el("div", "view plain");
  const scroll = el("div", "scroll");
  v.append(scroll);
  mount(v);

  const p = state.profile || (state.profile = await api("/api/profile"));
  const papers = await api("/api/papers");
  const ves = p.vessel || {};

  const facts = el("div", "card");
  facts.append(Object.assign(el("h2", "", "Measurements"),
    { innerHTML: `Measurements<span class="side">VESSEL</span>` }));
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
  ]));
  facts.append(fb);

  /* The gaps, given the same weight as the facts. A boat that does not know its own draft
     is the ordinary case, and a panel that only lists what is filled in reads as a
     complete spec sheet — which is the lie this project is built to refuse. */
  const gaps = el("div", "card");
  gaps.innerHTML = `<h2>What is not recorded<span class="side">HONEST GAPS</span></h2>`;
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

  const lim = el("div", "card");
  lim.innerHTML = `<h2>Limits this boat will go out in<span class="side">WEATHER</span></h2>`;
  const lb = el("div"); lb.style.padding = "4px 15px 12px";
  const L = p.limits || {};
  lb.append(kv([
    ["Wind", L.max_wind_kn ? L.max_wind_kn + " kn" : ""],
    ["Gust", L.max_gust_kn ? L.max_gust_kn + " kn" : ""],
    ["Wave", L.max_wave_m ? L.max_wave_m + " m" : ""],
    ["Rain", L.max_rain_mm ? L.max_rain_mm + " mm/h" : ""],
    ["Daylight only", L.daylight === undefined ? "" : (L.daylight ? "yes" : "no")],
  ]));
  lim.append(lb);

  const pap = el("div", "card");
  pap.innerHTML = `<h2>Papers<span class="side">${
    papers.expiring ? papers.expiring + " EXPIRING" : "SHIP'S DOCUMENTS"}</span></h2>`;
  if (!(papers.papers || []).length) {
    pap.append(Object.assign(el("p"), { textContent:
      "No papers recorded. Registration, insurance, radio licence and the survey all have " +
      "dates that matter and none of them are known here." }));
  } else {
    const pu = el("ul");
    papers.papers.forEach(x => {
      const li = el("li");
      li.append(el("span", "t", x.name || x.title || x.kind || "(unnamed)"));
      li.append(el("span", "s", x.expires ? `${x.expires} · ${ago(x.expires)}` : "no date"));
      pu.append(li);
    });
    pap.append(pu);
  }

  const cols = el("div", "cols");
  cols.append(facts, gaps, lim, pap);
  scroll.append(cols);
}

