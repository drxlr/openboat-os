/* OpenBoat console — the account page. Loaded by console.html after the shared furniture;
   it relies on the globals defined there (el, api, gatePost, pane, note, table, kv…).

   Everything on this page is about the *person*, not the boat: their name, their password,
   the devices they are still signed in on, and — for an admin — who else has a login here
   at all. The boat key in the address is only where the console lives. No boat fact is
   written into this file; every name on screen arrives from the gate.

   The one write helper it uses is `gatePost()`, which posts JSON to the gate and nothing
   else. The boat's own server is never a target from here. */

/* ── view: account ─────────────────────────────────────────────────────────────────── */
async function viewAccount() {
  const page = el("div");
  mount(page);
  page.append(pageHead("Your account",
    "Your name, your password, and the devices this login is still open on."));

  if (!ROOT) {
    page.append(note("info",
      "Accounts live on the login site, not on this page. Served straight off the boat's " +
      "own machine there is nobody signed in and nothing to change — open the same " +
      "console through the login and this page works."));
    return;
  }

  const me = await api("/me");
  if (me.error) { page.append(note("error", me.error)); return; }
  window.OB_USER = me;

  page.append(accountWho(me));
  page.append(accountPassword());
  page.append(accountDevices());

  if ((me.role || "") === "admin") {
    const slot = el("div");
    page.append(slot);
    slot.append(note("loading", "Reading the people on this gate…"));
    const data = await api("/account/users");
    slot.textContent = "";
    if (data.error) slot.append(note("error", data.error));
    else accountPeople(slot, data);
  }
}

/* A slot for the one-line answer a form gets back, cleared before every attempt so an
   old "Saved." never sits above a new failure. */
function accountFlash(box, kind, text) {
  box.textContent = "";
  if (text) box.append(note(kind, text));
}

/* ── who you are, and the name on it ───────────────────────────────────────────────── */
function accountWho(me) {
  const card = pane("Who you are", ROLE_SIDE[me.role] || me.role || "");
  const body = el("div", "card-body");

  const boats = (me.boats || []).map(b => b.name || b.key);
  body.append(kv([
    ["Name", me.name || "—"],
    ["Email", me.email || "—"],
    ["Role", me.role || "crew"],
    ["Boats", boats.length ? boats.join(", ") : "none yet"],
  ], null));

  body.append(el("hr", "my-3"));

  const flash = el("div");
  const form = el("form", "row g-2 align-items-end");
  const col = el("div", "col-sm");
  const label = el("label", "form-label", "Display name");
  const input = el("input", "form-control");
  input.type = "text";
  input.id = "acc-name";
  input.maxLength = 60;
  input.value = me.name || "";
  input.autocomplete = "name";
  label.htmlFor = input.id;
  col.append(label, input,
    el("div", "form-text", "The name that goes on every fault you file. Up to 60 characters."));
  const act = el("div", "col-sm-auto");
  const save = el("button", "btn btn-primary", "Save");
  save.type = "submit";
  act.append(save);
  form.append(col, act);

  form.onsubmit = async e => {
    e.preventDefault();
    const name = input.value.trim();
    accountFlash(flash, null, "");
    if (!name) { input.classList.add("is-invalid"); input.focus(); return; }
    input.classList.remove("is-invalid");
    save.disabled = true;
    const r = await gatePost("/account/name", { name });
    save.disabled = false;
    if (r.error) { accountFlash(flash, "error", r.error); return; }
    accountFlash(flash, "ok", "Saved.");
    accountRename(name);
  };

  body.append(form, flash);
  card.append(body);
  return card;
}

const ROLE_SIDE = { admin: "Every boat on this gate", owner: "Your boats",
                    crew: "Your boats" };

/* The name is printed in two other places the moment the page loaded. Rewriting them here
   is cheaper than a reload and honest: nothing else about the session changed. */
function accountRename(name) {
  if (window.OB_USER) window.OB_USER.name = name;
  const who = $("#who a") || $("#who");
  if (who) { who.textContent = ""; who.append(el("i", "bi bi-person me-1"));
             who.append(document.createTextNode(name)); }
  const face = $("#gate .ob-face");
  if (face) face.textContent = (name.trim()[0] || "?").toUpperCase();
  const first = $("#gate .ob-me span:last-child");
  if (first) first.textContent = name.split(/\s+/)[0] || "";
}

/* ── the password ──────────────────────────────────────────────────────────────────── */
function accountPassword() {
  const card = pane("Password", "Ten characters or more");
  const body = el("div", "card-body");
  body.append(el("p", "text-body-secondary",
    "Changing it here does not sign your other devices out. That is the button below, " +
    "and it is deliberately a separate thing to decide."));

  const flash = el("div");
  const form = el("form");

  const field = (id, label, hint, auto) => {
    const wrap = el("div", "mb-3");
    const lb = el("label", "form-label", label);
    const input = el("input", "form-control");
    input.type = "password";
    input.id = id;
    input.autocomplete = auto;
    lb.htmlFor = id;
    wrap.append(lb, input);
    if (hint) wrap.append(el("div", "form-text", hint));
    form.append(wrap);
    return input;
  };

  const current = field("acc-pw-now", "Current password", null, "current-password");
  const fresh = field("acc-pw-new", "New password", "Ten characters or more.", "new-password");
  const again = field("acc-pw-again", "And again", null, "new-password");
  fresh.minLength = again.minLength = 10;

  const save = el("button", "btn btn-primary", "Change it");
  save.type = "submit";
  form.append(save);

  form.onsubmit = async e => {
    e.preventDefault();
    accountFlash(flash, null, "");
    [current, fresh, again].forEach(i => i.classList.remove("is-invalid"));
    if (fresh.value.length < 10) {
      fresh.classList.add("is-invalid");
      accountFlash(flash, "warn", "Ten characters or more, please.");
      fresh.focus();
      return;
    }
    if (fresh.value !== again.value) {
      again.classList.add("is-invalid");
      accountFlash(flash, "warn", "The two did not match.");
      again.focus();
      return;
    }
    save.disabled = true;
    const r = await gatePost("/account/password",
                             { current: current.value, password: fresh.value });
    save.disabled = false;
    if (r.error) {
      accountFlash(flash, "error", r.error);
      current.classList.add("is-invalid");
      current.focus();
      return;
    }
    current.value = fresh.value = again.value = "";
    accountFlash(flash, "ok", "Changed. This browser stays signed in.");
  };

  body.append(form, flash);
  card.append(body);
  return card;
}

/* ── the other devices ─────────────────────────────────────────────────────────────── */
function accountDevices() {
  const card = pane("Other devices", "A phone in a locker counts");
  const body = el("div", "card-body");
  body.append(el("p", "mb-3",
    "Signing out everywhere else ends every other session on this account — another " +
    "browser, an old laptop, a phone that was lent to somebody. This browser stays " +
    "signed in. There is nothing to wait for: the next click on any of them lands on " +
    "the login page."));

  const flash = el("div");
  const go = el("button", "btn btn-outline-primary", "Sign out everywhere else");
  go.type = "button";
  go.onclick = async () => {
    accountFlash(flash, null, "");
    go.disabled = true;
    const r = await gatePost("/account/logout-all", {});
    go.disabled = false;
    if (r.error) { accountFlash(flash, "error", r.error); return; }
    accountFlash(flash, "ok", "Done. Every other device is signed out.");
  };

  body.append(go, el("div", "mt-3"), flash);
  card.append(body);
  return card;
}

/* ── the people on this gate, for an admin ─────────────────────────────────────────── */
/* `carry` is what a redraw must not lose: the sentence a form just earned, and the invite
   link it just made. Every mutation re-reads the list rather than patching a row — the
   users file is also edited from a command line — and a redraw that threw the link away
   would throw away the only copy of it. */
function accountPeople(slot, data, carry) {
  carry = carry || {};
  const boats = data.boats || [];
  const reload = async next => {
    const again = await api("/account/users");
    slot.textContent = "";
    if (again.error) { slot.append(note("error", again.error)); return; }
    accountPeople(slot, again, next || {});
  };

  const flash = el("div");
  slot.append(flash);
  if (carry.message) accountFlash(flash, carry.message[0], carry.message[1]);
  if (carry.link) flash.append(accountLinkCard(carry.link.answer, carry.link.who));

  /* the table */
  const card = pane("People", `${(data.users || []).length} with a login`);
  const boatName = key => (boats.find(b => b.key === key) || {}).name || key;

  const cols = [
    { label: "Person", w: "26%", get: p => {
        const box = el("div");
        box.append(el("div", "fw-medium", p.name || p.email));
        box.append(el("div", "small text-body-secondary text-break", p.email));
        return box;
      } },
    { label: "Role", w: "10%", get: p => p.role },
    { label: "Boats", w: "20%", get: p => (p.boats || []).map(boatName).join(", ") || "—" },
    { label: "Password", w: "14%", get: p => p.password
        ? statusCell("ok", "set")
        : statusCell("warn", "invited, no password yet") },
    { label: "Created", w: "10%", prio: "low", get: p => p.created || "—" },
    { label: "", w: "20%", cls: "text-end", get: p => accountRowActions(p, data, reload, flash) },
  ];
  card.append(table(cols, data.users || []));
  slot.append(card);

  /* inviting somebody */
  slot.append(accountInviteCard(data, reload));
}

function accountRowActions(person, data, reload, flash) {
  const box = el("div", "d-flex flex-wrap gap-1 justify-content-end");

  const boatsBtn = el("button", "btn btn-outline-secondary btn-sm", "Boats");
  boatsBtn.type = "button";
  boatsBtn.onclick = () => accountBoatsModal(person, data, reload);
  box.append(boatsBtn);

  if (!person.password) {
    const again = el("button", "btn btn-outline-secondary btn-sm", "Re-issue link");
    again.type = "button";
    again.onclick = async () => {
      again.disabled = true;
      const r = await gatePost("/account/reissue", { email: person.email });
      again.disabled = false;
      if (r.error) { accountFlash(flash, "error", r.error); return; }
      flash.textContent = "";
      flash.append(accountLinkCard(r, person.name || person.email));
      flash.scrollIntoView({ block: "nearest" });
    };
    box.append(again);
  }

  if (person.email !== data.you) {
    const kill = el("button", "btn btn-outline-danger btn-sm", "Revoke");
    kill.type = "button";
    kill.onclick = () => accountRevokeModal(person, reload);
    box.append(kill);
  }
  return box;
}

/* ── the invite form ───────────────────────────────────────────────────────────────── */
function accountInviteCard(data, reload) {
  const card = pane("Invite somebody", "They choose their own password");
  const body = el("div", "card-body");
  body.append(el("p", "text-body-secondary",
    "No password is set here and none is sent. What comes back is a link, good for a " +
    "week, on which they choose their own — so hand them the link and nothing else."));

  const flash = el("div");
  const form = el("form", "row g-3");

  const emailCol = el("div", "col-md-5");
  const emailLb = el("label", "form-label", "Email");
  const email = el("input", "form-control");
  email.type = "email"; email.id = "acc-inv-email"; email.required = true;
  email.autocomplete = "off";
  emailLb.htmlFor = email.id;
  emailCol.append(emailLb, email);

  const nameCol = el("div", "col-md-4");
  const nameLb = el("label", "form-label", "Name");
  const name = el("input", "form-control");
  name.type = "text"; name.id = "acc-inv-name"; name.maxLength = 60;
  name.autocomplete = "off";
  nameLb.htmlFor = name.id;
  nameCol.append(nameLb, name);

  const roleCol = el("div", "col-md-3");
  const roleLb = el("label", "form-label", "Role");
  const role = el("select", "form-select");
  role.id = "acc-inv-role";
  (data.roles || ["crew"]).forEach(r => {
    const o = el("option", "", r);
    o.value = r;
    if (r === "crew") o.selected = true;
    role.append(o);
  });
  roleLb.htmlFor = role.id;
  roleCol.append(roleLb, role);

  const boatsCol = el("div", "col-12");
  boatsCol.append(el("div", "form-label", "Boats"));
  const picks = accountBoatChecks(data.boats || [], [], "inv");
  boatsCol.append(picks.node);
  boatsCol.append(el("div", "form-text",
    "An admin sees every boat whatever is ticked here; for an owner or crew this is the list."));

  const actCol = el("div", "col-12");
  const send = el("button", "btn btn-primary", "Make a link");
  send.type = "submit";
  actCol.append(send);

  form.append(emailCol, nameCol, roleCol, boatsCol, actCol);

  form.onsubmit = async e => {
    e.preventDefault();
    accountFlash(flash, null, "");
    const address = email.value.trim().toLowerCase();
    if (!address || address.indexOf("@") < 0) {
      email.classList.add("is-invalid"); email.focus(); return;
    }
    email.classList.remove("is-invalid");
    send.disabled = true;
    const r = await gatePost("/account/invite", {
      email: address, name: name.value.trim(), role: role.value, boats: picks.chosen(),
    });
    send.disabled = false;
    if (r.error) { accountFlash(flash, "error", r.error); return; }
    email.value = name.value = "";
    picks.clear();
    reload({ link: { answer: r, who: r.name || address } });
  };

  body.append(form, el("div", "mt-3"), flash);
  card.append(body);
  return card;
}

/* A row of boat checkboxes. `chosen()` reads them, `clear()` empties them. */
function accountBoatChecks(boats, ticked, tag) {
  const node = el("div", "d-flex flex-wrap gap-3");
  const inputs = [];
  if (!boats.length) node.append(el("div", "text-body-secondary small",
                                   "This gate has no boats configured."));
  boats.forEach((b, i) => {
    const wrap = el("div", "form-check");
    const box = el("input", "form-check-input");
    box.type = "checkbox";
    box.id = `acc-boat-${tag}-${i}`;
    box.value = b.key;
    box.checked = (ticked || []).indexOf(b.key) >= 0;
    const lb = el("label", "form-check-label", b.name || b.key);
    lb.htmlFor = box.id;
    wrap.append(box, lb);
    node.append(wrap);
    inputs.push(box);
  });
  return { node,
           chosen: () => inputs.filter(i => i.checked).map(i => i.value),
           clear: () => inputs.forEach(i => { i.checked = false; }) };
}

/* ── the link, once it exists ──────────────────────────────────────────────────────── */
function accountLinkCard(answer, who) {
  const card = pane("The link for " + who, `Good for ${answer.days || 7} days`);
  const body = el("div", "card-body");
  body.append(el("p", "",
    "Whoever opens this sets the password on that account, so it goes to that person and " +
    "nobody else. It stops working once they have used it, or after it runs out."));

  const group = el("div", "input-group mb-2");
  const field = el("input", "form-control font-monospace");
  field.type = "text";
  field.readOnly = true;
  field.value = answer.url || "";
  field.setAttribute("aria-label", "Invite link");
  field.onclick = () => field.select();
  const copy = el("button", "btn btn-outline-primary");
  copy.type = "button";
  copy.innerHTML = '<i class="bi bi-clipboard me-1"></i>Copy';
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(field.value); copy.textContent = "Copied"; }
    catch (e) { field.select(); copy.textContent = "Select and copy"; }
    setTimeout(() => { copy.innerHTML = '<i class="bi bi-clipboard me-1"></i>Copy'; }, 2500);
  };
  group.append(field, copy);
  body.append(group);

  const text = "Here is your link to the boat console. Open it and choose a password — "
             + "it is good for " + (answer.days || 7) + " days.\n\n" + (answer.url || "");
  const wa = el("a", "btn btn-outline-secondary btn-sm");
  wa.href = "https://wa.me/?text=" + encodeURIComponent(text);
  wa.target = "_blank";
  wa.rel = "noopener noreferrer";
  wa.innerHTML = '<i class="bi bi-whatsapp me-1"></i>Send on WhatsApp';
  body.append(wa);

  card.append(body);
  return card;
}

/* ── which boats one person may see ────────────────────────────────────────────────── */
function accountBoatsModal(person, data, reload) {
  const m = el("div", "modal fade");
  m.tabIndex = -1;
  const dlg = el("div", "modal-dialog modal-dialog-centered modal-fullscreen-sm-down");
  const box = el("div", "modal-content");

  const head = el("div", "modal-header");
  head.append(el("h2", "modal-title h5 mb-0", "Boats for " + (person.name || person.email)));
  const x = el("button", "btn-close");
  x.type = "button";
  x.setAttribute("data-bs-dismiss", "modal");
  x.setAttribute("aria-label", "Close");
  head.append(x);

  const body = el("div", "modal-body");
  const errBox = el("div", "alert alert-danger d-none");
  errBox.setAttribute("role", "alert");
  body.append(errBox);
  if (person.role === "admin")
    body.append(el("p", "small text-body-secondary",
      "This person is an admin, so they see every boat on this gate whatever is ticked. " +
      "The list still matters if their role is ever changed."));
  const picks = accountBoatChecks(data.boats || [], person.boats || [], "edit");
  body.append(picks.node);

  const foot = el("div", "modal-footer");
  const cancel = el("button", "btn btn-outline-secondary", "Cancel");
  cancel.type = "button";
  cancel.setAttribute("data-bs-dismiss", "modal");
  const save = el("button", "btn btn-primary", "Save");
  save.type = "button";
  foot.append(cancel, save);

  save.onclick = async () => {
    errBox.classList.add("d-none");
    save.disabled = cancel.disabled = true;
    const r = await gatePost("/account/boats",
                             { email: person.email, boats: picks.chosen() });
    if (r.error) {
      save.disabled = cancel.disabled = false;
      errBox.textContent = r.error;
      errBox.classList.remove("d-none");
      return;
    }
    const inst = bootstrap.Modal.getInstance(m);
    if (inst) inst.hide(); else m.remove();
    reload({ message: ["ok", "Saved. It takes effect on their next click."] });
  };

  box.append(head, body, foot);
  dlg.append(box);
  m.append(dlg);
  document.body.append(m);
  m.addEventListener("hidden.bs.modal", () => m.remove());
  new bootstrap.Modal(m).show();
}

/* ── taking a login away ───────────────────────────────────────────────────────────── */
function accountRevokeModal(person, reload) {
  const m = el("div", "modal fade");
  m.tabIndex = -1;
  const dlg = el("div", "modal-dialog modal-dialog-centered");
  const box = el("div", "modal-content");

  const head = el("div", "modal-header");
  head.append(el("h2", "modal-title h5 mb-0", "Revoke this login?"));
  const x = el("button", "btn-close");
  x.type = "button";
  x.setAttribute("data-bs-dismiss", "modal");
  x.setAttribute("aria-label", "Close");
  head.append(x);

  const body = el("div", "modal-body");
  const errBox = el("div", "alert alert-danger d-none");
  errBox.setAttribute("role", "alert");
  body.append(errBox);
  body.append(el("p", "mb-2",
    `${person.name || person.email} <${person.email}> loses their login. Whatever they ` +
    "have already filed stays where it is — a fault keeps their name on it."));
  body.append(el("p", "mb-0 text-body-secondary",
    "It takes effect on their next click, not at the next restart. Inviting them again " +
    "later makes a new account, not the old one back."));

  const foot = el("div", "modal-footer");
  const cancel = el("button", "btn btn-outline-secondary", "Keep it");
  cancel.type = "button";
  cancel.setAttribute("data-bs-dismiss", "modal");
  const go = el("button", "btn btn-danger", "Revoke");
  go.type = "button";
  foot.append(cancel, go);

  go.onclick = async () => {
    errBox.classList.add("d-none");
    go.disabled = cancel.disabled = true;
    const r = await gatePost("/account/revoke", { email: person.email });
    if (r.error) {
      go.disabled = cancel.disabled = false;
      errBox.textContent = r.error;
      errBox.classList.remove("d-none");
      return;
    }
    const inst = bootstrap.Modal.getInstance(m);
    if (inst) inst.hide(); else m.remove();
    reload({ message: ["ok", `${person.email} no longer has a login here.`] });
  };

  box.append(head, body, foot);
  dlg.append(box);
  m.append(dlg);
  document.body.append(m);
  m.addEventListener("hidden.bs.modal", () => m.remove());
  new bootstrap.Modal(m).show();
}
