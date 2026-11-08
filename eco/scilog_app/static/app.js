// eco SciLog viewer: one logbook as a flow of days, an outline, fuzzy search.
// Data come from the local eco server (server.py), which talks to SciLog.
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { logbooks: [], logbook: null, posts: [], byId: new Map(), since: null, poll: null, results: [], active: -1 };
const POLL_MS = 20000;

// -- helpers -------------------------------------------------------------------------

async function api(path) {
  const r = await fetch(path, { credentials: "same-origin" });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).error || msg; } catch (e) { /* not json */ }
    throw new Error(`${r.status} ${msg}`);
  }
  return r.json();
}

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of children) if (c != null) e.append(c);
  return e;
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 1800);
}

function storageGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
function storageSet(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* private window */ } }

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function dayKey(t) { const d = new Date(t * 1000); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; }
function monthKey(t) { const d = new Date(t * 1000); return `${d.getFullYear()}-${d.getMonth()}`; }

// "Today", "Yesterday", "Friday" within the last week, else "Tue 14 Oct" (and the year if not this one)
function dayLabel(t) {
  const d = new Date(t * 1000), now = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((start(now) - start(d)) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days > 1 && days < 7) return WEEKDAYS[d.getDay()];
  return fullDate(t);
}
function fullDate(t) {
  const d = new Date(t * 1000);
  const year = d.getFullYear() !== new Date().getFullYear() ? ` ${d.getFullYear()}` : "";
  return `${WEEKDAYS[d.getDay()].slice(0, 3)} ${d.getDate()} ${MONTHS[d.getMonth()]}${year}`;
}
// "5 Nov" (and the year if not this one): the date beside a "Yesterday" or "Friday"
function shortDate(t) {
  const d = new Date(t * 1000);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}${d.getFullYear() !== new Date().getFullYear() ? " " + d.getFullYear() : ""}`;
}
function dayTitle(t) { return dayLabel(t) === fullDate(t) ? fullDate(t) : `${dayLabel(t)} · ${shortDate(t)}`; }
function monthLabel(t) { const d = new Date(t * 1000); return `${MONTHS[d.getMonth()]} ${d.getFullYear()}`; }
function clock(t) { const d = new Date(t * 1000); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; }
function shortName(email) { return (email || "").split("@")[0]; }

// -- logbook picker -------------------------------------------------------------------

function showPicker(show = true) {
  const p = $("#picker");
  p.hidden = !show;
  if (show) {
    $("#picker-filter").value = "";
    fillPicker("");
    $("#picker-filter").focus();
  }
}

function fillPicker(filter) {
  const list = $("#picker-list");
  list.replaceChildren();
  const f = filter.toLowerCase();
  const shown = state.logbooks.filter((lb) => !f || `${lb.name} ${lb.pgroup} ${lb.location}`.toLowerCase().includes(f));
  shown.slice(0, 300).forEach((lb, i) => {
    const touched = lb.touched ? new Date(lb.touched).getTime() / 1000 : 0;
    list.append(el("li", { "data-id": lb.id, class: i === 0 ? "active" : "", onclick: () => openLogbook(lb.id) },
      el("div", { text: lb.name || lb.id }),
      el("div", { class: "meta", text: [lb.pgroup, lb.location, touched ? `last active ${dayLabel(touched)}` : ""].filter(Boolean).join(" · ") })));
  });
  if (!shown.length) list.append(el("li", { class: "meta", text: "No logbook matches." }));
}

// -- the flow -----------------------------------------------------------------------------

function postElement(p) {
  const actions = el("span", { class: "actions" },
    el("button", { title: "Copy a link to this post", onclick: (ev) => { ev.stopPropagation(); copyLink(p.id); } }, "🔗"));
  const head = el("div", { class: "post-head" },
    el("span", { class: "time", text: clock(p.t), title: new Date(p.t * 1000).toString() }),
    el("span", { class: "author", text: shortName(p.author), title: p.author }),
    ...p.tags.map((t) => el("span", { class: "tag", text: t })),
    p.edited ? el("span", { class: "edited", text: "edited", title: `${p.editor || ""} ${p.updated}` }) : null,
    actions);
  const body = el("div", { class: "body" });
  body.innerHTML = p.html;  // cleaned by the server (content.clean_post)
  body.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((h, i) => (h.id = `h-${p.id}-${i}`));
  body.querySelectorAll(".plot").forEach(preparePlot);
  body.querySelectorAll("img").forEach((img) => img.addEventListener("click", () => lightbox(img.src)));
  return el("article", { class: "post", id: `p-${p.id}`, "data-id": p.id }, head, body);
}

function dayElement(t) {
  return el("h2", { class: "day", "data-day": dayKey(t) }, dayLabel(t), el("span", { class: "date", text: dayLabel(t) === fullDate(t) ? "" : shortDate(t) }));
}

function renderFlow() {
  const flow = $("#flow");
  const frag = document.createDocumentFragment();
  let day = null;
  for (const p of state.posts) {
    const k = dayKey(p.t);
    if (k !== day) { frag.append(dayElement(p.t)); day = k; }
    frag.append(postElement(p));
  }
  flow.replaceChildren(frag);
  if (!state.posts.length) flow.append(el("p", { class: "hint", text: "This logbook has no posts yet." }));
  observePosts();
}

function preparePlot(span) {
  const id = span.dataset.file, name = span.dataset.name || "plot";
  const bar = el("div", { class: "plot-bar" },
    el("span", { text: `📈 ${name}` }),
    el("button", { onclick: () => runPlot(span, id) }, "▶ Show the interactive plot"),
    el("a", { href: `file/${id}?download=1`, target: "_blank", rel: "noopener" }, "download"));
  span.replaceChildren(bar);
}

function runPlot(span, id) {
  // its own origin (the server sends CSP sandbox): no cookie, no access to this page
  const frame = el("iframe", { src: `file/${id}`, sandbox: "allow-scripts", loading: "lazy", title: span.dataset.name || "plot" });
  span.querySelector("button")?.remove();
  span.append(frame);
}

function lightbox(src) {
  const box = el("div", { id: "lightbox", onclick: () => box.remove() }, el("img", { src }));
  document.body.append(box);
}

// -- outline ---------------------------------------------------------------------------------

function renderOutline() {
  const tree = $("#outline-tree");
  const frag = document.createDocumentFragment();
  const months = [];
  for (const p of state.posts) {
    const mk = monthKey(p.t), dk = dayKey(p.t);
    let m = months[months.length - 1];
    if (!m || m.key !== mk) months.push((m = { key: mk, t: p.t, days: [] }));
    let d = m.days[m.days.length - 1];
    if (!d || d.key !== dk) m.days.push((d = { key: dk, t: p.t, posts: [] }));
    d.posts.push(p);
  }
  months.forEach((m, i) => {
    const recent = i >= months.length - 2;
    const node = el("div", { class: `o-month${recent ? "" : " o-collapsed"}` });
    const toggle = el("span", { class: "o-toggle", text: recent ? "▾" : "▸" });
    node.append(el("div", { class: "o-label", onclick: () => { node.classList.toggle("o-collapsed"); toggle.textContent = node.classList.contains("o-collapsed") ? "▸" : "▾"; } },
      toggle, `${monthLabel(m.t)} (${m.days.reduce((n, d) => n + d.posts.length, 0)})`));
    const days = el("div", { class: "o-children" });
    for (const d of m.days) {
      const dayNode = el("div", { class: "o-day" },
        el("div", { class: "o-label", onclick: () => jumpTo(document.querySelector(`.day[data-day="${d.key}"]`)), text: dayTitle(d.t) }));
      for (const p of d.posts) {
        dayNode.append(el("div", { class: "o-label o-post", "data-post": p.id, title: p.title, onclick: () => jumpToPost(p.id) }, `${clock(p.t)} ${p.title || (p.html.includes("<img") ? "🖼 image" : p.html.includes("plot") ? "📈 plot" : "(empty)")}`));
        const heads = [...p.html.matchAll(/<(h[1-6])\b[^>]*>(.*?)<\/h[1-6]>/gis)];
        heads.forEach((h, n) => {
          const text = h[2].replace(/<[^>]+>/g, "").trim();
          if (text && text !== p.title) dayNode.append(el("div", { class: `o-label o-head ${h[1].toLowerCase()}`, title: text, onclick: () => jumpTo(document.getElementById(`h-${p.id}-${n}`)), text }));
        });
      }
      days.append(dayNode);
    }
    node.append(days);
    frag.append(node);
  });
  tree.replaceChildren(frag);
}

let observer = null;
function observePosts() {
  if (observer) observer.disconnect();
  observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const id = e.target.dataset.id;
      document.querySelectorAll(".o-label.current").forEach((x) => x.classList.remove("current"));
      const item = document.querySelector(`.o-post[data-post="${id}"]`);
      if (item) {
        item.classList.add("current");
        const month = item.closest(".o-month");
        if (month && !month.classList.contains("o-collapsed")) item.scrollIntoView({ block: "nearest" });
      }
    }
  }, { root: $("#flow"), rootMargin: "-10% 0px -80% 0px" });
  document.querySelectorAll(".post").forEach((p) => observer.observe(p));
}

function jumpTo(target, flash = false) {
  if (!target) return;
  target.scrollIntoView({ block: "start" });  // scroll-margin-top keeps it below the day header
  if (flash) {
    const post = target.closest(".post") || target;
    post.classList.remove("flash");
    void post.offsetWidth;
    post.classList.add("flash");
  }
  if (window.matchMedia("(max-width: 760px)").matches) document.body.classList.remove("outline-open");
}

function revealInOutline(id) {
  const item = document.querySelector(`.o-post[data-post="${id}"]`);
  const month = item?.closest(".o-month");
  if (month?.classList.contains("o-collapsed")) {
    month.classList.remove("o-collapsed");
    month.querySelector(".o-toggle").textContent = "▾";
  }
  document.querySelectorAll(".o-label.current").forEach((x) => x.classList.remove("current"));
  item?.classList.add("current");
  item?.scrollIntoView({ block: "center" });
}

function jumpToPost(id, words) {
  const post = document.getElementById(`p-${id}`);
  if (!post) return;
  revealInOutline(id);
  clearMarks();
  if (words && words.length) markWords(post.querySelector(".body"), words);
  jumpTo(post, true);  // the post with its header first
  const first = post.querySelector("mark.hit");
  const flow = $("#flow");
  if (first && first.getBoundingClientRect().top > flow.getBoundingClientRect().top + flow.clientHeight * 0.75) {
    first.scrollIntoView({ block: "center" });  // a match far down a long post
  }
  history.replaceState(null, "", `#lb=${state.logbook.id}&post=${id}`);
}

// -- search -------------------------------------------------------------------------------------

const fold = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

function prepareSearch() {
  for (const p of state.posts) {
    p._text = fold(`${p.text}\n${p.author}\n${p.tags.join(" ")}`);
    p._words = null;
  }
}

function wordsOf(p) {
  if (!p._words) p._words = [...new Set(p._text.match(/[\p{L}\p{N}_.-]{2,}/gu) || [])];
  return p._words;
}

// Levenshtein distance, giving up above `max`
function within(a, b, max) {
  if (Math.abs(a.length - b.length) > max) return false;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let best = i;
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      best = Math.min(best, cur[j]);
    }
    if (best > max) return false;
    prev = cur;
  }
  return prev[b.length] <= max;
}

function parseQuery(q) {
  const terms = [];
  q = fold(q).replace(/"([^"]+)"/g, (_, phrase) => { terms.push({ text: phrase.trim(), exact: true }); return " "; });
  for (const w of q.split(/\s+/)) if (w.length >= 2) terms.push({ text: w, exact: false });
  return terms;
}

// every term must match: as a substring, or (4+ letters) a word within 1 typo (8+: 2)
function search(q) {
  const terms = parseQuery(q);
  if (!terms.length) return [];
  const newest = state.posts.length ? state.posts[state.posts.length - 1].t : 0;
  const out = [];
  for (const p of state.posts) {
    let score = 0;
    const matched = [];
    let ok = true;
    for (const term of terms) {
      const count = p._text.split(term.text).length - 1;
      if (count) {
        score += 3 + Math.log2(1 + count);
        matched.push(term.text);
        continue;
      }
      if (term.exact || term.text.length < 4) { ok = false; break; }
      const max = term.text.length >= 8 ? 2 : 1;
      const near = wordsOf(p).filter((w) => within(term.text, w.slice(0, term.text.length + max), max));
      if (!near.length) { ok = false; break; }
      score += 1.5;
      matched.push(...near.slice(0, 5));
    }
    if (ok) out.push({ post: p, score: score + 0.5 * (p.t / (newest || 1)), words: matched });
  }
  return out.sort((a, b) => b.score - a.score).slice(0, 60);
}

function escapeHtml(s) { return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }
function wordsRegex(words) {
  const parts = [...new Set(words)].sort((a, b) => b.length - a.length).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return parts.length ? new RegExp(`(${parts.join("|")})`, "gi") : null;
}

function snippet(p, words) {
  const text = p.text.replace(/\s+/g, " ");
  const low = fold(text);
  let at = -1;
  for (const w of words) { const i = low.indexOf(w); if (i >= 0 && (at < 0 || i < at)) at = i; }
  const start = Math.max(0, at - 70), end = Math.min(text.length, (at < 0 ? 0 : at) + 150);
  let s = escapeHtml(text.slice(start, end));
  const rx = wordsRegex(words.map(escapeHtml));
  if (rx) s = s.replace(rx, '<mark class="hit">$1</mark>');
  return (start > 0 ? "… " : "") + s + (end < text.length ? " …" : "");
}

function showResults(q) {
  const box = $("#results");
  state.results = q.trim() ? search(q) : [];
  state.active = state.results.length ? 0 : -1;
  if (!q.trim()) { closeResults(); return; }
  box.replaceChildren(el("div", { class: "count", text: state.results.length ? `${state.results.length}${state.results.length === 60 ? "+" : ""} posts · ↑↓ Enter · typos are allowed, "quotes" for exact` : "Nothing found." }));
  state.results.forEach((r, i) => {
    const item = el("div", { class: `result${i === 0 ? " active" : ""}`, onclick: () => openResult(i) },
      el("div", { class: "meta", text: `${dayTitle(r.post.t)} ${clock(r.post.t)} · ${shortName(r.post.author)}` }));
    const snip = el("div", { class: "snippet" });
    snip.innerHTML = snippet(r.post, r.words);
    item.append(snip);
    box.append(item);
  });
  box.hidden = false;
  document.body.classList.add("searching");
}

const docked = () => window.matchMedia("(min-width: 1100px)").matches;
function closeResults() { $("#results").hidden = true; document.body.classList.remove("searching"); }

function openResult(i) {
  const r = state.results[i];
  if (!r) return;
  state.active = i;
  document.querySelectorAll("#results .result").forEach((x, n) => x.classList.toggle("active", n === i));
  jumpToPost(r.post.id, r.words);
  if (!docked()) closeResults();  // an overlay would cover the post
}

function clearMarks() {
  document.querySelectorAll("#flow mark.hit").forEach((m) => m.replaceWith(...m.childNodes));
}

function markWords(root, words) {
  const rx = wordsRegex(words);
  if (!rx || !root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    if (!rx.test(fold(text))) continue;
    rx.lastIndex = 0;
    const lowered = fold(text);
    const frag = document.createDocumentFragment();
    let last = 0, m;
    while ((m = rx.exec(lowered))) {
      frag.append(text.slice(last, m.index), el("mark", { class: "hit", text: text.slice(m.index, m.index + m[0].length) }));
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

// -- loading and live updates ------------------------------------------------------------------

async function openLogbook(id, postId) {
  showPicker(false);
  const lb = state.logbooks.find((x) => x.id === id) || { id, name: id };
  state.logbook = lb;
  storageSet("eco-scilog-last", id);
  $("#logbook-name").textContent = lb.name || id;
  document.title = `${lb.name || id} · SciLog viewer`;
  const link = $("#scilog-link");
  link.href = lb.url || `https://scilog.psi.ch/logbooks/${id}/dashboard`;
  link.hidden = false;
  $("#flow").replaceChildren(el("p", { class: "hint", text: "Reading the logbook from SciLog ..." }));
  clearInterval(state.poll);
  try {
    const data = await api(`/api/posts?logbook=${id}`);
    state.posts = data.posts;
    state.since = data.since;
  } catch (e) {
    $("#flow").replaceChildren(el("p", { class: "hint", text: `SciLog could not be read: ${e.message}` }));
    return;
  }
  state.byId = new Map(state.posts.map((p) => [p.id, p]));
  prepareSearch();
  renderFlow();
  renderOutline();
  setStatus();
  if (postId && state.byId.has(postId)) jumpToPost(postId);
  else holdBottom();
  history.replaceState(null, "", `#lb=${id}${postId ? `&post=${postId}` : ""}`);
  state.poll = setInterval(refresh, POLL_MS);
}

// The newest post is at the bottom. Images and posts only get their real
// height when they come into view, so the bottom moves for a while: follow it
// until the user scrolls.
function holdBottom() {
  const flow = $("#flow");
  let mine = true;
  const toBottom = () => { if (mine) flow.scrollTop = flow.scrollHeight; };
  const stop = () => { mine = false; resize.disconnect(); };
  const resize = new ResizeObserver(toBottom);
  [...flow.children].slice(-30).forEach((c) => resize.observe(c));
  ["wheel", "touchstart", "keydown", "mousedown"].forEach((ev) => flow.addEventListener(ev, stop, { once: true }));
  toBottom();
  setTimeout(stop, 8000);
}

async function refresh() {
  if (!state.logbook || document.hidden) return;
  let data;
  try { data = await api(`/api/posts?logbook=${state.logbook.id}&since=${encodeURIComponent(state.since || "")}`); }
  catch (e) { setStatus(`offline: ${e.message}`); return; }
  state.since = data.since;
  if (!data.posts.length && !data.removed.length) { setStatus(); return; }
  const flow = $("#flow");
  const atBottom = flow.scrollHeight - flow.scrollTop - flow.clientHeight < 80;
  let appended = 0;
  for (const id of data.removed) { state.byId.delete(id); document.getElementById(`p-${id}`)?.remove(); }
  for (const p of data.posts) {
    const old = state.byId.get(p.id);
    state.byId.set(p.id, p);
    if (old) document.getElementById(`p-${p.id}`)?.replaceWith(postElement(p));
    else appended++;
  }
  state.posts = [...state.byId.values()].sort((a, b) => a.t - b.t);
  prepareSearch();
  if (appended) {
    renderFlow();
    if (atBottom) flow.scrollTop = flow.scrollHeight;
    toast(`${appended} new post${appended > 1 ? "s" : ""}`);
  }
  renderOutline();
  observePosts();
  setStatus();
}

function setStatus(extra) {
  const now = new Date();
  $("#status").textContent = extra || `${state.posts.length} posts · ${state.identity || ""} · ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
}

function copyLink(id) {
  const url = `${location.origin}/#lb=${state.logbook.id}&post=${id}`;
  navigator.clipboard?.writeText(url).then(() => toast("Link copied"), () => prompt("Link to this post:", url));
  history.replaceState(null, "", `#lb=${state.logbook.id}&post=${id}`);
}

function fromHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  return { lb: h.get("lb"), post: h.get("post") };
}

// -- start ---------------------------------------------------------------------------------------

async function main() {
  $("#toggle-outline").onclick = () => {
    if (window.matchMedia("(max-width: 760px)").matches) document.body.classList.toggle("outline-open");
    else { document.body.classList.toggle("no-outline"); storageSet("eco-scilog-outline", document.body.classList.contains("no-outline") ? "off" : "on"); }
  };
  if (storageGet("eco-scilog-outline") === "off") document.body.classList.add("no-outline");
  $("#logbook-button").onclick = () => showPicker($("#picker").hidden);
  $("#picker-filter").oninput = (e) => fillPicker(e.target.value);
  $("#picker-filter").onkeydown = (e) => {
    const items = [...document.querySelectorAll("#picker-list li[data-id]")];
    let i = items.findIndex((x) => x.classList.contains("active"));
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      items[i]?.classList.remove("active");
      i = Math.max(0, Math.min(items.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
      items[i]?.classList.add("active");
      items[i]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && items[i]) openLogbook(items[i].dataset.id);
    else if (e.key === "Escape") showPicker(false);
  };
  let timer = null;
  const input = $("#search");
  input.oninput = () => { clearTimeout(timer); timer = setTimeout(() => showResults(input.value), 120); };
  input.onfocus = () => { if (input.value.trim()) showResults(input.value); };
  input.onkeydown = (e) => {
    const n = state.results.length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!n) return;
      state.active = (state.active + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
      document.querySelectorAll("#results .result").forEach((x, k) => x.classList.toggle("active", k === state.active));
      document.querySelectorAll("#results .result")[state.active]?.scrollIntoView({ block: "nearest" });
      if (docked()) openResult(state.active);  // the post beside the list follows
    } else if (e.key === "Enter") { e.preventDefault(); openResult(state.active); }
    else if (e.key === "Escape") { closeResults(); input.blur(); }
  };
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#search-wrap, #flow") || (!docked() && !e.target.closest("#search-wrap"))) closeResults();
    if (!e.target.closest("#picker, #logbook-button")) $("#picker").hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, [contenteditable]")) return;
    if (e.key === "/") { e.preventDefault(); input.focus(); input.select(); }
    else if (e.key === "o") $("#toggle-outline").click();
    else if (e.key === "l") showPicker(true);
    else if (e.key === "Escape") { $("#picker").hidden = true; document.getElementById("lightbox")?.remove(); }
  });
  window.addEventListener("hashchange", () => {
    const { lb, post } = fromHash();
    if (lb && (!state.logbook || lb !== state.logbook.id)) openLogbook(lb, post);
    else if (post) jumpToPost(post);
  });

  try {
    const [me, books] = await Promise.all([api("/api/me"), api("/api/logbooks")]);
    state.identity = me.identity.split("<")[0].trim();
    state.logbooks = books.logbooks;
  } catch (e) {
    $("#flow").replaceChildren(el("p", { class: "hint", text: `SciLog could not be read: ${e.message}` }));
    return;
  }
  const { lb, post } = fromHash();
  const last = lb || storageGet("eco-scilog-last");
  if (last && state.logbooks.some((x) => x.id === last)) openLogbook(last, post);
  else { $("#flow").replaceChildren(el("p", { class: "hint", text: "Choose a logbook: the most recently active come first." })); showPicker(true); }
}

main();
