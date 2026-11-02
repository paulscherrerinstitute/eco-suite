// eco web client -- talks web_frontend/PROTOCOL.md (v0) to eco.web.gateway.
// Plain ES modules, no build step, no dependencies. DOM is built with
// textContent only (never innerHTML from server data); SVG panels are
// sanitized before insertion (sanitizeSvg).

import * as strip from "./strip.js";

const VERSION = "0.3";
const STALE_AFTER = 35; // s without a fresh read -> greyed (monitored items are re-read at least every 10 s)
const SHOW_AGE_AFTER = 12; // s
const SVG_REFRESH = 3000; // ms
const PING_EVERY = 15000; // ms -- a dropped network sends no close frame; only a missing reply shows it
const PING_TIMEOUT = 8000; // ms
const CHART_REDRAW = 500; // ms
const DOT = { waiting: "var(--wait)", live: "var(--ok)", subscribed: "var(--ok)", polled: "var(--polled)",
  busy: "var(--busy)", moving: "var(--busy)", error: "var(--err)" };

const S = {
  ws: null, nextId: 0, pending: new Map(), hello: null, retry: 0, retryTimer: null,
  offset: 0,              // client clock - gateway clock, s
  view: null,             // current view: {kind, name, wanted: [], unlisten: [], refs: Set, timers: []}
  filter: "",
  subs: new Map(),        // name -> how many places want its values (view rows, folds, pins)
  listeners: new Map(),   // name -> Set of {value(v), write(w)}
  last: new Map(),        // name -> last value event (a new row shows it at once)
  series: new Map(),      // name -> strip.Series, for pinned items
  pins: loadPins(),       // [{name, window, avg}]
  unfolded: new Set(),    // assembly names unfolded inline (kept while navigating)
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

function stored(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : JSON.parse(v);
  } catch {
    return fallback;
  }
}

function store(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {}
}

function clientId() {
  let id = stored("eco-web-client", null);
  if (!id) {
    id = Math.random().toString(36).slice(2, 10);
    store("eco-web-client", id);
  }
  return id;
}

function formatNum(n) {
  if (Number.isInteger(n)) return String(n);
  const a = Math.abs(n);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return n.toExponential(4);
  return String(+n.toPrecision(7));
}

function valueText(v) {
  return typeof v.num === "number" ? formatNum(v.num) : v.text;
}

/** A plottable number for a value event, or null (item_menu.numeric: enums by their index). */
function numeric(v) {
  if (!v || v.error !== undefined) return null;
  if (typeof v.num === "number") return v.num;
  if (typeof v.bool === "boolean") return v.bool ? 1 : 0;
  if (v.enum) {
    const i = v.enum.indexOf(v.text);
    return i >= 0 ? i : null;
  }
  return null;
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
    // a new connection is a new server session: subscribe everything that is wanted again
    const names = [...S.subs.keys()];
    if (names.length) request("subscribe", { names }).catch((e) => toast(`subscribe failed: ${e.message}`, true));
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
  S.hello = null; // nothing is sent until the next connection's hello (which subscribes S.subs again)
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

// ---- subscriptions and value fan-out ---------------------------------------------------

/** Values of `names` are wanted (by a row, a fold, a pin); subscribes the new ones. */
function want(names) {
  const fresh = [];
  for (const name of names) {
    const n = S.subs.get(name) || 0;
    S.subs.set(name, n + 1);
    if (!n) fresh.push(name);
  }
  if (fresh.length && S.hello) request("subscribe", { names: fresh }).catch((e) => toast(`${fresh[0]}: ${e.message}`, true));
}

function release(names) {
  const gone = [];
  for (const name of names) {
    const n = (S.subs.get(name) || 0) - 1;
    if (n > 0) S.subs.set(name, n);
    else {
      S.subs.delete(name);
      S.last.delete(name);
      gone.push(name);
    }
  }
  if (gone.length && S.hello) request("unsubscribe", { names: gone }).catch(() => {});
}

/** Calls handler.value(v) / handler.write(w) for `name`; returns the unlisten function. */
function listen(name, handler) {
  if (!S.listeners.has(name)) S.listeners.set(name, new Set());
  S.listeners.get(name).add(handler);
  const last = S.last.get(name);
  if (last && handler.value) handler.value(last);
  return () => S.listeners.get(name)?.delete(handler);
}

function onEvent(message) {
  if (message.ev === "values") {
    if (typeof message.now === "number") S.offset = Date.now() / 1000 - message.now;
    for (const [name, value] of Object.entries(message.data)) {
      S.last.set(name, value);
      const series = S.series.get(name);
      const y = numeric(value);
      if (series && y !== null) series.add(value.t, y);
      for (const h of S.listeners.get(name) || []) h.value?.(value);
    }
  } else if (message.ev === "write") {
    const w = message.data;
    if (w.state === "error") toast(`${w.name}: ${w.message}`, true);
    for (const h of S.listeners.get(w.name) || []) h.write?.(w);
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
  view.cleanup?.();
  view.unlisten.forEach((u) => u());
  release(view.wanted);
}

function route() {
  leaveView();
  if (!S.hello) return;
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith("/d/")) showDevice(hash.slice(3));
  else if (hash.startsWith("/svg/")) showSvg(hash.slice(5));
  else if (hash.startsWith("/tweak/")) showTweak(hash.slice(7));
  else if (hash.startsWith("/jog/")) showJog(hash.slice(5));
  else if (hash.startsWith("/cam/")) showCamera(hash.slice(5));
  else if (hash === "/queues") showQueues();
  else if (hash.startsWith("/qout/")) showTaskOutput(hash.slice(6));
  else showHome();
}

window.addEventListener("hashchange", route);
document.getElementById("back").addEventListener("click", () => {
  if (history.length > 1) history.back();
  else location.hash = "";
});

function newView(kind, name) {
  S.view = { kind, name, wanted: [], unlisten: [], refs: new Set(), timers: [] };
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
  const tools = el("div", { class: "toolbar" },
    el("a", { class: "btn link-btn", href: "#/queues", text: "Queues" }));
  setMain(tools, search, sections);
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
  if (desc.camera) toolbar.append(el("a", { class: "btn primary link-btn", href: `#/cam/${encodeURIComponent(name)}` }, "Live view"));
  if (desc.svg) toolbar.append(el("a", { class: "btn primary link-btn", href: `#/svg/${encodeURIComponent(name)}` }, "Panel"));
  const parts = [];
  if (toolbar.childElementCount) parts.push(toolbar);
  if (desc.doc) parts.push(el("p", { class: "doc", text: desc.doc }));
  parts.push(rowsBlock(view, desc.rows, view, "no display items"));
  setMain(...parts);
  view.timers.push(setInterval(() => refreshAges(view), 1000));
}

/**
 * The rows of a description: visible ones in a list (sub-assembly items shown
 * inline under a small group header), the rest behind "N more items". `owner`
 * collects the subscriptions/listeners (the view, or an unfolded row).
 */
function rowsBlock(view, rows, owner, emptyText) {
  const visible = rows.filter((r) => !r.hidden);
  const hidden = rows.filter((r) => r.hidden);
  const box = el("div", { class: "rows" });
  box.append(visible.length ? el("div", { class: "list" }, groupedRows(view, visible, owner)) :
    el("div", { class: "empty small", text: emptyText }));
  if (hidden.length) {
    const hiddenList = el("div", { class: "list" });
    const details = el("details", { class: "hidden-items" }, el("summary", { text: `${hidden.length} more items` }), hiddenList);
    details.addEventListener("toggle", () => {
      if (details.open && !hiddenList.childElementCount) hiddenList.append(...groupedRows(view, hidden, owner));
    });
    box.append(details);
  }
  return box;
}

/** Rows, with a header above each run of 2+ rows from the same sub-assembly ("cryo.setpoint", "cryo.temperature"). */
function groupedRows(view, rows, owner) {
  const out = [];
  let i = 0;
  while (i < rows.length) {
    const dot = rows[i].label.indexOf(".");
    const group = dot > 0 ? rows[i].label.slice(0, dot) : null;
    let j = i + 1;
    while (group && j < rows.length && rows[j].label.startsWith(group + ".")) j++;
    if (group && j - i >= 2) {
      out.push(el("div", { class: "group-head", text: group }));
      for (const r of rows.slice(i, j)) out.push(buildRow(view, r, owner, r.label.slice(group.length + 1)));
    } else {
      for (const r of rows.slice(i, j)) out.push(buildRow(view, r, owner));
    }
    i = j;
  }
  return out;
}

function buildRow(view, row, owner, label = row.label) {
  if (row.kind === "assembly") return assemblyRow(view, row, owner, label);
  const node = el("div", { class: "row" + (row.own ? " own" : "") });
  if (row.kind === "trigger") {
    const refs = { row };
    const btn = el("button", { class: "btn", text: row.button, title: row.doc || "", disabled: !row.writable,
      onclick: () => confirmDialog(`Trigger ${row.name}?`, row.doc || "", () =>
        request("trigger", { name: row.name }).catch((e) => toast(e.message, true))) });
    refs.dot = el("span", { class: "dot", text: "●", style: "color:var(--wait)" });
    node.append(el("div", { class: "label", text: label }), el("div", { class: "value" }), el("div", { class: "ctl" }, refs.dot, btn));
    owner.unlisten.push(listen(row.name, { write: (w) => applyWrite(refs, w) }));
    return node;
  }
  node.append(el("div", { class: "label", text: label }), ...itemCells(view, row, owner));
  return node;
}

/** Whether a row's value can be set (an adjustable, or an assembly that is one, like a motor record). */
function settable(row) {
  return row.writable && (row.kind === "adjustable" || row.value === "adjustable");
}

/** The live value and the controls of an item row: [value cell, controls + ⋯ menu]. */
function itemCells(view, row, owner) {
  const refs = { row, value: null, readState: "waiting", writing: false, ctlBuilt: false, step: null };
  refs.dot = el("span", { class: "dot", text: "●", style: `color:${DOT.waiting}`, title: "waiting for the first value" });
  refs.valueEl = el("div", { class: "value", text: "…" });
  refs.ctl = el("div", { class: "ctl" }, refs.dot);
  const menu = el("button", { class: "btn menu", text: "⋯", title: "More", "aria-label": `more for ${row.name}`,
    onclick: () => rowMenu(row, refs) });
  view.refs.add(refs);
  owner.unlisten.push(listen(row.name, { value: (v) => applyValue(refs, v), write: (w) => applyWrite(refs, w) }));
  owner.wanted.push(row.name);
  want([row.name]);
  return [refs.valueEl, el("div", { class: "ctl-wrap" }, refs.ctl, menu)];
}

/** A sub-assembly row: ▸ unfolds its rows in place (▾ folds them again), › opens it as its own view. */
function assemblyRow(view, row, owner, label) {
  const node = el("div", { class: "row assembly" });
  const inner = el("div", { class: "fold-body", hidden: true });
  const toggle = el("button", { class: "btn fold", text: "▸", "aria-label": `unfold ${row.name}`, "aria-expanded": "false" });
  let fold = null; // {wanted, unlisten} while unfolded

  // forget: folded by the user (else only because its view goes away -- it unfolds again next time)
  const close = (forget) => {
    if (!fold) return;
    fold.unlisten.forEach((u) => u());
    release(fold.wanted);
    fold = null;
    inner.replaceChildren();
    inner.hidden = true;
    for (const r of [...view.refs]) if (!r.valueEl?.isConnected) view.refs.delete(r);
    toggle.textContent = "▸";
    toggle.setAttribute("aria-expanded", "false");
    if (forget) S.unfolded.delete(row.name);
  };
  const open = async () => {
    if (fold) return;
    fold = { wanted: [], unlisten: [] };
    const mine = fold;
    toggle.textContent = "▾";
    toggle.setAttribute("aria-expanded", "true");
    S.unfolded.add(row.name);
    inner.hidden = false;
    inner.replaceChildren(el("div", { class: "loading small", text: "loading…" }));
    let desc;
    try {
      desc = await request("describe", { name: row.name });
    } catch (e) {
      if (fold === mine) inner.replaceChildren(el("div", { class: "empty small", text: e.message }));
      return;
    }
    if (fold !== mine || S.view !== view) return;
    inner.replaceChildren(rowsBlock(view, desc.rows, mine, "nothing to show"));
  };
  toggle.addEventListener("click", () => (fold ? close(true) : open()));
  owner.unlisten.push(() => close(false)); // folding the parent (or leaving the view) folds this too

  node.append(
    el("div", { class: "label" }, toggle,
      el("a", { href: `#/d/${encodeURIComponent(row.name)}`, title: `open ${row.name} as its own view`, text: `${label} ›` })),
  );
  // a motor record: an assembly that is also an item has its value and controls on the same row
  if (row.value) node.append(...itemCells(view, row, owner));
  if (S.unfolded.has(row.name)) open();
  return el("div", { class: "assembly-wrap" }, node, inner);
}

function applyValue(refs, value) {
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
    refs.valueEl.className = "value" + (value.cls ? ` ${value.cls}` : "") + (refs.row.writable ? " settable" : "");
    refs.valueEl.replaceChildren(valueText(value), refs.row.unit ? el("span", { class: "unit", text: refs.row.unit }) : "", el("span", { class: "age" }));
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

function applyWrite(refs, { state, message }) {
  refs.writing = state === "busy" || state === "moving";
  if (refs.writing || state === "error") showDot(refs, state, message);
  else showDot(refs, refs.readState, refs.readMessage);
}

function refreshAges(view) {
  const now = serverNow();
  for (const refs of view.refs) {
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
  if (!settable(row)) return;
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
  if (!labels) refs.valueEl.addEventListener("click", () => moveDialog(name, row.unit));
}

function moveDialog(name, unit) {
  const v = S.last.get(name);
  const current = v && v.error === undefined ? (typeof v.num === "number" ? String(v.num) : v.text) : "";
  inputDialog(`Move ${name}`, `currently ${current}${unit ? " " + unit : ""}`, current, "Move", (target) =>
    request("set", { name, value: target }).catch((e) => toast(e.message, true)));
}

function inputDialog(title, note, initial, okText, onOk) {
  const input = el("input", { value: initial, inputmode: "decimal", autocomplete: "off" });
  const dialog = el("dialog", {},
    el("h2", { text: title }), el("p", { text: note }), input,
    el("div", { class: "actions" },
      el("button", { class: "btn", text: "Cancel", onclick: () => dialog.close() }),
      el("button", { class: "btn primary", text: okText, onclick: () => { dialog.close(); onOk(input.value); } })));
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

/** A sheet of actions; `actions`: [[label, fn], ...]. */
function actionSheet(title, actions) {
  const dialog = el("dialog", { class: "sheet" }, el("h2", { text: title }),
    el("div", { class: "sheet-actions" }, actions.map(([label, fn]) =>
      el("button", { class: "btn", text: label, onclick: () => { dialog.close(); fn(); } }))),
    el("div", { class: "actions" }, el("button", { class: "btn", text: "Close", onclick: () => dialog.close() })));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

function rowMenu(row, refs) {
  const actions = [];
  const pinned = isPinned(row.name);
  actions.push([pinned ? "Unpin strip chart" : "Pin live strip chart", () => (pinned ? unpin(row.name) : pin(row.name))]);
  if (settable(row) && typeof refs.value?.num === "number") {
    actions.push(["Tweak", () => (location.hash = `#/tweak/${encodeURIComponent(row.name)}`)]);
  }
  if (settable(row) && row.jog) {
    actions.push(["Jog", () => (location.hash = `#/jog/${encodeURIComponent(row.name)}`)]);
  }
  actions.push(["Details", () => showDetails(row.name)]);
  if (navigator.clipboard && window.isSecureContext) {
    actions.push(["Copy name", () => navigator.clipboard.writeText(row.name).then(() => toast("copied"), () => {})]);
  }
  actionSheet(row.name, actions);
}

async function showDetails(name) {
  const body = el("div", { class: "details-body", text: "reading…" });
  const dialog = el("dialog", { class: "sheet" }, el("h2", { text: name }), body,
    el("div", { class: "actions" }, el("button", { class: "btn", text: "Close", onclick: () => dialog.close() })));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  try {
    const { rows } = await request("details", { name });
    body.replaceChildren(el("dl", {}, rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })])));
  } catch (e) {
    body.textContent = e.message;
  }
}

// ---- picker: find any item by name -------------------------------------------------------------

/** A search over the initialized components; `onPick(name)` for the chosen item. */
function picker(title, onPick) {
  const input = el("input", { type: "search", placeholder: "part of a name, e.g. gauge or omega", autocomplete: "off",
    autocapitalize: "off", spellcheck: "false" });
  const results = el("div", { class: "list picker-results" });
  const note = el("p", { class: "picker-note", text: "Searches the initialized components." });
  const dialog = el("dialog", { class: "sheet" }, el("h2", { text: title }), input, note, results,
    el("div", { class: "actions" }, el("button", { class: "btn", text: "Close", onclick: () => dialog.close() })));
  let timer = null, seq = 0;
  const run = async () => {
    const mine = ++seq;
    try {
      const { hits } = await request("search", { query: input.value, limit: 40 });
      if (mine !== seq) return;
      results.replaceChildren(...hits.map((h) => el("button", { class: "item pick", onclick: () => { dialog.close(); onPick(h.name); } },
        el("span", { class: "name", text: h.name }), el("span", { class: "note", text: h.kind }))));
      if (!hits.length) results.append(el("div", { class: "empty small", text: "nothing found" }));
    } catch (e) {
      results.replaceChildren(el("div", { class: "empty small", text: e.message }));
    }
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 250); });
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  input.focus();
  run();
}

// ---- pinned live strip charts (the dock) --------------------------------------------------------

function loadPins() {
  const pins = stored("eco-web-pins", []);
  return Array.isArray(pins) ? pins.filter((p) => p && typeof p.name === "string") : [];
}

function savePins() {
  store("eco-web-pins", S.pins.map(({ name, window, avg }) => ({ name, window, avg })));
}

function isPinned(name) {
  return S.pins.some((p) => p.name === name);
}

function pin(name) {
  if (isPinned(name)) return;
  S.pins.push({ name, window: 60, avg: 0 });
  savePins();
  startPin(S.pins[S.pins.length - 1]);
  renderDock(true);
  toast(`${name} pinned – it stays in the strip below while you go elsewhere`);
}

function unpin(name) {
  const p = S.pins.find((q) => q.name === name);
  if (!p) return;
  stopPin(p);
  S.pins = S.pins.filter((q) => q !== p);
  savePins();
  renderDock();
}

function startPin(p) {
  if (!S.series.has(p.name)) S.series.set(p.name, new strip.Series());
  p.unlisten = listen(p.name, { value: (v) => updateCard(p, v) });
  want([p.name]);
}

function stopPin(p) {
  p.unlisten?.();
  release([p.name]);
  S.series.delete(p.name);
}

function updateCard(p, v) {
  if (!p.valueEl) return;
  p.valueEl.textContent = v.error !== undefined ? v.error : valueText(v);
  p.valueEl.classList.toggle("error", v.error !== undefined);
}

function cycle(list, current) {
  const i = list.indexOf(current);
  return list[(i + 1) % list.length];
}

function renderDock(open) {
  const dock = document.getElementById("dock");
  if (open) store("eco-web-dock-open", true);
  const isOpen = stored("eco-web-dock-open", true);
  dock.hidden = false;
  const head = el("div", { class: "dock-head" },
    el("button", { class: "btn dock-toggle", text: `${isOpen ? "▾" : "▸"} Pinned (${S.pins.length})`, "aria-expanded": String(isOpen),
      onclick: () => { store("eco-web-dock-open", !isOpen); renderDock(); } }),
    el("button", { class: "btn", text: "+ Add", title: "pin any item: search by name", onclick: () => picker("Pin an item", pin) }));
  const cards = el("div", { class: "dock-cards", hidden: !isOpen });
  for (const p of S.pins) {
    const windowLabel = strip.WINDOWS.find(([, s]) => s === p.window)?.[0] || `${p.window} s`;
    p.valueEl = el("span", { class: "card-value", text: S.last.get(p.name) ? valueText(S.last.get(p.name)) : "…" });
    p.avgEl = el("span", { class: "card-avg" });
    p.canvas = el("canvas", { class: "chart", "aria-label": `strip chart of ${p.name}` });
    cards.append(el("div", { class: "card" },
      el("div", { class: "card-head" },
        el("a", { class: "card-name", href: `#/d/${encodeURIComponent(p.name)}`, text: p.name }),
        p.valueEl, p.avgEl),
      p.canvas,
      el("div", { class: "card-tools" },
        el("button", { class: "btn small", text: windowLabel, title: "time window",
          onclick: () => { p.window = cycle(strip.WINDOWS.map(([, s]) => s), p.window); savePins(); renderDock(); } }),
        el("button", { class: "btn small", text: p.avg ? `avg ${p.avg}` : "avg off", title: "running average over the last N samples",
          onclick: () => { p.avg = cycle(strip.AVERAGES, p.avg); savePins(); renderDock(); } }),
        el("button", { class: "btn small", text: "clear", title: "forget the samples so far",
          onclick: () => S.series.get(p.name) && (S.series.get(p.name).points = []) }),
        el("button", { class: "btn small", text: "✕", title: "unpin", "aria-label": `unpin ${p.name}`, onclick: () => unpin(p.name) }))));
  }
  if (!S.pins.length) cards.append(el("p", { class: "empty small", text: "Pin items from their ⋯ menu, or add one by name." }));
  dock.replaceChildren(head, cards);
  document.body.style.setProperty("--dock-h", `${dock.offsetHeight}px`);
  drawCharts();
}

function drawCharts() {
  const now = serverNow();
  for (const p of S.pins) {
    const series = S.series.get(p.name);
    if (!p.canvas || !series || !p.canvas.isConnected || p.canvas.closest("[hidden]")) continue;
    const labels = S.last.get(p.name)?.enum || null;
    const mean = strip.draw(p.canvas, series, { now, window: p.window, avg: p.avg, labels });
    if (p.avgEl) p.avgEl.textContent = mean === null ? "" : `avg ${formatNum(+mean.toPrecision(6))}`;
  }
}

setInterval(() => { if (document.visibilityState === "visible") drawCharts(); }, CHART_REDRAW);
window.addEventListener("resize", () => {
  const dock = document.getElementById("dock");
  document.body.style.setProperty("--dock-h", `${dock.offsetHeight}px`);
});

// ---- tweak: one adjustable, big buttons, against the pinned detectors --------------------------------

async function showTweak(name) {
  const view = newView("tweak", name);
  setTitle(`tweak ${name}`, true);
  setMain(el("div", { class: "loading", text: "opening…" }));
  let desc;
  try {
    desc = await request("describe", { name });
  } catch (e) {
    if (S.view === view) setMain(el("div", { class: "empty", text: `${name}: ${e.message}` }));
    return;
  }
  if (S.view !== view) return;
  const row = desc.rows.find((r) => r.name === name);
  if (!row || !settable(row)) {
    setMain(el("div", { class: "empty", text: `${name} cannot be tweaked here (not a writable adjustable${S.hello.writes ? "" : "; writes are off"}).` }));
    return;
  }
  const big = el("div", { class: "tweak-value", text: "…" });
  const dot = el("span", { class: "dot", text: "●", style: `color:${DOT.waiting}` });
  const step = el("input", { class: "step big", type: "number", inputmode: "decimal", step: "any", value: "1", "aria-label": "step" });
  const goTo = el("input", { class: "step big", type: "number", inputmode: "decimal", step: "any", placeholder: "go to", "aria-label": "go to" });
  let start = null, readState = "waiting", writing = false;
  request("step_size", { name }).then((r) => (step.value = r.step)).catch(() => {});

  const send = (op, args) => request(op, { name, ...args }).catch((e) => toast(e.message, true));
  const move = (sign) => send("step", { sign, step: parseFloat(step.value) });
  const scale = (f) => { step.value = String(+(parseFloat(step.value) * f).toPrecision(6)); };
  const startNote = el("span", { class: "tweak-start" });

  view.unlisten.push(listen(name, {
    value: (v) => {
      if (v.error !== undefined) { big.textContent = v.error; readState = "error"; }
      else {
        big.replaceChildren(valueText(v), row.unit ? el("span", { class: "unit", text: row.unit }) : "");
        readState = v.state || "polled";
        if (start === null && typeof v.num === "number") { start = v.num; startNote.textContent = `start ${formatNum(start)}`; }
      }
      if (!writing) dot.style.color = DOT[readState] || DOT.error;
    },
    write: (w) => {
      writing = w.state === "busy" || w.state === "moving";
      dot.style.color = writing || w.state === "error" ? DOT[w.state] || DOT.error : DOT[readState] || DOT.error;
    },
  }));
  view.wanted.push(name);
  want([name]);

  const onKey = (e) => {
    if (e.target.closest?.("input, select, textarea, dialog")) return;
    if (e.key === "ArrowUp" || e.key === "ArrowRight") { move(1); e.preventDefault(); }
    else if (e.key === "ArrowDown" || e.key === "ArrowLeft") { move(-1); e.preventDefault(); }
    else if (e.key === "+" || e.key === "*") scale(2);
    else if (e.key === "-" || e.key === "/") scale(0.5);
    else if (e.key === "Escape") { send("stop", {}); e.preventDefault(); }
  };
  document.addEventListener("keydown", onKey);
  view.cleanup = () => document.removeEventListener("keydown", onKey);

  setMain(
    el("div", { class: "tweak" },
      el("div", { class: "tweak-head" }, dot, big),
      el("div", { class: "tweak-row" },
        el("button", { class: "btn huge", text: "▼", "aria-label": "step down", onclick: () => move(-1) }),
        el("button", { class: "btn huge", text: "▲", "aria-label": "step up", onclick: () => move(1) })),
      el("div", { class: "tweak-row" },
        el("button", { class: "btn", text: "÷2", onclick: () => scale(0.5) }), step,
        el("button", { class: "btn", text: "×2", onclick: () => scale(2) })),
      el("div", { class: "tweak-row" },
        goTo,
        el("button", { class: "btn", text: "Go", onclick: () => goTo.value !== "" && send("set", { value: goTo.value }) })),
      el("div", { class: "tweak-row" },
        el("button", { class: "btn", text: "↺ back to start", onclick: () => start !== null && send("set", { value: start }) }),
        startNote),
      el("div", { class: "tweak-row" }, el("button", { class: "btn stop wide", text: "■ Stop", onclick: () => send("stop", {}) })),
      row.jog ? el("div", { class: "tweak-row" }, el("a", { class: "btn link-btn", href: `#/jog/${encodeURIComponent(name)}`, text: "Jog instead" })) : null,
      el("p", { class: "doc", text: "Keys: ↑/→ step up, ↓/← step down, +/− double or halve the step, Esc stop. " +
        "Pin detectors (⋯ menu or + Add below) to watch them while you tweak." })));
}

// ---- jog: move while held, at an adjustable speed (motor records) -------------------------------------

async function showJog(name) {
  const view = newView("jog", name);
  setTitle(`jog ${name}`, true);
  setMain(el("div", { class: "loading", text: "opening…" }));
  let desc;
  try {
    desc = await request("describe", { name });
  } catch (e) {
    if (S.view === view) setMain(el("div", { class: "empty", text: `${name}: ${e.message}` }));
    return;
  }
  if (S.view !== view) return;
  const row = desc.rows.find((r) => r.name === name);
  if (!row || !settable(row) || !row.jog) {
    const why = row?.jog ? "writes are off in this web session" : "it has no hardware jog";
    setMain(el("div", { class: "empty", text: `${name} cannot be jogged here: ${why}.` }));
    return;
  }
  const speedName = row.jog.speed;
  const unit = row.unit || "";
  const big = el("div", { class: "tweak-value", text: "…" });
  const dot = el("span", { class: "dot", text: "●", style: `color:${DOT.waiting}` });
  const state = el("div", { class: "jog-state", text: "" });
  const speedValue = el("span", { class: "jog-speed-value", text: "…" });
  const speedInput = el("input", { class: "step big", type: "number", inputmode: "decimal", step: "any", min: "0",
    placeholder: "speed", "aria-label": "jog speed" });
  let speed = null, readState = "waiting", writing = false;

  view.unlisten.push(listen(name, {
    value: (v) => {
      if (v.error !== undefined) { big.textContent = v.error; readState = "error"; }
      else {
        big.replaceChildren(valueText(v), unit ? el("span", { class: "unit", text: unit }) : "");
        readState = v.state || "polled";
      }
      if (!writing) dot.style.color = DOT[readState] || DOT.error;
    },
    write: (w) => {
      writing = w.state === "busy" || w.state === "moving";
      dot.style.color = writing || w.state === "error" ? DOT[w.state] || DOT.error : DOT[readState] || DOT.error;
      state.textContent = w.message || "";
      state.className = "jog-state" + (w.state === "error" ? " error" : writing ? " moving" : "");
    },
  }));
  view.unlisten.push(listen(speedName, {
    value: (v) => {
      if (v.error !== undefined) { speedValue.textContent = v.error; return; }
      speed = typeof v.num === "number" ? v.num : null;
      speedValue.textContent = `${valueText(v)}${unit ? ` ${unit}/s` : ""}`;
      if (!speedInput.matches(":focus")) speedInput.value = speed ?? "";
    },
  }));
  view.wanted.push(name, speedName);
  want([name, speedName]);

  // Held: "jog" every jog_repeat s; the gateway stops the motor when that stops
  // coming (released, phone locked, connection gone). Released: an explicit stop.
  let held = 0, repeat = null, holder = null;
  const jogStart = (direction, button) => {
    if (held === direction) return;
    if (held) jogEnd();
    held = direction;
    holder = button;
    button?.classList.add("held");
    const send = () => request("jog", { name, direction, start: true }).catch((e) => { toast(e.message, true); jogEnd(); });
    send();
    repeat = setInterval(send, (S.hello?.jog_repeat || 0.25) * 1000);
  };
  const jogEnd = () => {
    if (!held) return;
    const direction = held;
    held = 0;
    clearInterval(repeat);
    holder?.classList.remove("held");
    request("jog", { name, direction, start: false }).catch(() => {});
  };
  const holdButton = (text, direction, label) => {
    const b = el("button", { class: "btn huge jog", text, "aria-label": label });
    b.addEventListener("pointerdown", (e) => { e.preventDefault(); b.setPointerCapture?.(e.pointerId); jogStart(direction, b); });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) b.addEventListener(type, jogEnd);
    b.addEventListener("contextmenu", (e) => e.preventDefault()); // long press on a phone
    b.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) jogStart(direction, b); });
    b.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") jogEnd(); });
    return b;
  };
  const setSpeed = (value) => {
    if (!(value > 0)) return toast("the jog speed must be positive", true);
    request("jog_speed", { name, value }).catch((e) => toast(e.message, true));
  };

  const onKey = (e) => {
    if (e.target.closest?.("input, select, textarea, dialog")) return;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") { if (!e.repeat) jogStart(1, plus); e.preventDefault(); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") { if (!e.repeat) jogStart(-1, minus); e.preventDefault(); }
    else if ((e.key === "+" || e.key === "*") && speed) setSpeed(speed * 2);
    else if ((e.key === "-" || e.key === "/") && speed) setSpeed(speed / 2);
    else if (e.key === "Escape") { jogEnd(); request("stop", { name }).catch(() => {}); e.preventDefault(); }
  };
  const onKeyUp = (e) => {
    if (["ArrowRight", "ArrowUp", "ArrowLeft", "ArrowDown"].includes(e.key)) jogEnd();
  };
  const onHide = () => { if (document.visibilityState !== "visible") jogEnd(); };
  document.addEventListener("keydown", onKey);
  document.addEventListener("keyup", onKeyUp);
  document.addEventListener("visibilitychange", onHide);
  window.addEventListener("blur", jogEnd);
  view.cleanup = () => {
    jogEnd();
    document.removeEventListener("keydown", onKey);
    document.removeEventListener("keyup", onKeyUp);
    document.removeEventListener("visibilitychange", onHide);
    window.removeEventListener("blur", jogEnd);
  };

  const minus = holdButton("◀ −", -1, "jog negative while held");
  const plus = holdButton("+ ▶", 1, "jog positive while held");
  setMain(
    el("div", { class: "tweak" },
      el("div", { class: "tweak-head" }, dot, big),
      state,
      el("div", { class: "tweak-row" }, minus, plus),
      el("div", { class: "tweak-row jog-speed" }, el("span", { class: "tweak-start", text: "jog speed" }), speedValue),
      el("div", { class: "tweak-row" },
        el("button", { class: "btn", text: "÷2", onclick: () => speed && setSpeed(speed / 2) }), speedInput,
        el("button", { class: "btn", text: "×2", onclick: () => speed && setSpeed(speed * 2) }),
        el("button", { class: "btn", text: "Set", onclick: () => setSpeed(parseFloat(speedInput.value)) })),
      el("div", { class: "tweak-row" }, el("button", { class: "btn stop wide", text: "■ Stop",
        onclick: () => { jogEnd(); request("stop", { name }).catch((e) => toast(e.message, true)); } })),
      el("div", { class: "tweak-row" }, el("a", { class: "btn link-btn", href: `#/tweak/${encodeURIComponent(name)}`, text: "Tweak instead" })),
      el("p", { class: "doc", text: "Moves only while a button is held, at the jog speed, until a limit. " +
        "Released, phone locked or connection lost: it stops (the gateway stops it within a second " +
        "when the held button stops reporting). Keys: hold →/↑ or ←/↓, +/− double or halve the speed, Esc stop." })));
}

// ---- camera: live view, tap to center, drag to zoom ---------------------------------------------------

async function showCamera(name) {
  const view = newView("camera", name);
  setTitle(`${name} live`, true);
  setMain(el("div", { class: "loading", text: "opening…" }));
  let desc;
  try {
    desc = await request("describe", { name });
  } catch (e) {
    if (S.view === view) setMain(el("div", { class: "empty", text: `${name}: ${e.message}` }));
    return;
  }
  if (S.view !== view) return;
  const cam = desc.camera;
  if (!cam) {
    setMain(el("div", { class: "empty", text: `${name} has no live stream.` }));
    return;
  }
  const qualityKey = `eco-web-cam-quality:${name}`;
  let quality = stored(qualityKey, "low");
  if (!cam.qualities.includes(quality)) quality = "low";
  const img = el("img", { class: "cam-img" + (cam.rotate ? " rotated" : ""), alt: `${name} live`, draggable: "false" });
  const box = el("div", { class: "cam-box" });
  const note = el("p", { class: "panel-note", text: "connecting…" });
  const wrap = el("div", { class: "cam-wrap" }, img, box);
  let retry = null, frames = 0;

  // an <img> keeps the stream's connection open: dropping its src closes it,
  // and the gateway closes the camera connection with its last viewer
  const stop = () => { clearTimeout(retry); img.src = "data:,"; };
  const start = () => {
    clearTimeout(retry);
    frames = 0;
    note.textContent = "connecting…";
    img.src = `/api/mjpeg?name=${encodeURIComponent(name)}&quality=${quality}&t=${Date.now()}`;
  };
  img.addEventListener("load", () => {
    if (img.src.startsWith("data:")) return;
    frames++;
    note.textContent = `live · ${quality}` + (control ? " · control on: tap to center, drag a box to zoom" : "");
  });
  img.addEventListener("error", () => {
    if (S.view !== view || img.src.startsWith("data:")) return;
    note.textContent = `no stream (${quality})${quality !== "low" ? " – try low quality" : ""}; retrying…`;
    retry = setTimeout(() => S.view === view && document.visibilityState === "visible" && start(), 4000);
  });
  const onVisible = () => (document.visibilityState === "visible" ? start() : stop());
  document.addEventListener("visibilitychange", onVisible);
  view.cleanup = () => { stop(); document.removeEventListener("visibilitychange", onVisible); };

  // control: off by default (a stray tap must not move the camera), like the Qt viewer's mouse control
  let control = false;
  const canControl = cam.ptz && S.hello.writes && desc.writable;
  const ptz = (action, args = {}) => request("ptz", { name, action, ...args }).catch((e) => toast(e.message, true));
  const at = (e) => {
    const r = img.getBoundingClientRect();
    return { x: Math.min(Math.max(e.clientX - r.left, 0), r.width), y: Math.min(Math.max(e.clientY - r.top, 0), r.height), w: r.width, h: r.height };
  };
  let drag = null;
  wrap.addEventListener("pointerdown", (e) => {
    if (!control) return;
    e.preventDefault();
    wrap.setPointerCapture?.(e.pointerId);
    drag = at(e);
  });
  wrap.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const p = at(e);
    Object.assign(box.style, { display: "block", left: `${Math.min(drag.x, p.x)}px`, top: `${Math.min(drag.y, p.y)}px`,
      width: `${Math.abs(p.x - drag.x)}px`, height: `${Math.abs(p.y - drag.y)}px` });
  });
  const endDrag = (e, cancelled) => {
    if (!drag) return;
    const p = at(e), d = drag;
    drag = null;
    box.style.display = "none";
    if (cancelled) return;
    if (Math.abs(p.x - d.x) < 8 && Math.abs(p.y - d.y) < 8) ptz("center", { x: p.x, y: p.y, w: p.w, h: p.h });
    else ptz("rect", { x: d.x, y: d.y, x1: p.x, y1: p.y, w: p.w, h: p.h });
  };
  wrap.addEventListener("pointerup", (e) => endDrag(e, false));
  wrap.addEventListener("pointercancel", (e) => endDrag(e, true));
  wrap.addEventListener("wheel", (e) => {
    if (!control) return;
    e.preventDefault();
    const p = at(e);
    ptz(e.deltaY < 0 ? "zoom_in" : "zoom_out", p);
  }, { passive: false });
  wrap.addEventListener("contextmenu", (e) => control && e.preventDefault());

  const controlBtn = el("button", { class: "btn", text: "Control: off", disabled: !canControl,
    title: canControl ? "tap to center, drag a box to zoom" : (cam.ptz ? "writes are off in this web session" : "no pan/tilt/zoom"),
    onclick: () => {
      control = !control;
      controlBtn.textContent = `Control: ${control ? "on" : "off"}`;
      controlBtn.classList.toggle("on", control);
      wrap.classList.toggle("control", control);
      document.querySelectorAll(".cam-ptz").forEach((b) => (b.disabled = !control));
      if (frames) note.textContent = `live · ${quality}` + (control ? " · control on: tap to center, drag a box to zoom" : "");
    } });
  const centre = () => { const r = img.getBoundingClientRect(); return { w: r.width || 1, h: r.height || 1 }; };
  const ptzBtn = (text, title, fn) => el("button", { class: "btn cam-ptz", text, title, disabled: true, onclick: fn });
  const qualitySelect = el("select", { class: "enum", "aria-label": "stream quality",
    onchange: (e) => { quality = e.target.value; store(qualityKey, quality); start(); } },
    cam.qualities.map((q) => el("option", { value: q, text: q })));
  qualitySelect.value = quality;

  setMain(
    wrap, note,
    el("div", { class: "toolbar cam-tools" },
      cam.ptz ? controlBtn : null,
      cam.ptz ? ptzBtn("Zoom +", "zoom in one step", () => ptz("zoom_in", centre())) : null,
      cam.ptz ? ptzBtn("Zoom −", "zoom out one step", () => ptz("zoom_out", centre())) : null,
      cam.ptz ? ptzBtn("Wide", "widest field of view", () => ptz("wide")) : null,
      cam.ptz ? ptzBtn("Home", "the camera's home position", () => confirmDialog(`Send ${name} home?`, "", () => ptz("home"))) : null,
      qualitySelect,
      el("a", { class: "btn link-btn", href: `#/d/${encodeURIComponent(name)}`, text: "Settings" })),
    el("p", { class: "doc", text: "Control on: tap to center the view there, drag a box to zoom into it; " +
      "on a laptop the mouse wheel zooms. Quality: low for a phone on a tunnel, full on the console network." }));
  if (document.visibilityState === "visible") start();
}

// ---- task queues: what runs and waits, pause/resume/stop -----------------------------------------------

const QUEUE_POLL = 1000; // ms

function duration(seconds) {
  if (seconds === null || seconds === undefined) return "";
  const s = Math.max(0, Math.round(seconds));
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

async function showQueues() {
  const view = newView("queues", "");
  setTitle("Queues", true);
  const box = el("div", { class: "queues" }, el("div", { class: "loading", text: "loading queues…" }));
  setMain(box, el("p", { class: "doc", text: "The session's task queues (%queue, scans with queue=…). " +
    "Controls act on what is already queued; nothing is added here." }));
  const writes = !!S.hello.writes;
  const act = (queue, action, task, question) => {
    const go = () => request("queue_action", { queue, action, task }).then(load, (e) => toast(e.message, true));
    question ? confirmDialog(question, "", go) : go();
  };
  const button = (text, onclick, cls = "") =>
    el("button", { class: `btn small ${cls}`, text, onclick, disabled: !writes, title: writes ? "" : "writes are off in this web session" });

  const taskLine = (q, t, kind) => {
    const took = t.started ? duration((t.finished || serverNow()) - t.started) : "";
    const head = el("div", { class: "task-head" },
      el("span", { class: `task-state ${t.state}`, text: t.state }),
      el("span", { class: "task-id", text: `#${t.id}` }),
      t.description ? el("span", { class: "task-desc", text: t.description }) : null,
      took ? el("span", { class: "task-time", text: took }) : null);
    const node = el("div", { class: `task ${kind}` }, head, el("div", { class: "task-source", text: t.source }));
    if (t.error) node.append(el("div", { class: "task-error", text: t.error }));
    if (t.scan) {
      const pct = t.scan.total ? (100 * t.scan.done) / t.scan.total : 0;
      node.append(el("div", { class: "progress" }, el("div", { class: "bar", style: `width:${pct.toFixed(1)}%` })),
        el("div", { class: "task-scan", text: `step ${t.scan.done} / ${t.scan.total}` +
          (t.scan.stopping ? " · stopping" : t.scan.paused ? " · scan paused" : "") }));
    }
    const tools = el("div", { class: "task-tools" });
    if (kind !== "pending") tools.append(el("a", { class: "btn small link-btn", href: `#/qout/${encodeURIComponent(q.name)}/${t.id}`, text: "Output" }));
    if (kind === "running") {
      if (t.scan) tools.append(t.scan.paused ? button("Resume scan", () => act(q.name, "resume_running"))
        : button("Pause scan", () => act(q.name, "pause_running")));
      tools.append(button("Stop task", () => act(q.name, "stop_running", undefined,
        `Stop task #${t.id}? Its scan stops at the next step; the queue pauses.`), "stop"));
    } else if (kind === "pending") {
      tools.append(button("Top", () => act(q.name, "move_top", t.id)), button("↑", () => act(q.name, "move_up", t.id)),
        button("Remove", () => act(q.name, "remove", t.id, `Remove task #${t.id} from queue ${q.name}?`), "stop"));
    }
    if (tools.childElementCount) node.append(tools);
    return node;
  };

  const render = (data) => {
    if (!data.queues.length) {
      box.replaceChildren(el("div", { class: "empty", text: "No queues in this session yet." }));
      return;
    }
    box.replaceChildren(...data.queues.map((q) => {
      const state = q.running ? (q.paused ? "running, then paused" : "running") : q.paused ? "paused" : q.pending.length ? "starting" : "idle";
      const card = el("section", { class: "queue-card" },
        el("div", { class: "queue-head" },
          el("h2", { text: q.name }), el("span", { class: `badge q-${q.paused ? "paused" : q.running ? "running" : "idle"}`, text: state }),
          q.paused ? button("Resume queue", () => act(q.name, "resume", undefined, q.pending.length ? `Resume queue ${q.name}? ${q.pending.length} waiting task(s) will start.` : null))
            : button("Pause queue", () => act(q.name, "pause"))));
      if (q.running) card.append(taskLine(q, q.running, "running"));
      if (q.pending.length) {
        card.append(el("div", { class: "section-title", text: `Waiting (${q.pending.length})` }),
          ...q.pending.map((t) => taskLine(q, t, "pending")),
          el("div", { class: "task-tools" },
            button("Drop all waiting", () => act(q.name, "clear", undefined, `Drop all ${q.pending.length} waiting tasks of ${q.name}?`), "stop")));
      }
      if (q.running || q.pending.length) {
        card.append(el("div", { class: "task-tools" }, button("Stop everything", () => act(q.name, "stop", undefined,
          `Stop the running task of ${q.name} and drop everything waiting?`), "stop")));
      }
      if (q.history.length) {
        const list = el("div", {}, ...q.history.map((t) => taskLine(q, t, "done")));
        card.append(el("details", { class: "hidden-items" }, el("summary", { text: `Finished (${q.history.length})` }), list));
      }
      return card;
    }));
  };
  let busy = false;
  const open = new Set(); // keep "Finished" folds open across refreshes
  const load = async () => {
    if (busy || S.view !== view || document.visibilityState !== "visible") return;
    busy = true;
    try {
      box.querySelectorAll("section.queue-card").forEach((c) => {
        if (c.querySelector("details")?.open) open.add(c.querySelector("h2").textContent);
        else open.delete(c.querySelector("h2")?.textContent);
      });
      const data = await request("queues");
      if (S.view !== view) return;
      render(data);
      box.querySelectorAll("section.queue-card").forEach((c) => {
        const d = c.querySelector("details");
        if (d && open.has(c.querySelector("h2").textContent)) d.open = true;
      });
    } catch (e) {
      if (S.view === view) box.replaceChildren(el("div", { class: "empty", text: `could not read the queues: ${e.message}` }));
    } finally {
      busy = false;
    }
  };
  await load();
  view.timers.push(setInterval(load, QUEUE_POLL));
}

async function showTaskOutput(path) {
  const slash = path.lastIndexOf("/");
  const queue = path.slice(0, slash), id = parseInt(path.slice(slash + 1), 10);
  const view = newView("qout", path);
  setTitle(`${queue} #${id}`, true);
  const head = el("div", { class: "task-head" });
  const pre = el("pre", { class: "task-output", text: "" });
  const follow = el("label", { class: "follow" }, el("input", { type: "checkbox", checked: true }), " follow");
  setMain(head, pre, follow);
  let offset = 0, busy = false;
  const load = async () => {
    if (busy || S.view !== view || document.visibilityState !== "visible") return;
    busy = true;
    try {
      const r = await request("queue_output", { queue, task: id, since: offset });
      if (S.view !== view) return;
      if (r.offset < offset || r.clipped) pre.textContent = r.clipped ? "… (older output not shown)\n" : "";
      offset = r.offset;
      if (r.text) pre.append(r.text);
      head.replaceChildren(el("span", { class: `task-state ${r.task.state}`, text: r.task.state }),
        el("span", { class: "task-source", text: r.task.source }));
      if (follow.querySelector("input").checked && r.text) pre.scrollTop = pre.scrollHeight;
    } catch (e) {
      if (S.view === view) head.replaceChildren(el("span", { class: "task-error", text: e.message }));
    } finally {
      busy = false;
    }
  };
  await load();
  view.timers.push(setInterval(load, QUEUE_POLL));
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
for (const p of S.pins) startPin(p);
renderDock();
connect();
