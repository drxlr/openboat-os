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
    "One address, pasted once into ChatGPT, Claude or Grok, and the assistant can read " +
    "this boat, file a fault against it, and suggest things for its inbox."));

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
    "Whoever holds this link can read everything on this boat through an assistant and " +
    "file tasks against it, so it goes into the assistant\u2019s connector screen and " +
    "nowhere else — not a chat, not a note, not an email. If it ever leaks, the server " +
    "can issue a new one and this one dies.\n\n" +
    "One address per assistant, not one address shared between them: the server keeps a " +
    "labelled list, and a separate key is what lets you take one back without breaking " +
    "the others."));
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

  /* one platform at a time
     
     Three assistants now speak MCP and each one buries the custom-connector screen
     somewhere different. Side by side, two of them were a column of steps for a product
     the reader is not using — and the steps are near-identical prose, so the eye skims and
     pastes the address into the wrong box. Tabs: pick your assistant, read four lines. */
  const HOW = {
    "ChatGPT": {
      where: "chatgpt.com → Settings → Connectors (Apps & Connectors on some accounts)",
      note: "Custom connectors need Developer mode switched on in Settings first. " +
            "Available on paid plans; the free tier has no custom connectors.",
      steps: [
        "Settings → Connectors → Create.",
        "Name it after the boat, paste the address as the MCP server URL, and set " +
        "authentication to \u201cNone\u201d \u2014 the key is already in the address.",
        "Create it, then in a chat enable the connector under Tools and ask " +
        "\u201cwhat is open on the boat?\u201d",
        "When the server gains a new tool, open the connector\u2019s Manage page and press " +
        "Refresh \u2014 ChatGPT caches the tool list and will not see it otherwise.",
      ],
    },
    "Claude": {
      where: "claude.ai → Settings → Connectors",
      note: "Also works in Claude Desktop and Claude Code. Custom connectors are on the " +
            "paid plans.",
      steps: [
        "Settings → Connectors → Add custom connector.",
        "Name it after the boat and paste the address. Leave the OAuth client fields " +
        "empty \u2014 there is no OAuth here.",
        "Add it, then enable it in a chat from the tools menu and ask the same question.",
        "Claude re-reads the tool list when the connector reconnects; toggling it off and " +
        "on is enough after the server changes.",
      ],
    },
    "Grok": {
      where: "grok.com → Connectors → New Connector → Custom",
      note: "xAI calls this \u201cBring Your Own MCP\u201d. The server must be reachable " +
            "on the public internet, which this one is. Grok Build also takes local " +
            "stdio servers through ~/.grok/config.toml; this address is not one of those.",
      steps: [
        "grok.com → Connectors → New Connector → Custom.",
        "Paste the address as the server URL. There is nowhere to put a header and " +
        "nothing to put in one \u2014 the key is in the address.",
        "Save, then enable it in a chat and ask \u201cwhat is open on the boat?\u201d",
        "On the xAI API the same URL goes in as a remote MCP tool, on Grok 4.3 or later.",
      ],
    },
  };

  const box = el("div", "card mb-3");
  const boxBody = el("div", "card-body");
  box.append(boxBody);
  let which = "ChatGPT";
  const drawHow = () => {
    boxBody.textContent = "";
    boxBody.append(tabs(Object.keys(HOW), which, n => { which = n; drawHow(); }));
    const h = HOW[which];
    boxBody.append(el("p", "text-body-secondary mb-3", h.where));
    const ol = el("ol", "mb-3 ps-3");
    h.steps.forEach(l => ol.append(el("li", "mb-2", l)));
    boxBody.append(ol);
    boxBody.append(note("info", h.note));
  };
  drawHow();
  page.append(box);

  /* what it can do, and what it cannot */
  const can = pane("What the assistant can do", "Read, and two kinds of write");
  const cb = el("div", "card-body");
  const ul = el("ul", "mb-3");
  [
    "Read the boat’s papers and quote them with the line they came from.",
    "List faults with their status, photographs and history, and the service that is due.",
    "Look at a fault’s photographs and read what is on them.",
    "Give a marine forecast and a passage window for the boat’s own limits.",
    "Say what could be bought for the boat, and what is not yet known about each item.",
    "File a new fault or idea on the task list, in the owner\u2019s words.",
    "Put a link or a fetched PDF into the Inbox — and nothing becomes one of the " +
    "boat’s documents until somebody accepts it there.",
  ].forEach(t => ul.append(el("li", "", t)));
  cb.append(ul);
  cb.append(el("p", "mb-0 text-body-secondary",
    "It can only ever add. It cannot close a fault, change its status, reassign it, " +
    "rename it, edit a word anybody else wrote, delete anything, change the profile, or " +
    "reach another boat. Everything it writes carries the assistant\u2019s name and is " +
    "marked unverified, and a fetched document stays in the Inbox until a person accepts " +
    "it."));
  can.append(cb);
  page.append(can);
}
