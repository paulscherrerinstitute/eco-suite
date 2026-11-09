// eco SciLog viewer: the map -- the flow drawn very small, as narrow as the
// outline it can replace. Headings, text, lists, tables, code, plots and
// images keep their shape, so a figure or a table is found by its look; a
// click jumps to that element in the flow. The posts in view are marked and
// the map follows the reading position -- until the map is scrolled by hand;
// then it stays put until the flow is clicked again.
"use strict";

const mapState = { on: false, detached: false, visible: new Set(), cache: new Map() };
let mapObserver = null;

function setPane(mode) {
  if (!["headings", "outline", "map"].includes(mode)) mode = "headings";
  mapState.on = mode === "map";
  $("#outline-tree").hidden = mode !== "outline";
  $("#map").hidden = mode !== "map";
  $("#headings").hidden = mode !== "headings";
  if (typeof setHeadingsPane === "function") setHeadingsPane(mode === "headings");
  document.querySelectorAll("#pane-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.pane === mode));
  storageSet("eco-scilog-pane", mode);
  renderMap();
}

// characters per line of the flow, for how many lines a paragraph gets
function charsPerLine() {
  const w = ($("#flow").clientWidth || 800) - 48;
  return Math.max(40, Math.round(w / 7.5));
}

function renderMap() {
  const map = $("#map");
  if (mapObserver) { mapObserver.disconnect(); mapObserver = null; }
  if (!mapState.on) { map.replaceChildren(); return; }
  const frag = document.createDocumentFragment();
  const cpl = charsPerLine();
  let day = null;
  for (const p of state.posts) {
    const k = dayKey(p.t);
    if (k !== day) {
      frag.append(el("div", { class: "m-day", title: "Go to this day", onclick: () => jumpTo(document.querySelector(`.day[data-day="${k}"]`)) }, dayTitle(p.t)));
      day = k;
    }
    const key = `${p.id}|${p.updated || ""}|${cpl}|${p.comments?.length || 0}|${p.quote ? 1 : 0}`;
    let card = mapState.cache.get(p.id);
    if (!card || card.dataset.key !== key) {
      card = mapCard(p, cpl);
      card.dataset.key = key;
      mapState.cache.set(p.id, card);
    }
    card.classList.remove("m-visible");
    frag.append(card);
  }
  if (!state.posts.length) frag.append(el("p", { class: "hint", text: "No posts." }));
  map.replaceChildren(frag);
  observeForMap();
}

function mapCard(p, cpl) {
  const card = el("div", { class: "m-post", "data-post": p.id, title: `${clock(p.t)} ${shortName(p.author)}${p.title ? " · " + p.title : ""}` });
  card.append(el("div", { class: "m-meta", onclick: () => jumpToPost(p.id) }, `${clock(p.t)} ${shortName(p.author)} `, ...postMarks(p)));
  if (p.quote) card.append(el("div", { class: "m-reply m-el", title: `reply to: ${p.quote.text.slice(0, 80)}` }));
  const t = document.createElement("template");
  t.innerHTML = p.html;  // a template: nothing in it loads or runs
  [...t.content.children].forEach((node, i) => {
    const g = glyph(node, cpl);
    if (!g) return;
    g.classList.add("m-el");
    g.addEventListener("click", (ev) => { ev.stopPropagation(); jumpToElement(p.id, i); });
    card.append(g);
  });
  if (p.comments?.length) {
    const c = el("div", { class: "m-comments m-el", title: `${p.comments.length} comment${p.comments.length > 1 ? "s" : ""}` },
      ...p.comments.slice(0, 4).map(() => el("span", { class: "m-line" })));
    c.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const box = document.querySelector(`#p-${p.id} .comments`);
      if (box) { box.classList.add("open"); box.scrollIntoView({ block: "center" }); }
    });
    card.append(c);
  }
  card.addEventListener("click", () => jumpToPost(p.id));
  return card;
}

function jumpToElement(postId, index) {
  const post = document.getElementById(`p-${postId}`);
  const target = post?.querySelector(".body")?.children[index];
  if (!target) { jumpToPost(postId); return; }
  revealInOutline(postId);
  target.scrollIntoView({ block: "center" });
  target.classList.remove("m-flash");
  void target.offsetWidth;
  target.classList.add("m-flash");
  history.replaceState(null, "", `#lb=${state.logbook.id}&post=${postId}`);
}

function lines(n, lastFraction = 0.6) {
  const box = el("div", { class: "m-text" });
  for (let i = 0; i < n; i++) {
    const line = el("span", { class: "m-line" });
    if (i === n - 1) line.style.width = `${Math.round(Math.max(0.15, lastFraction) * 100)}%`;
    box.append(line);
  }
  return box;
}

function textLines(text, cpl, max = 14) {
  const len = text.replace(/\s+/g, " ").trim().length;
  if (!len) return null;
  const n = Math.min(max, Math.ceil(len / cpl));
  const last = len >= max * cpl ? 1 : (len - (n - 1) * cpl) / cpl;
  return lines(n, last);
}

function imageGlyph(img) {
  const src = img.getAttribute("src") || "";
  if (!/^(img|file)\//.test(src)) return null;  // only SciLog's own files, through this server
  const box = el("div", { class: "m-img" });
  const fig = img.closest("figure");
  const px = parseInt((fig?.style.width || img.style.width || img.getAttribute("width") || "").toString(), 10);
  const thumb = el("img", { src, loading: "lazy", alt: "", decoding: "async" });
  if (px > 0) thumb.style.width = `${Math.min(100, Math.round(px * 100 / Math.max(300, $("#flow").clientWidth - 48)))}%`;
  box.append(thumb);
  return box;
}

const PLOT_SVG = '<svg viewBox="0 0 60 24" preserveAspectRatio="none" aria-hidden="true">' +
  '<polyline points="1,20 9,16 16,18 24,9 31,12 39,5 46,8 53,3 59,6" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
  '<line x1="1" y1="23" x2="59" y2="23" stroke="currentColor" stroke-width=".8" opacity=".5"/></svg>';

function plotGlyph() {
  const box = el("div", { class: "m-plot" });
  box.innerHTML = PLOT_SVG;
  return box;
}

function glyph(node, cpl) {
  const tag = node.tagName.toLowerCase();
  if (/^h[1-6]$/.test(tag)) {
    const text = node.textContent.replace(/\s+/g, " ").trim();
    if (!text) return null;
    return el("div", { class: `m-h m-${tag}`, text, title: text });  // headings readable, in small type
  }
  if (tag === "hr") return el("div", { class: "m-hr" });
  if (node.classList.contains("plot") || node.querySelector(".plot")) return plotGlyph();
  if (tag === "table" || node.querySelector("table")) {
    const table = tag === "table" ? node : node.querySelector("table");
    const rows = [...table.rows].slice(0, 8);
    const cols = Math.min(8, Math.max(1, ...rows.map((r) => r.cells.length)));
    const grid = el("div", { class: "m-table" });
    grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    rows.forEach((r, i) => {
      for (let c = 0; c < cols; c++) grid.append(el("span", { class: i === 0 ? "m-cell m-th" : "m-cell" }));
    });
    if (table.rows.length > 8) grid.classList.add("m-more");
    return grid;
  }
  if (tag === "ul" || tag === "ol") {
    const list = el("div", { class: `m-list m-${tag}` });
    [...node.children].slice(0, 10).forEach((li) => {
      const len = li.textContent.trim().length;
      const line = el("span", { class: "m-line" });
      line.style.width = `${Math.min(92, Math.max(10, Math.round(len * 100 / cpl)))}%`;
      list.append(el("div", { class: "m-li" }, line));
    });
    return list;
  }
  if (tag === "pre") {
    const n = Math.min(10, Math.max(1, node.textContent.split("\n").filter((l) => l.trim()).length));
    const box = el("div", { class: "m-code" });
    box.append(lines(n, 0.5));
    return box;
  }
  const imgs = tag === "img" ? [node] : [...node.querySelectorAll("img")];
  if (imgs.length) {
    const box = el("div", { class: "m-mixed" });
    const text = textLines(node.textContent, cpl, 4);
    if (text) box.append(text);
    imgs.slice(0, 4).forEach((img) => { const g = imageGlyph(img); if (g) box.append(g); });
    return box.children.length ? box : null;
  }
  const text = textLines(node.textContent, cpl);
  if (!text) return null;
  if (tag === "blockquote") text.classList.add("m-quote");
  return text;
}

// -- which posts are in view ------------------------------------------------------------------

function observeForMap() {
  mapState.visible.clear();
  mapObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const id = e.target.dataset.id;
      if (e.isIntersecting) mapState.visible.add(id); else mapState.visible.delete(id);
      mapState.cache.get(id)?.classList.toggle("m-visible", e.isIntersecting);
    }
    followReading();
  }, { root: $("#flow") });
  document.querySelectorAll("#flow .post").forEach((p) => mapObserver.observe(p));
}

function followReading() {
  if (mapState.detached || !mapState.visible.size) return;
  const pane = $("#outline");
  const cards = [...mapState.visible].map((id) => mapState.cache.get(id)).filter(Boolean);
  if (!cards.length) return;
  const top = Math.min(...cards.map((c) => c.offsetTop)), bottom = Math.max(...cards.map((c) => c.offsetTop + c.offsetHeight));
  const view = pane.clientHeight;
  if (top < pane.scrollTop + 40 || bottom > pane.scrollTop + view) {
    pane.scrollTop = Math.max(0, (top + bottom) / 2 - view / 2);
  }
}

// -- set up -------------------------------------------------------------------------------------

(function setUpMap() {
  document.querySelectorAll("#pane-tabs button").forEach((b) => (b.onclick = () => setPane(b.dataset.pane)));
  const map = $("#map");
  // scrolling the map by hand (wheel, touch, its scroll bar) stops it following the flow;
  // a click into the flow brings it back to the reading position
  const pane = $("#outline");
  const detach = () => { if (mapState.on) mapState.detached = true; };
  pane.addEventListener("wheel", detach, { passive: true });
  pane.addEventListener("touchstart", detach, { passive: true });
  pane.addEventListener("pointerdown", (e) => { if (e.target === pane) detach(); });  // the scroll bar
  $("#flow").addEventListener("pointerdown", () => {
    if (!mapState.detached) return;
    mapState.detached = false;
    followReading();
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, [contenteditable], select")) return;
    if (e.key === "m" || e.key === "h" || e.key === "s") {
      document.body.classList.remove("no-outline");
      const want = { m: "map", h: "headings", s: "outline" }[e.key];
      setPane(storageGet("eco-scilog-pane") === want && want !== "headings" ? "headings" : want);
    }
  });
  window.addEventListener("resize", () => { clearTimeout(setUpMap.t); setUpMap.t = setTimeout(() => mapState.on && renderMap(), 300); });
})();
