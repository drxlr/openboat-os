/* OpenBoat console — the tasks view. Loaded by console.html after the shared
   furniture; it relies on the globals defined there (state, el, api, table, pane…).
   No boat facts live here: everything on screen arrives from the API. */

/* ── view: tasks ─────────────────────────────────────────────────────────────────── */
async function viewTasks() {
  const v = el("div", "view");
  const list = el("div");
  list.style.cssText = "min-height:0;overflow:hidden;display:grid";
  const detail = el("div", "split");

  const seg = el("div", "seg");
  [["all", "All"], ["open", "Open"], ["snag", "Snags"], ["service", "Service"]]
    .forEach(([id, label]) => {
      const b = el("button", "", label);
      b.setAttribute("aria-pressed", state.filter === id ? "true" : "false");
      b.onclick = () => { state.filter = id; state.sel = null; draw(); };
      seg.append(b);
    });

  v.append(toolbar("Filter what this boat owes…", () => { state.sel = null; draw(); }, [seg]));
  v.append(list);
  v.append(detail);
  mount(v);

  const [maint, snags] = await Promise.all([api("/api/maintenance"), api("/api/snags")]);
  state.maint = maint; state.snags = snags;

  /* One list, because a boat has one. The engine is owed a flush because it ran in salt
     water; the locker is owed a striker plate because somebody stood in front of it. Kept
     apart they are two lists nobody reads. */
  function rows() {
    const out = [];
    (snags.snags || []).forEach(s => out.push({
      kind: "snag", title: s.title, where: s.where, when: s.when, open: s.open,
      status: s.status, by: s.by, body: s.body, photos: s.photos || [],
      updates: s.updates || [], raw: s,
    }));
    (maint.items || []).forEach(m => out.push({
      kind: "service", title: m.item, where: m.description, when: m.last,
      open: m.verdict !== "ok", status: m.verdict, why: m.why, raw: m,
    }));
    const q = state.q.trim().toLowerCase();
    return out.filter(r => {
      if (state.filter === "open" && !r.open) return false;
      if (state.filter === "snag" && r.kind !== "snag") return false;
      if (state.filter === "service" && r.kind !== "service") return false;
      if (!q) return true;
      return JSON.stringify(r).toLowerCase().includes(q);
    });
  }

  function statusOf(r) {
    if (r.kind === "snag")
      return r.open ? statusCell("warn", r.status || "open")
                    : statusCell("ok", r.status || "fixed");
    return { due:     statusCell("bad",  "due"),
             soon:    statusCell("warn", "soon"),
             ok:      statusCell("ok",   "in date"),
             unknown: statusCell("info", "never recorded") }[r.status]
           || statusCell("", r.status);
  }

  function draw() {
    const rs = rows();
    list.textContent = "";
    list.append(table([
      { label: "SOURCE", cls: "k", w: "7em", get: r => r.kind },
      { label: "WHAT", cls: "w", get: r => {
          const s = el("span", "trunc", r.title);
          if (r.updates && r.updates.length) s.title = `${r.updates.length} update(s)`;
          return s; } },
      { label: "WHERE", w: "30%", get: r => {
          const s = el("span", "trunc", r.where || "—"); return s; } },
      { label: "WHEN", cls: "num", w: "8em", get: r => ago(r.when) },
      { label: "STATUS", cls: "st", w: "12em", get: r => statusOf(r) },
    ], rs, (r, i) => { state.sel = i; draw(); show(r); }, state.sel));

    if (!maint.items?.length && maint.engine_hours_source) {
      const e = el("div", "empty");
      e.append(statusCell("info", maint.engine_hours_source));
      list.append(e);
    }
    if (state.sel === null) idle(rs);
  }

  function idle(rs) {
    detail.textContent = "";
    const open = rs.filter(r => r.open).length;
    const p = pane("Everything the boat is owed", "LIST");
    p.classList.add("full");
    const b = el("div", "empty");
    b.innerHTML =
      `<b>${rs.length}</b> item(s), <b>${open}</b> still open.<br><br>` +
      `Snags come from whoever stood in front of the fault with a phone. Service items ` +
      `come from the engine's own running hours. Pick one to read it.<br><br>` +
      `Nothing on this page closes anything: a snag is closed by editing the file, a ` +
      `service by recording that the work actually happened.`;
    p.append(b);
    detail.append(p);
  }

  function show(r) {
    detail.textContent = "";
    const left = pane(r.title, r.kind.toUpperCase(), r.kind === "snag" ? r.when : "");
    const body = el("div", "body");
    if (r.kind === "snag") {
      body.append(kv([
        ["Filed", r.when],
        ["Age", ago(r.when)],
        ["Where on the boat", r.where],
        ["Reported by", r.by || "(unnamed)"],
        ["Status", r.status],
        ["Photographs", r.photos.length || 0],
        ["Updates", r.updates.length || 0],
      ]));
    } else {
      const m = r.raw;
      body.append(kv([
        ["Item", m.item],
        ["What it is", m.description],
        ["Last done", m.last ? `${m.last}  (${ago(m.last)})` : "never recorded"],
        ["Hours since", m.hours_since === null ? "" : m.hours_since?.toFixed(1) + " h"],
        ["Days since", m.days_since],
        ["Outings since", m.outings_since],
        ["Interval", m.interval_hours ? m.interval_hours + " h"
                    : m.interval_months ? m.interval_months + " months"
                    : m.per_outing ? "every outing in salt water" : ""],
        ["Verdict", m.verdict],
      ]));
      const src = el("div", "empty");
      src.append(statusCell("info", maint.engine_hours_source || ""));
      body.append(src);
    }
    left.append(body);
    detail.append(left);

    const right = pane(r.kind === "snag" ? "What was written" : "Why it is owed",
                       r.kind === "snag" ? "NOTE" : "REASON");
    const rb = el("div", "body");
    if (r.kind === "snag") {
      rb.append(code(r.body || "(no note — see the photograph)"));
      if (r.photos.length) {
        const shots = el("div", "shots");
        const base = `${location.protocol}//${location.hostname}:${snags.photo_port}`;
        r.photos.forEach(name => {
          const a = el("a");
          a.href = `${base}/photo?boat=${encodeURIComponent(snags.boat || "")}` +
                   `&name=${encodeURIComponent(name)}`;
          a.target = "_blank"; a.rel = "noopener"; a.title = name;
          const img = el("img");
          img.src = a.href; img.alt = name; img.loading = "lazy";
          /* The photographs live with the write service, not with this one. If it is not
             running, say so rather than showing a broken frame — a missing picture that
             looks like a missing picture is honest; one that looks like no picture was
             ever taken is not. */
          img.onerror = () => {
            a.textContent = "";
            a.append(el("span", "miss", `${name}\nneeds the snag service on :${snags.photo_port}`));
          };
          a.append(img);
          shots.append(a);
        });
        rb.append(shots);
      }
      r.updates.forEach(u => {
        const h = el("div", "empty");
        h.append(statusCell("info", `Update · ${u.when}${u.by ? " · " + u.by : ""}`));
        rb.append(h);
        rb.append(code(u.body || ""));
      });
    } else {
      rb.append(code(r.why || ""));
    }
    right.append(rb);
    detail.append(right);
  }

  draw();
}

