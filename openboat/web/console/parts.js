/* OpenBoat console — the parts view.
 *
 * Everything anybody has indexed for this boat, across every shop, in one searchable list.
 * `tasks` answers "what does this job need"; this answers the question underneath it —
 * what exists for this boat at all, and who sells it.
 *
 * It searches in the browser. A thousand rows is nothing for a phone and everything for a
 * round trip per keystroke, so the whole index arrives once and every keypress after that
 * is free — which matters most in the place this gets used, on a marina wifi that is
 * technically connected.
 *
 * Nothing here orders. Rows link out to the shop and add to the same basket `cart.js`
 * owns, which is spent on the shop's own site by a person who read its terms.
 */

const PARTS_PAGE = 60;          // rows before "show more"; a thumb scrolls, it does not page
let partsIndex = null;          // the whole /api/catalogue answer
let partsShown = PARTS_PAGE;
let partsQuery = "";
let partsShop = "";
let partsCat = "";
let partsSort = "name";
let partsRun = 0;

const partsMoney = v =>
  v === null || v === undefined
    ? "—" : "€" + Number(v).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, " ");

/* The shop's terms, said on every row that came from it. They are facts about the
   merchant, not about the part, and a row that omits them is a price with no basis. */
function partsShopOf(id) {
  return ((partsIndex && partsIndex.shops) || []).find(s => s.id === id) || null;
}

function partsTerms(shop) {
  if (!shop) return "shop not recorded";
  const bits = [shop.name || shop.id];
  bits.push({ incl: "inc VAT", excl: "ex VAT" }[shop.vat] || "VAT basis unknown");
  if (shop.ships_from) bits.push("from " + shop.ships_from);
  bits.push(shop.checked ? "indexed " + shop.checked : "never dated");
  return bits.join(" · ");
}

/* ── filtering ────────────────────────────────────────────────────────────────────── */

/* Every word must appear somewhere in the row, not any word. "furling rope" should not
   return every rope and every furler — on a catalogue this size that is the difference
   between a search and a list. */
function partsMatch(item, words) {
  if (!words.length) return true;
  const hay = [item.number, item.name, item.category, item.brand, item.description]
    .join(" ").toLowerCase();
  return words.every(w => hay.includes(w));
}

function partsFiltered() {
  const words = partsQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
  let rows = ((partsIndex && partsIndex.items) || []).filter(i =>
    (!partsShop || i.shop === partsShop) &&
    (!partsCat || i.category === partsCat || i.category.startsWith(partsCat + " > ")) &&
    partsMatch(i, words));

  const dir = partsSort.endsWith("-desc") ? -1 : 1;
  if (partsSort.startsWith("price"))
    /* Unpriced rows sort last in both directions rather than counting as zero. A part with
       no price is not a cheap part. */
    rows = rows.slice().sort((a, b) => {
      const av = a.price_eur, bv = b.price_eur;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      return (av - bv) * dir;
    });
  else
    rows = rows.slice().sort((a, b) => a.name.localeCompare(b.name) * dir);
  return rows;
}

/* ── one row ──────────────────────────────────────────────────────────────────────── */

/* Collapsed: number, name, category, price, shop. Opened: the specification — which is the
   half a listing never shows and the only half worth reading before ordering — plus the
   fitment list and the way out to the shop. */
function partsRow(item) {
  const shop = partsShopOf(item.shop);
  const wrap = el("div", "list-group-item px-0 py-0");

  const head = el("button", "btn btn-link text-decoration-none text-body w-100 text-start " +
                            "px-3 py-3 d-flex flex-wrap gap-3 align-items-start");
  head.type = "button";
  head.setAttribute("aria-expanded", "false");

  const left = el("div", "flex-grow-1");
  left.style.minWidth = "0";
  const title = el("div", "d-flex flex-wrap gap-2 align-items-baseline");
  title.append(el("span", "fw-semibold text-break", item.name));
  title.append(el("code", "small", item.number));
  left.append(title);
  const meta = [];
  if (item.category) meta.push(item.category);
  if ((item.fits || []).length) meta.push(`fits ${item.fits.length} model${item.fits.length === 1 ? "" : "s"}`);
  left.append(el("div", "small text-body-secondary text-break", meta.join(" · ")));

  const right = el("div", "text-end flex-shrink-0");
  right.style.minWidth = "8rem";
  right.append(el("div", shop && shop.stale ? "num text-body-secondary" : "num",
                  partsMoney(item.price_eur) +
                  (shop && shop.vat === "excl" ? " ex" : shop && shop.vat === "incl" ? " inc" : "")));
  right.append(el("div", "small text-body-secondary text-break", (shop && (shop.name || shop.id)) || item.shop));
  if (shop && shop.stale)
    right.append(el("div", "small text-warning-emphasis", "not a current price"));

  const chev = el("i", "bi bi-chevron-down text-body-secondary small flex-shrink-0 mt-1");
  head.append(left, right, chev);
  wrap.append(head);

  const body = el("div", "px-3 pb-3");
  body.hidden = true;
  let filled = false;

  function fill() {
    if (filled) return;
    filled = true;
    body.append(el("div", "small text-body-secondary mb-2", partsTerms(shop)));

    if (item.description) {
      const spec = el("p", "text-break mb-2");
      spec.style.whiteSpace = "pre-wrap";
      spec.textContent = item.description;
      body.append(spec);
      /* The list is served with descriptions trimmed. Say so rather than letting a cut
         sentence read as the whole specification. */
      if (item.description.length >= 400)
        body.append(el("p", "small text-body-secondary",
                       "Trimmed here — open the shop page for the rest."));
    } else {
      body.append(el("p", "small text-body-secondary mb-2",
        "The shop gives no specification beyond the name. Nothing more is recorded, " +
        "because anything more would be a guess."));
    }

    if ((item.fits || []).length) {
      const det = el("details", "mb-2");
      const sum = el("summary", "small text-body-secondary");
      sum.textContent = `Fits ${item.fits.length} model${item.fits.length === 1 ? "" : "s"}`;
      det.append(sum);
      det.append(el("div", "small text-break pt-1", item.fits.join(" · ")));
      body.append(det);
      /* Worth naming, because it decides where to buy: a part the yard lists against
         thirty hulls is somebody else's component with a yard sticker on it. */
      if (item.fits.length >= 15)
        body.append(el("p", "small text-body-secondary",
          "Listed against many models — likely a bought-in component, so it is probably " +
          "available elsewhere under its own maker's number."));
    }

    const bar = el("div", "d-flex flex-wrap gap-2 pt-1");
    const add = el("button", "btn btn-sm btn-outline-primary");
    add.type = "button";
    add.innerHTML = '<i class="bi bi-plus-lg me-1"></i>Add to list';
    add.onclick = () => {
      /* No `item`: this was picked off the catalogue, not against a job, and "for parts"
         in the basket would be a label that says nothing. A row added from a task carries
         that task's name; one added here carries none, honestly. */
      cartAdd({ item: "", part: item.name, number: item.number, qty: 1,
                supplier: (shop && (shop.name || shop.id)) || item.shop,
                price_eur: item.price_eur, vat: shop ? shop.vat : "",
                stock: "", checked: shop ? shop.checked : "",
                stale: !!(shop && shop.stale), url: item.url });
      add.innerHTML = '<i class="bi bi-check2 me-1"></i>On the list';
    };
    bar.append(add);
    if (item.url) {
      const go = el("a", "btn btn-sm btn-link");
      go.href = item.url; go.target = "_blank"; go.rel = "noopener noreferrer";
      go.innerHTML = 'Open at the shop <i class="bi bi-box-arrow-up-right ms-1"></i>';
      bar.append(go);
    }
    body.append(bar);
  }

  head.onclick = () => {
    const open = body.hidden;
    if (open) fill();
    body.hidden = !open;
    head.setAttribute("aria-expanded", String(open));
    chev.className = `bi bi-chevron-${open ? "up" : "down"} text-body-secondary ` +
                     `small flex-shrink-0 mt-1`;
  };

  wrap.append(body);
  return wrap;
}

/* ── the view ─────────────────────────────────────────────────────────────────────── */

async function partsView() {
  const run = ++partsRun;
  const v = el("div");
  v.append(pageHead("Parts", ["every shop indexed for this boat"], []));
  const holder = el("div");
  holder.append(el("p", "text-body-secondary", "Reading the catalogues…"));
  v.append(holder);
  mount(v);

  if (!partsIndex) {
    const d = await api("/api/catalogue");
    if (run !== partsRun) return;
    partsIndex = d;
  }
  if (run !== partsRun) return;

  holder.textContent = "";

  if (partsIndex && partsIndex.error) {
    holder.append(el("div", "alert alert-warning",
      "The catalogue could not be read: " + partsIndex.error));
    return;
  }
  if (!partsIndex || !(partsIndex.items || []).length) {
    holder.append(cardBody(el("p", "mb-0 text-body-secondary",
      "No shop is indexed for this boat yet. One JSON file per shop goes in a " +
      "catalogue/ directory beside this boat's boat.toml.")));
    return;
  }

  /* Shops first, with their terms and their age. A stale shop is the single most
     important thing on this screen and it is stated before any price is shown. */
  const shops = pane("Shops indexed", `${partsIndex.shops.length}`);
  const slist = el("ul", "list-group list-group-flush");
  partsIndex.shops.forEach(s => {
    const n = partsIndex.items.filter(i => i.shop === s.id).length;
    const li = el("li", "list-group-item d-flex flex-wrap gap-2 align-items-baseline");
    const who = el("div", "flex-grow-1");
    who.append(el("div", "fw-semibold text-break", s.name || s.id));
    who.append(el("div", "small text-body-secondary text-break", partsTerms(s)));
    if (s.note) who.append(el("div", "small text-body-secondary fst-italic text-break", s.note));
    li.append(who);
    li.append(el("span", "num", String(n)));
    if (s.stale)
      li.append(el("span", "badge rounded-pill text-bg-warning",
                   `indexed ${s.age_days === null ? "never" : s.age_days + " days ago"}`));
    slist.append(li);
  });
  shops.append(slist);
  holder.append(shops);

  const list = pane("Parts", "");
  const controls = el("div", "card-body border-bottom");

  const search = el("input", "form-control mb-2");
  search.type = "search";
  search.placeholder = "Search name, part number or specification…";
  search.value = partsQuery;
  search.setAttribute("aria-label", "Search parts");
  controls.append(search);

  const row = el("div", "d-flex flex-wrap gap-2");
  const shopSel = el("select", "form-select w-auto");
  shopSel.append(new Option("All shops", ""));
  partsIndex.shops.forEach(s => shopSel.append(new Option(s.name || s.id, s.id)));
  shopSel.value = partsShop;

  const catSel = el("select", "form-select w-auto");
  catSel.style.maxWidth = "20rem";
  catSel.append(new Option("All categories", ""));
  /* Top-level groups first, then the full paths under them: a flat list of 90 paths is
     not a filter anybody uses. */
  const tops = [...new Set(partsIndex.categories.map(c => c.split(" > ")[0]))].sort();
  tops.forEach(t => {
    catSel.append(new Option(t, t));
    partsIndex.categories.filter(c => c.startsWith(t + " > "))
      .forEach(c => catSel.append(new Option("   " + c.split(" > ").slice(1).join(" > "), c)));
  });
  catSel.value = partsCat;

  const sortSel = el("select", "form-select w-auto");
  [["name", "Name A–Z"], ["name-desc", "Name Z–A"],
   ["price", "Price, low first"], ["price-desc", "Price, high first"]]
    .forEach(([v2, l]) => sortSel.append(new Option(l, v2)));
  sortSel.value = partsSort;

  row.append(shopSel, catSel, sortSel);
  const clear = el("button", "btn btn-outline-secondary");
  clear.type = "button";
  clear.textContent = "Clear";
  row.append(clear);
  controls.append(row);
  list.append(controls);

  const count = el("div", "card-body py-2 small text-body-secondary border-bottom");
  list.append(count);
  const rows = el("div", "list-group list-group-flush");
  list.append(rows);
  const more = el("div", "card-body");
  list.append(more);
  holder.append(list);

  function draw() {
    const hits = partsFiltered();
    count.textContent =
      `${hits.length} of ${partsIndex.count} parts` +
      (partsQuery || partsShop || partsCat ? " match" : "");
    rows.textContent = "";
    hits.slice(0, partsShown).forEach(i => rows.append(partsRow(i)));
    more.textContent = "";
    if (hits.length > partsShown) {
      const b = el("button", "btn btn-outline-secondary w-100");
      b.type = "button";
      b.textContent = `Show ${Math.min(PARTS_PAGE, hits.length - partsShown)} more ` +
                      `of ${hits.length - partsShown}`;
      b.onclick = () => { partsShown += PARTS_PAGE; draw(); };
      more.append(b);
    } else if (!hits.length) {
      more.append(el("p", "mb-0 text-body-secondary",
        "Nothing matches. Every word has to appear somewhere in the part — try fewer."));
    }
  }

  let timer = null;
  search.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { partsQuery = search.value; partsShown = PARTS_PAGE; draw(); }, 120);
  };
  shopSel.onchange = () => { partsShop = shopSel.value; partsShown = PARTS_PAGE; draw(); };
  catSel.onchange = () => { partsCat = catSel.value; partsShown = PARTS_PAGE; draw(); };
  sortSel.onchange = () => { partsSort = sortSel.value; partsShown = PARTS_PAGE; draw(); };
  clear.onclick = () => {
    partsQuery = partsShop = partsCat = ""; partsSort = "name"; partsShown = PARTS_PAGE;
    search.value = ""; shopSel.value = ""; catSel.value = ""; sortSel.value = "name";
    draw();
  };

  draw();
}
