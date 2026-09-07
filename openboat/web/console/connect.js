/* OpenBoat console — connecting an assistant. Loaded by console.html after the shared
   furniture; it relies on the globals defined there (state, el, api, pane, note…).
   No boat facts live here: the address on screen arrives from the gate, and only for a
   person the gate has decided may hand this boat to an assistant. */

/* ── view: MCP connect ─────────────────────────────────────────────────────────────
   ChatGPT and Claude reach a boat through one URL, and the URL is the key: whoever has
   it can read this boat's papers, faults, photographs and inbox, and file into the inbox.
   So the page says that before it shows the address, and shows it only behind the login. */
async function viewConnect() {
  const page = el("div");
  mount(page);
  page.append(pageHead("Connect an assistant",
    "One address, pasted once into ChatGPT or Claude, and the assistant can read this " +
    "boat and suggest things for its inbox."));

  if (!ROOT) {
    page.append(note("info",
      "The address is handed out by the login site, not by this local page. Sign in " +
      "there as an owner of the boat and open the same entry."));
    return;
  }

  const c = await api("/mcp-connect");
  if (c.error || c.status === 404) {
    page.append(note("info",
      "Only an owner of this boat can connect an assistant to it. Ask them to open this " +
      "page and send you nothing — the address is the key, and it is theirs to give."));
    return;
  }
  if (!c.url) {
    page.append(note("warn",
      "No assistant address is configured for this boat yet. Whoever runs the server " +
      "sets OPENBOAT_MCP_CONNECT for it; the runbook says how."));
    return;
  }

  /* the address */
  const key = pane("The address", "Keep it like a key");
  const kb = el("div", "card-body");
  kb.append(el("p", "mb-3",
    "Whoever holds this link can read everything on this boat through an assistant, " +
    "so it goes into ChatGPT or Claude and nowhere else — not a chat, not a note, not " +
    "an email. If it ever leaks, the server can issue a new one and this one dies."));
  const group = el("div", "input-group input-group-lg mb-2");
  const field = el("input", "form-control font-monospace");
  field.type = "text"; field.readOnly = true; field.value = c.url;
  field.setAttribute("aria-label", "Assistant address");
  field.onclick = () => field.select();
  const copy = el("button", "btn btn-outline-primary");
  copy.type = "button";
  copy.innerHTML = '<i class="bi bi-clipboard me-1"></i>Copy';
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(c.url); copy.textContent = "Copied"; }
    catch (e) { field.select(); copy.textContent = "Select and copy"; }
    setTimeout(() => { copy.innerHTML = '<i class="bi bi-clipboard me-1"></i>Copy'; }, 2500);
  };
  group.append(field, copy);
  kb.append(group);
  kb.append(el("div", "form-text",
    `For ${c.name || c.boat}. It answers as an MCP server over streamable HTTP; ` +
    "the older event-stream transport is not offered."));
  key.append(kb);
  page.append(key);

  /* the two assistants, side by side */
  const row = el("div", "row g-3 mb-3");
  const steps = (title, icon, lines) => {
    const col = el("div", "col-lg-6");
    const card = el("div", "card h-100");
    card.append(cardHead(title));
    const body = el("div", "card-body");
    const ol = el("ol", "mb-0 ps-3");
    lines.forEach(l => ol.append(el("li", "mb-2", l)));
    body.append(ol);
    card.append(body);
    col.append(card);
    return col;
  };
  row.append(steps("ChatGPT", "bi-chat", [
    "Settings → Connectors (or Apps & Connectors) → Create. Developer mode has to be on " +
    "for custom connectors.",
    "Name it after the boat, paste the address as the MCP server URL, authentication " +
    "“None” — the key is in the address.",
    "Create. In a chat, enable the connector under Tools, then ask: “what is open on " +
    "the boat?”",
    "After the server gains tools, the connector’s Manage page has a Refresh that " +
    "picks them up.",
  ]));
  row.append(steps("Claude", "bi-stars", [
    "Settings → Connectors → Add custom connector.",
    "Name it after the boat and paste the address. No OAuth: leave the client fields empty.",
    "Add. Enable it in a chat under the tools menu, then ask the same question.",
  ]));
  page.append(row);

  /* what it can do, and what it cannot */
  const can = pane("What the assistant can do", "Read, and one kind of write");
  const cb = el("div", "card-body");
  const ul = el("ul", "mb-3");
  [
    "Read the boat’s papers and quote them with the line they came from.",
    "List faults with their status, photographs and history, and the service that is due.",
    "Look at a fault’s photographs and read what is on them.",
    "Give a marine forecast and a passage window for the boat’s own limits.",
    "Put a link or a fetched PDF into the Inbox — and nothing becomes one of the " +
    "boat’s documents until somebody accepts it there.",
  ].forEach(t => ul.append(el("li", "", t)));
  cb.append(ul);
  cb.append(el("p", "mb-0 text-body-secondary",
    "It cannot change a fault, delete anything, edit the profile, or reach another boat. " +
    "Everything it writes is signed with the assistant’s name and lands in the Inbox " +
    "for a person to decide on."));
  can.append(cb);
  page.append(can);
}
