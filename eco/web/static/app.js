// eco web client -- talks web_frontend/PROTOCOL.md (v0) to eco.web.gateway.
// Plain ES module, no build step, no dependencies. DOM is built with
// textContent only (never innerHTML from server data); SVG panels are
// sanitized before insertion (sanitizeSvg).

const VERSION = "0.1";
const STALE_AFTER = 35; // s without a fresh read -> greyed (monitored items are re-read at least every 10 s)
const SHOW_AGE_AFTER = 12; // s
const SVG_REFRESH = 3000; // ms
const PING_EVERY = 15000; // ms -- a dropped network sends no close frame; only a missing reply shows it
const PING_TIMEOUT = 8000; // ms
const DOT = { waiting: "var(--wait)", live: "var(--ok)", subscribed: "var(--ok)", polled: "var(--polled)",
  busy: "var(--busy)", moving: "var(--busy)", error: "var(--err)" };

const S = {
  ws: null, nextId: 0, pending: new Map(), hello: null, retry: 0, retryTimer: null,
  offset: 0,           // client clock - gateway clock, s
  view: null,          // current view: {kind, name, subscribed: [], rows: Map, timers: []}
  filter: "",
};

// ---- small DOM helpers ------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const main = () => document.getElementById("main");

function setMain(...children) {
  main().replaceChildren(...children);
}

function toast(message, isError = false) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.className = isError ? "err" : "";
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (box.hidden = true), isError ? 6000 : 2500);
}

function banner(message) {
  const box = document.getElementById("banner");
  box.textContent = message || "";
  box.hidden = !message;
}

function setTitle(text, withBack) {
  document.getElementById("title").textContent = text;
  document.title = text === "eco" ? "eco" : `${text} – eco`;
  document.getElementById("back").hidden = !withBack;
}

function setConn(state) {
  const dot = document.getElementById("conn");
  dot.className = "conn " + state;
  dot.title = { up: "connected", down: "disconnected", "": "connecting" }[state] ?? state;
}

function clientId() {
  try {
    let id = localStorage.getItem("eco-web-client");
    if (!id) {
      id = Math.random().toString(36).slice(2, 10);
      localStorage.setItem("eco-web-client", id);
    }
    return id;
  } catch {
    return "anon";
  }
}

function formatNum(n) {
  if (Number.isInteger(n)) return String(n);
  const a = Math.abs(n);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return n.toExponential(4);
  return String(+n.toPrecision(7));
}

// ---- connection ------------------------------------------------------------------------

function connect() {
  clearTimeout(S.retryTimer);
  setConn("");
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  S.ws = ws;
  ws.onopen = async () => {
    S.retry = 0;
    setConn("up");
    banner("");
    clearInterval(S.pingTimer);
    S.pingTimer = setInterval(() => {
      if (S.ws !== ws) return clearInterval(S.pingTimer);
      const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("ping timeout")), PING_TIMEOUT));
      Promise.race([request("ping"), timeout]).catch(() => {
        lost(ws, { code: 0 }); // at once: close() on a dead connection can take minutes to report
        ws.close();
      });
    }, PING_EVERY);
    try {
      S.hello = await request("hello", { client_version: VERSION, client_id: clientId() });
    } catch (e) {
      banner(`hello failed: ${e.message}`);
      return;
    }
    const mode = document.getElementById("mode");
    mode.textContent = S.hello.writes ? "writes on" : "read-only";
    mode.className = "badge" + (S.hello.writes ? " writes" : "");
    route();
  };
  ws.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if ("re" in message) {
      const waiter = S.pending.get(message.re);
      if (!waiter) return;
      S.pending.delete(message.re);
      if (message.ok) waiter.resolve(message.data);
      else waiter.reject(Object.assign(new Error(message.error?.message || "error"), { type: message.error?.type }));
    } else if (message.ev) {
      onEvent(message);
    }
  };
  ws.onclose = (event) => lost(ws, event);
}

function lost(ws, event) {
  if (S.ws !== ws) return;
  S.ws = null;
  clearInterval(S.pingTimer);
  setConn("down");
  for (const waiter of S.pending.values()) waiter.reject(new Error("disconnected"));
  S.pending.clear();
  if (event.code === 4403) {
    banner("Not authorized – open the URL with ?token=… printed by eco.web.start_web() once more.");
    return;
  }
  banner("Disconnected – reconnecting…");
  const delay = Math.min(10, 2 ** S.retry++) * 1000;
  S.retryTimer = setTimeout(connect, delay);
}

function request(op, args = {}) {
  return new Promise((resolve, reject) => {
    if (!S.ws || S.ws.readyState !== WebSocket.OPEN) return reject(new Error("not connected"));
    const id = ++S.nextId;
    S.pending.set(id, { resolve, reject });
    S.ws.send(JSON.stringify({ v: 0, id, op, args }));
  });
}

document.addEventListener("visibilitychange", () => {
  // phones suspend background tabs/apps: reconnect at once when shown again
  if (document.visibilityState === "visible" && (!S.ws || S.ws.readyState > WebSocket.OPEN)) {
    S.retry = 0;
    connect();
  }
});

// ---- events -------------------------------------------------------------------------------

function onEvent(message) {
  if (message.ev === "values") {
    if (typeof message.now === "number") S.offset = Date.now() / 1000 - message.now;
    for (const [name, value] of Object.entries(message.data)) applyValue(name, value);
  } else if (message.ev === "write") {
    applyWrite(message.data);
  }
}

function serverNow() {
  return Date.now() / 1000 - S.offset;
}

// ---- routing ---------------------------------------------------------------------------------

function leaveView() {
  const view = S.view;
  S.view = null;
  if (!view) return;
  view.timers.forEach(clearInterval);
  if (view.subscribed.length) request("unsubscribe", { names: view.subscribed }).catch(() => {});
}

function route() {
  leaveView();
  if (!S.hello) return;
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith("/d/")) showDevice(hash.slice(3));
  else if (hash.startsWith("/svg/")) showSvg(hash.slice(5));
  else showHome();
}

window.addEventListener("hashchange", route);
document.getElementById("back").addEventListener("click", () => {
  if (history.length > 1) history.back();
  else location.hash = "";
});

function newView(kind, name) {
  S.view = { kind, name, subscribed: [], rows: new Map(), timers: [] };
  return S.view;
}

// ---- home: the namespace -----------------------------------------------------------------

async function showHome() {
  const view = newView("home", "");
  setTitle(S.hello.label || "eco", false);
  setMain(el("div", { class: "loading", text: "loading names…" }));
  let names;
  try {
    names = await request("names");
  } catch (e) {
    if (S.view === view) setMain(el("div", { class: "empty", text: `could not list names: ${e.message}` }));
    return;
  }
  if (S.view !== view) return;

  const sections = el("div");
  const render = () => {
    const f = S.filter.toLowerCase();
    const match = (n) => !f || n.toLowerCase().includes(f);
    const item = (name, cls, note) =>
      el("a", { class: `item ${cls}`, href: `#/d/${encodeURIComponent(name)}` },
        el("span", { class: "name", text: name }), note ? el("span", { class: "note", text: note }) : null);
    const block = (title, list) => (list.length ? [el("div", { class: "section-title", text: title }), el("div", { class: "list" }, list)] : []);
    const failed = Object.entries(names.failed).filter(([n]) => match(n));
    sections.replaceChildren(
      ...block("Initialized", names.initialized.filter(match).map((n) => item(n, "", ""))),
      ...block("Not initialized – tap to initialize", names.lazy.filter(match).map((n) => item(n, "lazy", "lazy"))),
      ...block("Failed", failed.map(([n, reason]) => item(n, "failed", reason.slice(0, 80)))),
    );
    if (!sections.childElementCount) sections.append(el("div", { class: "empty", text: "nothing matches" }));
  };
  const search = el("input", { class: "search", type: "search", placeholder: "filter components", value: S.filter,
    autocapitalize: "off", autocomplete: "off", spellcheck: "false",
    oninput: (e) => { S.filter = e.target.value; render(); } });
  render();
  setMain(search, sections);
}

// ---- device: an assembly or a single item -----------------------------------------------------

async function showDevice(name) {
  const view = newView("device", name);
  setTitle(name, true);
  setMain(el("div", { class: "loading", text: `opening ${name}… (initializing it if needed)` }));
  let desc;
  try {
    desc = await request("describe", { name });
  } catch (e) {
    if (S.view === view) setMain(el("div", { class: "empty", text: `${name}: ${e.message}` }));
    return;
  }
  if (S.view !== view) return;

  const toolbar = el("div", { class: "toolbar" });
  if (desc.svg) toolbar.append(el("a", { class: "btn primary", href: `#/svg/${encodeURIComponent(name)}`, style: "display:inline-flex;align-items:center;text-decoration:none" }, "Panel"));
  const visible = desc.rows.filter((r) => !r.hidden);
  const hidden = desc.rows.filter((r) => r.hidden);
  const list = el("div", { class: "list" }, visible.map((r) => buildRow(view, r)));
  const parts = [];
  if (toolbar.childElementCount) parts.push(toolbar);
  if (desc.doc) parts.push(el("p", { class: "doc", text: desc.doc }));
  parts.push(visible.length ? list : el("div", { class: "empty", text: "no display items" }));
  if (hidden.length) {
    const hiddenList = el("div", { class: "list" });
    const details = el("details", { class: "hidden-items" }, el("summary", { text: `${hidden.length} more items` }), hiddenList);
    details.addEventListener("toggle", () => {
      if (details.open && !hiddenList.childElementCount) {
        hiddenList.append(...hidden.map((r) => buildRow(view, r)));
        subscribe(view, hidden);
      }
    });
    parts.push(details);
  }
  setMain(...parts);
  subscribe(view, visible);
  view.timers.push(setInterval(() => refreshAges(view), 1000));
}

function subscribe(view, rows) {
  const names = rows.filter((r) => r.kind !== "assembly" && r.kind !== "trigger").map((r) => r.name);
  if (!names.length) return;
  view.subscribed.push(...names);
  request("subscribe", { names }).catch((e) => toast(`subscribe failed: ${e.message}`, true));
}

function buildRow(view, row) {
  const refs = { row, value: null, last: null, readState: "waiting", writing: false, ctlBuilt: false, step: null };
  const node = el("div", { class: "row" });
  if (row.group) node.style.borderLeftColor = row.group;
  refs.node = node;

  if (row.kind === "assembly") {
    node.append(
      el("div", { class: "label" }, el("a", { href: `#/d/${encodeURIComponent(row.name)}`, text: `${row.label} ›` })),
    );
    return node;
  }
  if (row.kind === "trigger") {
    const btn = el("button", { class: "btn", text: row.button, title: row.doc || "", disabled: !row.writable,
      onclick: () => confirmDialog(`Trigger ${row.name}?`, row.doc || "", () =>
        request("trigger", { name: row.name }).catch((e) => toast(e.message, true))) });
    refs.dot = el("span", { class: "dot", text: "●", style: "color:var(--wait)" });
    node.append(el("div", { class: "label", text: row.label }), el("div", { class: "value" }), el("div", { class: "ctl" }, refs.dot, btn));
    view.rows.set(row.name, refs);
    return node;
  }
  refs.dot = el("span", { class: "dot", text: "●", style: `color:${DOT.waiting}`, title: "waiting for the first value" });
  refs.valueEl = el("div", { class: "value", text: "…" });
  refs.ctl = el("div", { class: "ctl" }, refs.dot);
  node.append(el("div", { class: "label", text: row.label }), refs.valueEl, refs.ctl);
  view.rows.set(row.name, refs);
  return node;
}

function applyValue(name, value) {
  const refs = S.view?.rows.get(name);
  if (!refs || !refs.valueEl) return;
  refs.t = value.t;
  if (value.error !== undefined) {
    refs.readState = "error";
    refs.readMessage = value.error;
    refs.valueEl.className = "value error";
    refs.valueEl.textContent = value.error;
  } else {
    refs.value = value;
    refs.readState = value.state || "polled";
    refs.readMessage = "";
    const text = typeof value.num === "number" ? formatNum(value.num) : value.text;
    refs.valueEl.className = "value" + (value.cls ? ` ${value.cls}` : "") + (refs.row.writable ? " settable" : "");
    refs.valueEl.replaceChildren(text, refs.row.unit ? el("span", { class: "unit", text: refs.row.unit }) : "", el("span", { class: "age" }));
    if (!refs.ctlBuilt) buildControls(refs);
    if (refs.select && !refs.select.matches(":focus")) refs.select.value = value.text;
  }
  if (!refs.writing) showDot(refs, refs.readState, refs.readMessage);
}

function showDot(refs, state, message) {
  if (!refs.dot) return;
  refs.dot.style.color = DOT[state] || DOT.error;
  refs.dot.title = message ? `${state}: ${message}` : state;
}

function applyWrite({ name, state, message }) {
  const refs = S.view?.rows.get(name);
  if (state === "error") toast(`${name}: ${message}`, true);
  if (!refs) return;
  refs.writing = state === "busy" || state === "moving";
  if (refs.writing || state === "error") showDot(refs, state, message);
  else showDot(refs, refs.readState, refs.readMessage);
}

function refreshAges(view) {
  const now = serverNow();
  for (const refs of view.rows.values()) {
    if (!refs.t || !refs.valueEl) continue;
    const age = now - refs.t;
    refs.valueEl.classList.toggle("stale", age > STALE_AFTER);
    const ageEl = refs.valueEl.querySelector(".age");
    if (ageEl) ageEl.textContent = age > SHOW_AGE_AFTER ? `${Math.round(age)} s ago` : "";
  }
}

// ---- controls for writable items ---------------------------------------------------------------

function buildControls(refs) {
  refs.ctlBuilt = true;
  const { row, value } = refs;
  if (!row.writable || row.kind !== "adjustable") return;
  const name = row.name;
  const labels = row.enum || value.enum;
  if (labels) {
    refs.select = el("select", { class: "enum", "aria-label": `set ${row.label}`,
      onchange: (e) => request("set", { name, value: e.target.value }).catch((err) => toast(err.message, true)) },
      labels.map((l) => el("option", { value: l, text: l })));
    refs.select.value = value.text;
    refs.ctl.append(refs.select);
  } else if (typeof value.num === "number") {
    refs.step = el("input", { class: "step", type: "number", inputmode: "decimal", step: "any", "aria-label": "step", value: "1" });
    request("step_size", { name }).then((r) => (refs.step.value = r.step)).catch(() => {});
    const move = (sign) => request("step", { name, sign, step: parseFloat(refs.step.value) }).catch((e) => toast(e.message, true));
    refs.ctl.append(
      el("button", { class: "btn", text: "▼", "aria-label": "step down", onclick: () => move(-1) }),
      refs.step,
      el("button", { class: "btn", text: "▲", "aria-label": "step up", onclick: () => move(1) }),
    );
  }
  refs.ctl.append(el("button", { class: "btn stop", text: "■", title: "Stop", "aria-label": "stop",
    onclick: () => request("stop", { name }).catch((e) => toast(e.message, true)) }));
  if (!labels) {
    refs.valueEl.addEventListener("click", () => {
      const current = refs.value ? (typeof refs.value.num === "number" ? String(refs.value.num) : refs.value.text) : "";
      inputDialog(`Move ${name}`, `currently ${current}${row.unit ? " " + row.unit : ""}`, current, (target) =>
        request("set", { name, value: target }).catch((e) => toast(e.message, true)));
    });
  }
}

function inputDialog(title, note, initial, onOk) {
  const input = el("input", { value: initial, inputmode: "decimal", autocomplete: "off" });
  const dialog = el("dialog", {},
    el("h2", { text: title }), el("p", { text: note }), input,
    el("div", { class: "actions" },
      el("button", { class: "btn", text: "Cancel", onclick: () => dialog.close() }),
      el("button", { class: "btn primary", text: "Move", onclick: () => { dialog.close(); onOk(input.value); } })));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { dialog.close(); onOk(input.value); } });
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  input.select();
}

function confirmDialog(title, note, onOk) {
  const dialog = el("dialog", {},
    el("h2", { text: title }), note ? el("p", { text: note }) : null,
    el("div", { class: "actions" },
      el("button", { class: "btn", text: "Cancel", onclick: () => dialog.close() }),
      el("button", { class: "btn primary", text: "OK", onclick: () => { dialog.close(); onOk(); } })));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

// ---- SVG panels ------------------------------------------------------------------------------------

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/;

function sanitizeSvg(text) {
  // Parse, drop anything executable, keep the eco click markers as data-eco.
  // onclick="// eco: <name>" (or a <desc> child) is how eco's panels mark a
  // device; here a click only opens that device's view -- nothing runs.
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  const svg = doc.documentElement;
  if (svg.nodeName !== "svg" || doc.getElementsByTagName("parsererror").length) throw new Error("not an SVG");
  svg.querySelectorAll("script, foreignObject, iframe, object, embed").forEach((n) => n.remove());
  for (const node of [svg, ...svg.querySelectorAll("*")]) {
    const onclick = node.getAttribute("onclick");
    const marker = onclick && onclick.match(/^\s*\/\/\s*eco:\s*(.*)$/);
    if (marker) node.setAttribute("data-eco", marker[1].trim());
    else if (node !== svg && node.nodeName === "g") {
      const desc = [...node.children].find((c) => c.nodeName === "desc");
      if (desc && NAME_RE.test(desc.textContent.trim())) node.setAttribute("data-eco", desc.textContent.trim());
    }
    for (const attr of [...node.attributes]) {
      if (/^on/i.test(attr.name) || (/href$/i.test(attr.name) && /^\s*javascript:/i.test(attr.value))) node.removeAttribute(attr.name);
    }
  }
  if (!svg.getAttribute("viewBox") && svg.getAttribute("width") && svg.getAttribute("height")) {
    svg.setAttribute("viewBox", `0 0 ${parseFloat(svg.getAttribute("width"))} ${parseFloat(svg.getAttribute("height"))}`);
  }
  svg.removeAttribute("width");
  svg.removeAttribute("height");
  return document.importNode(svg, true);
}

async function showSvg(name) {
  const view = newView("svg", name);
  setTitle(`${name} panel`, true);
  const panel = el("div", { class: "panel" });
  const note = el("p", { class: "panel-note", text: "loading…" });
  setMain(panel, note);
  let prefix = name;
  let busy = false;

  const load = async () => {
    if (busy || document.visibilityState !== "visible") return;
    busy = true;
    try {
      const response = await fetch(`/api/svg?name=${encodeURIComponent(name)}`, { cache: "no-store" });
      if (!response.ok) throw new Error(response.statusText || `HTTP ${response.status}`);
      prefix = response.headers.get("X-Eco-Prefix") || name;
      const svg = sanitizeSvg(await response.text());
      if (S.view !== view) return;
      panel.replaceChildren(svg);
      note.textContent = `live – refreshed every ${SVG_REFRESH / 1000} s · tap a device to open it`;
    } catch (e) {
      if (S.view === view) note.textContent = `could not load the panel: ${e.message}`;
    } finally {
      busy = false;
    }
  };
  panel.addEventListener("click", (e) => {
    const target = e.target.closest("[data-eco]");
    if (!target) return;
    const raw = target.getAttribute("data-eco");
    if (!NAME_RE.test(raw)) return toast(`not a device name: ${raw}`, true);
    location.hash = `#/d/${encodeURIComponent(prefix ? `${prefix}.${raw}` : raw)}`;
  });
  await load();
  view.timers.push(setInterval(load, SVG_REFRESH));
}

// ---- start ---------------------------------------------------------------------------------------------

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
connect();
