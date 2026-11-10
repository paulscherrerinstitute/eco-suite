// eco SciLog viewer: one logbook as a flow of days, an outline, fuzzy search.
// Data come from the local eco server (server.py), which talks to SciLog.
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { logbooks: [], logbook: null, posts: [], all: [], byId: new Map(), since: null, poll: null, results: [], active: -1,
                me: {}, view: "all", only: null };
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

// SciLog refuses changes once a post's edit period is over (3 days unless it was given another)
function editable(p) { return p.can_edit && (!p.expires || p.expires * 1000 > Date.now()); }
function editableUntil(p) { return p.expires ? `${fullDate(p.expires)} ${clock(p.expires)}` : ""; }

function postElement(p, pinned = false) {
  const actions = el("span", { class: "actions" },
    el("button", { class: "more", title: "Edit, insert, link, share, pin …", "aria-label": "Actions for this post",
      onclick: (ev) => { ev.stopPropagation(); openPostMenu(p, ev.currentTarget, pinned); } }, "⋯"));
  const head = el("div", { class: "post-head" },
    el("span", { class: "time", text: clock(p.t), title: new Date(p.t * 1000).toString() }),
    el("span", { class: "author", text: shortName(p.author), title: p.author }),
    ...p.tags.map((t) => el("span", { class: "tag", text: t })),
    p.edited ? el("span", { class: "edited", text: "edited", title: `${p.editor || ""} ${p.updated}` }) : null,
    p.shared ? el("span", { class: "shared", text: "🌐", title: "Readable by everyone with a PSI account" }) : null,
    p.inserted ? el("span", { class: "inserted", text: `inserted ${shortDate(p.written)} ${clock(p.written)}`, title: `Written ${fullDate(p.written)} ${clock(p.written)}, placed here afterwards` }) : null,
    actions);
  const body = el("div", { class: "body" });
  if (editable(p) && !pinned) {
    body.title = "Double-click to edit";
    body.addEventListener("dblclick", (ev) => {
      if (ev.target.closest("a, button, iframe, img")) return;
      if (canWrite()) { window.getSelection()?.removeAllRanges(); editPost(p.id); }
      else askToAllowWriting();
    });
  }
  body.innerHTML = p.html;  // cleaned by the server (content.clean_post)
  body.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((h, i) => (h.id = `${pinned ? "pin-" : ""}h-${p.id}-${i}`));
  body.querySelectorAll(".plot").forEach(preparePlot);
  body.querySelectorAll("img").forEach((img) => img.addEventListener("click", () => lightbox(img.src)));
  return el("article", { class: "post", id: `${pinned ? "pin-" : ""}p-${p.id}`, "data-id": p.id },
    head, p.quote ? replyElement(p) : null, body, p.comments?.length ? commentsElement(p) : null);
}

// a reply: what it answers, as a one-line teaser that unfolds
function replyElement(p) {
  const q = p.quote, original = q.of ? state.byId.get(q.of) : null;
  const who = original ? shortName(original.author) : "";
  const teaser = q.text.replace(/\s+/g, " ").trim();
  const full = el("blockquote", { class: "r-full" });
  full.innerHTML = q.html;
  full.querySelectorAll("img").forEach((img) => img.addEventListener("click", (ev) => { ev.stopPropagation(); lightbox(img.src); }));
  const box = el("div", { class: "reply", title: "Click to unfold what this replies to" },
    el("div", { class: "r-teaser" }, el("span", { class: "r-arrow", text: "↩" }),
      el("span", { class: "r-who", text: `Reply${who ? ` to ${who}` : ""}${original ? ` (${shortDate(original.t)} ${clock(original.t)})` : ""}: ` }),
      el("span", { class: "r-text", text: teaser.slice(0, 140) + (teaser.length > 140 ? " …" : "") || "(image)" })),
    full,
    original ? el("button", { class: "r-go", onclick: (ev) => { ev.stopPropagation(); jumpToPost(original.id); } }, "go to the original ↑") : null);
  box.addEventListener("click", (ev) => { if (!ev.target.closest("a, button, img")) box.classList.toggle("open"); });
  return box;
}

// comments: a chip that shows or hides them (c: all at once)
function commentsElement(p) {
  const n = p.comments.length;
  const list = el("div", { class: "c-list" }, ...p.comments.map((c) => {
    const body = el("div", { class: "c-body" });
    body.innerHTML = c.html;
    body.querySelectorAll("img").forEach((img) => img.addEventListener("click", () => lightbox(img.src)));
    const tools = el("span", { class: "c-tools" },
      c.can_edit ? (editable(c)
        ? el("button", { title: `Edit this comment (until ${editableUntil(c)})`, onclick: () => editComment(p, c) }, "✏️")
        : el("button", { disabled: "", title: `SciLog allowed changes only until ${editableUntil(c)}` }, "✏️")) : null,
      c.can_delete ? el("button", { title: "Delete this comment", onclick: () => deleteComment(p, c) }, "🗑") : null);
    return el("div", { class: "comment", id: `c-${c.id}` },
      el("div", { class: "c-head" }, el("strong", { text: shortName(c.author), title: c.author }), ` ${shortDate(c.t)} ${clock(c.t)}`,
        c.edited ? el("span", { class: "edited", text: " edited", title: `${c.editor || ""} ${c.updated}` }) : null, tools), body);
  }));
  const box = el("div", { class: `comments${state.commentsOpen ? " open" : ""}` });
  const chip = el("button", { class: "c-chip", title: "Show or hide the comments (c: all of them)", onclick: () => box.classList.toggle("open") },
    `💬 ${n} comment${n > 1 ? "s" : ""}`);
  box.append(chip, list);
  return box;
}

function dayElement(t) {
  return el("h2", { class: "day", "data-day": dayKey(t) }, dayLabel(t), el("span", { class: "date", text: dayLabel(t) === fullDate(t) ? "" : shortDate(t) }));
}

// tools that stay at the top right of the flow, level with the day header stuck there
// (a zero-height sticky strip, so the day headers further down carry nothing)
function flowTools() {
  const on = document.body.classList.contains("thumbs");
  return el("div", { id: "flow-tools" },
    el("button", { class: "day-thumbs", title: "Small figures: every image as a thumbnail (click one to see it large)", "aria-pressed": String(on),
      onclick: (ev) => { ev.stopPropagation(); setThumbs(!document.body.classList.contains("thumbs")); } }, "🖼"));
}

// all figures as thumbnails, or as their authors sized them; the reading position stays on the same post
function setThumbs(on) {
  const flow = $("#flow");
  const anchor = [...flow.querySelectorAll(".post")].find((p) => p.getBoundingClientRect().bottom > flow.getBoundingClientRect().top + 60);
  const offset = anchor ? anchor.getBoundingClientRect().top : 0;
  document.body.classList.toggle("thumbs", on);
  document.querySelectorAll(".day-thumbs").forEach((b) => b.setAttribute("aria-pressed", String(on)));
  storageSet("eco-scilog-thumbs", on ? "on" : "off");
  if (anchor) flow.scrollTop += anchor.getBoundingClientRect().top - offset;
}

// -- views: which posts are shown ---------------------------------------------------------

function applyView() {
  const v = state.view;
  state.posts = state.all.filter((p) =>
    state.only ? p.id === state.only
      : v === "mine" ? p.author === state.me.email
      : v.startsWith("tag:") ? p.tags.includes(v.slice(4))
      : true);
}

function fillViews() {
  const select = $("#view");
  const counts = new Map();
  for (const p of state.all) for (const t of p.tags) counts.set(t, (counts.get(t) || 0) + 1);
  const tags = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 30);
  select.replaceChildren(
    el("option", { value: "all", text: `All posts (${state.all.length})` }),
    el("option", { value: "mine", text: `My posts (${state.all.filter((p) => p.author === state.me.email).length})` }),
    ...tags.map(([t, n]) => el("option", { value: `tag:${t}`, text: `# ${t} (${n})` })),
    el("option", { value: "__views", text: "⚙ SciLog views …" }));
  if (![...select.options].some((o) => o.value === state.view)) state.view = "all";
  select.value = state.view;
  select.hidden = false;
  $("#views-button").hidden = false;
}

function setView(view) {
  state.view = view;
  storageSet(`eco-scilog-view:${state.logbook.id}`, view);
  applyView();
  prepareSearch();
  renderFlow();
  renderOutline();
  holdBottom();
  setStatus();
}

function showOnly(id) {
  state.only = id;
  applyView();
  renderFlow();
  renderOutline();
  const banner = $("#only-banner");
  banner.replaceChildren("Only this post is shown · ",
    el("a", { href: "#", onclick: (e) => { e.preventDefault(); showInContext(id); } }, "show it in its context"));
  banner.hidden = false;
  const url = `${location.origin}/#lb=${state.logbook.id}&post=${id}&only=1`;
  history.replaceState(null, "", `#lb=${state.logbook.id}&post=${id}&only=1`);
  navigator.clipboard?.writeText(url).then(() => toast("Link to this post alone copied"), () => {});
}

function showInContext(id) {
  state.only = null;
  $("#only-banner").hidden = true;
  applyView();
  renderFlow();
  renderOutline();
  jumpToPost(id);
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
  flow.replaceChildren(flowTools(), frag);
  if (!state.posts.length) flow.append(el("p", { class: "hint", text: "This logbook has no posts yet." }));
  if (!state.only) flow.append(composerElement());
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
        dayNode.append(el("div", { class: "o-label o-post", "data-post": p.id, title: p.title, onclick: () => jumpToPost(p.id) },
          `${clock(p.t)} `, ...postMarks(p), `${p.title || (p.html.includes("<img") ? "🖼 image" : p.html.includes("plot") ? "📈 plot" : "(empty)")}`));
        const heads = [...p.html.matchAll(/<(h[1-6])\b[^>]*>(.*?)<\/h[1-6]>/gis)];
        heads.forEach((h, n) => {
          const text = h[2].replace(/<[^>]+>/g, "").trim();
          if (!text || (n === 0 && text === p.title)) return;  // the post's own title is the line above
          const level = h[1].toLowerCase();
          const item = el("div", { class: `o-label o-head ${level}`, title: text, onclick: () => jumpTo(document.getElementById(`h-${p.id}-${n}`)) }, text);
          if (p.can_edit && canWrite()) {
            item.append(el("button", { class: "o-level", title: "Change this heading's level",
              onclick: (ev) => { ev.stopPropagation(); headingMenu(ev, p.id, n, level); } }, level.toUpperCase()));
          }
          dayNode.append(item);
        });
      }
      days.append(dayNode);
    }
    node.append(days);
    frag.append(node);
  });
  tree.replaceChildren(frag);
  if (typeof renderMap === "function") renderMap();
  if (typeof renderHeadings === "function") renderHeadings();
}

// small marks for replies, comments and inserted posts (outline, map, headings)
function postMarks(p) {
  return [
    p.quote ? el("span", { class: "mark-reply", text: "↩", title: "a reply" }) : null,
    p.comments?.length ? el("span", { class: "mark-comments", text: `💬${p.comments.length}`, title: `${p.comments.length} comment${p.comments.length > 1 ? "s" : ""}` }) : null,
    p.inserted ? el("span", { class: "mark-inserted", text: "⤓", title: "inserted afterwards" }) : null,
  ].filter(Boolean);
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
  document.querySelectorAll("#flow .post").forEach((p) => observer.observe(p));
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

// The search runs in a worker (search-worker.js), so a large logbook never freezes the page;
// where workers are not available, here (search() below).
let searchWorker = null, searchSeq = 0;
const searchPending = new Map();
function startSearchWorker() {
  try {
    searchWorker = new Worker("/static/search-worker.js");
    searchWorker.onmessage = searchAnswered;
    searchWorker.onerror = () => { searchWorker = null; searchPending.forEach((w) => w.resolve()); searchPending.clear(); prepareSearch(); };
  } catch (e) { searchWorker = null; }
}

function prepareSearch() {
  if (searchWorker === null && !prepareSearch.tried) { prepareSearch.tried = true; startSearchWorker(); }
  if (searchWorker) {
    searchWorker.postMessage({ posts: state.posts.map((p) => ({ id: p.id, t: p.t, text: `${p.text}\n${p.author}\n${p.tags.join(" ")}` })) });
    return;
  }
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

// returns a promise that is fulfilled once the results are shown
function showResults(q) {
  if (!q.trim()) { state.results = []; state.active = -1; closeResults(); return Promise.resolve(); }
  if (!searchWorker) { renderResults(search(q)); return Promise.resolve(); }
  const seq = ++searchSeq;
  // only a search that takes a moment says so: a quick one would just flicker
  const slow = setTimeout(() => { if (seq === searchSeq) searchingSign(); }, 150);
  return new Promise((resolve) => {
    searchPending.set(seq, { resolve, slow });
    searchWorker.postMessage({ seq, q });
  });
}

function searchAnswered(e) {
  const { seq, results } = e.data;
  const waiting = searchPending.get(seq);
  searchPending.delete(seq);
  if (!waiting) return;
  clearTimeout(waiting.slow);
  if (seq === searchSeq && $("#search").value.trim()) {  // an older answer is not shown over a newer question
    renderResults(results.map((r) => ({ post: state.byId.get(r.id), score: r.score, words: r.words })).filter((r) => r.post));
  }
  waiting.resolve();
}

function searchingSign() {
  const box = $("#results");
  box.replaceChildren(el("div", { class: "count" }, el("span", { class: "spinner", "aria-hidden": "true" }), " Searching …"));
  box.hidden = false;
  document.body.classList.add("searching");
}

function renderResults(results) {
  const box = $("#results");
  state.results = results;
  state.active = state.results.length ? 0 : -1;
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

// narrow screens: the search takes the whole bar and its results the whole page while it is in use
const narrow = () => window.matchMedia("(max-width: 760px)").matches;
function openSearch() {
  $("#bar").classList.add("search-open");
  const input = $("#search");
  const focused = document.activeElement === input;
  input.focus();  // its focus handler shows the results of what is typed there
  if (focused && input.value.trim()) showResults(input.value);
}
function endSearch() {
  state.fromSearch = false;
  closeResults();
  $("#bar").classList.remove("search-open");
  $("#search").blur();
}

function openResult(i) {
  const r = state.results[i];
  if (!r) return;
  state.active = i;
  document.querySelectorAll("#results .result").forEach((x, n) => x.classList.toggle("active", n === i));
  jumpToPost(r.post.id, r.words);
  if (narrow()) endSearch();  // keyboard away, the post on the whole screen
  else if (!docked()) closeResults();  // an overlay would cover the post
  state.fromSearch = !docked();  // a phone's Back brings the results back
}

// the results again, as they were, without the keyboard popping up
function reopenResults() {
  const q = $("#search").value;
  if (!q.trim()) return false;
  if (narrow()) $("#bar").classList.add("search-open");
  const was = state.active;
  showResults(q).then(() => {
    state.active = Math.min(Math.max(was, 0), state.results.length - 1);
    const items = document.querySelectorAll("#results .result");
    items.forEach((x, n) => x.classList.toggle("active", n === state.active));
    items[state.active]?.scrollIntoView({ block: "nearest" });
  });
  return true;
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

async function openLogbook(id, postId, only = false) {
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
    state.all = data.posts;
    state.since = data.since;
  } catch (e) {
    $("#flow").replaceChildren(el("p", { class: "hint", text: `SciLog could not be read: ${e.message}` }));
    return;
  }
  state.byId = new Map(state.all.map((p) => [p.id, p]));
  state.view = storageGet(`eco-scilog-view:${id}`) || "all";
  state.only = only && postId ? postId : null;
  fillViews();
  applyView();
  if (postId && !state.posts.some((p) => p.id === postId)) { state.view = "all"; $("#view").value = "all"; applyView(); }
  prepareSearch();
  renderFlow();
  renderOutline();
  setStatus();
  updateWriteUi();
  preloadEditor();
  loadTasks(true);
  if (!narrow() && storageGet("eco-scilog-tasks") === "open") openTasks(true);
  $("#only-banner").hidden = true;
  if (state.only) showOnly(postId);
  else if (postId && state.byId.has(postId)) jumpToPost(postId);
  else holdBottom();
  if (!state.only) history.replaceState(null, "", `#lb=${id}${postId ? `&post=${postId}` : ""}`);
  state.poll = setInterval(refresh, POLL_MS);
}

// -- to the newest post ------------------------------------------------------------------------

function toNewest() {
  const flow = $("#flow");
  flow.scrollTo({ top: flow.scrollHeight, behavior: "smooth" });
  setTimeout(holdBottom, 400);  // lazily shown posts keep growing the bottom
}

function updateToBottom() {
  const flow = $("#flow"), button = $("#to-bottom");
  const far = flow.scrollHeight - flow.scrollTop - flow.clientHeight > flow.clientHeight;
  const n = state.unseen?.size || 0;
  button.classList.toggle("news", n > 0);
  button.innerHTML = n ? `&#8595; ${n} new post${n > 1 ? "s" : ""}` : "&#8595; Newest";
  button.title = n ? "Posts written meanwhile, from elsewhere: go to the first one" : "To the newest post (End)";
  button.hidden = !far && !n;
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

// Posts from elsewhere never move what is being read: the post at the top of the
// flow is kept where it is across the redraw, and new posts are announced by the
// "new posts" sign (bottom right) and a mark on them until they have been seen.
function readingAnchor() {
  const flow = $("#flow"), top = flow.getBoundingClientRect().top;
  const node = [...flow.querySelectorAll(".post")].find((x) => x.getBoundingClientRect().bottom > top + 1);
  return node ? { id: node.id, offset: node.getBoundingClientRect().top - top } : null;
}

function keepReading(anchor) {
  const node = anchor && document.getElementById(anchor.id);
  if (!node) return;
  const flow = $("#flow");
  flow.scrollTop += node.getBoundingClientRect().top - flow.getBoundingClientRect().top - anchor.offset;
}

state.unseen = new Set();
let unseenObserver = null;
function watchUnseen() {
  if (unseenObserver) unseenObserver.disconnect();
  unseenObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const id = e.target.dataset.id;
      state.unseen.delete(id);
      unseenObserver.unobserve(e.target);
      setTimeout(() => e.target.classList.remove("fresh"), 4000);  // the mark fades once seen
    }
    updateToBottom();
  }, { root: $("#flow"), threshold: 0.3 });
  for (const id of state.unseen) {
    const node = document.getElementById(`p-${id}`);
    if (node) { node.classList.add("fresh"); unseenObserver.observe(node); }
  }
  updateToBottom();
}

function toUnseen() {
  const first = state.posts.find((p) => state.unseen.has(p.id));
  if (first) jumpToPost(first.id); else toNewest();
}

// a post put into the flow where it belongs, without redrawing the others (so
// the heights the browser has measured, and with them the reading position, stay)
function placeInFlow(p) {
  if (!state.posts.some((x) => x.id === p.id)) return;  // not in the current view
  const flow = $("#flow");
  const i = state.posts.findIndex((x) => x.id === p.id);
  const prev = state.posts[i - 1], next = state.posts.slice(i + 1).find((x) => document.getElementById(`p-${x.id}`));
  const node = postElement(p);
  const nextNode = next && document.getElementById(`p-${next.id}`);
  const parts = [];
  if (!prev || dayKey(prev.t) !== dayKey(p.t)) {
    const existing = flow.querySelector(`.day[data-day="${dayKey(p.t)}"]`);
    if (!existing) parts.push(dayElement(p.t));
  }
  parts.push(node);
  if (nextNode) {
    // before the next post, or before its day header when the next post starts another day
    const header = nextNode.previousElementSibling;
    const before = next && dayKey(next.t) !== dayKey(p.t) && header?.classList.contains("day") ? header : nextNode;
    before.before(...parts);
  } else {
    const composer = document.getElementById("composer");
    if (composer) composer.before(...parts); else flow.append(...parts);
  }
  flow.querySelector(".hint")?.remove();
}

// the outline redrawn once the page has answered (it takes a while for a large logbook)
let outlineTimer = null;
function laterOutline() {
  clearTimeout(outlineTimer);
  outlineTimer = setTimeout(() => { outlineTimer = null; renderOutline(); }, 60);
}

async function refresh() {
  if (!state.logbook || document.hidden) return;
  let data;
  try { data = await api(`/api/posts?logbook=${state.logbook.id}&since=${encodeURIComponent(state.since || "")}`); }
  catch (e) {
    if (/temporary token|choose whose/i.test(e.message)) { location.reload(); return; }  // a lent token ran out: the choice again
    setStatus(`offline: ${e.message}`); return;
  }
  state.since = data.since;
  if (!data.posts.length && !data.removed.length) { setStatus(); return; }
  const anchor = readingAnchor();
  let appended = 0;
  for (const id of data.removed) { state.byId.delete(id); state.unseen.delete(id); document.getElementById(`p-${id}`)?.remove(); }
  for (const p of data.posts) {
    const old = state.byId.get(p.id);
    state.byId.set(p.id, p);
    if (old) document.getElementById(`p-${p.id}`)?.replaceWith(postElement(p));
    else { appended++; state.unseen.add(p.id); }
  }
  state.all = [...state.byId.values()].sort((a, b) => a.t - b.t);
  applyView();
  fillViews();
  prepareSearch();
  if (appended) for (const p of data.posts) if (!document.getElementById(`p-${p.id}`)) placeInFlow(p);
  keepReading(anchor);
  laterOutline();
  observePosts();
  watchUnseen();
  setStatus();
}

// -- writing (editor.js) ------------------------------------------------------------------------

function canWrite() {
  const me = state.me, lb = state.logbook;
  if (!me.writes || !lb) return false;
  return me.write_logbooks === "all" || (me.write_logbooks || []).includes(lb.id);
}

function editPost(id) {
  openEditor({ logbook: state.logbook.id, logbookName: state.logbook.name, postId: id, onSaved: placeSaved });
}

// Where writing is not switched on, say why and how instead of hiding it.
function writeBlocker() {
  const me = state.me;
  if (!state.logbook) return "no logbook";
  if (!me.writes) return me.why_not || "writing is off";
  return canWrite() ? "" : "off-here";
}

function updateWriteUi() {
  const button = $("#new-post"), why = writeBlocker();
  button.hidden = !state.logbook || (why && why !== "off-here");
  button.innerHTML = why === "off-here" ? '&#9998;<span class="lbl"> Allow writing here</span>' : '&#10010;<span class="lbl"> New post</span>';
  button.title = why === "off-here" ? "Writing to this logbook is off: switch it on to post and to edit your own posts" : "Write a new post (n)";
  const old = document.getElementById("composer");
  if (old) old.replaceWith(composerElement());
}

function composerElement() {
  const why = writeBlocker();
  if (!why) {
    return el("div", { id: "composer", tabindex: "0", role: "button", onclick: newPost,
      onkeydown: (e) => { if (e.key === "Enter") newPost(); } },
      el("span", { class: "c-box", text: "Write a new post ..." }),
      el("span", { class: "c-hint", text: "n · double-click one of your posts to edit it" }));
  }
  if (why === "off-here") {
    return el("div", { id: "composer", class: "off" },
      el("span", { text: "Writing to this logbook is off. " }),
      el("button", { onclick: askToAllowWriting }, "✎ Allow writing here"));
  }
  return el("div", { id: "composer", class: "off" }, el("span", { class: "c-hint", text: `Writing is not possible: ${why}.` }));
}

async function askToAllowWriting() {
  if (writeBlocker() !== "off-here") return;
  if (!confirm(`Allow writing to "${state.logbook.name}"?\n\nYou can then post here and edit your own posts. ` +
               "This is remembered for you (in ~/.eco/scilog_app.json) and can be switched off again there.")) return;
  try {
    state.me = await writeApi("/api/write-logbook", "POST", { logbook: state.logbook.id, on: true });
  } catch (e) { toast(`Not switched on: ${e.message}`); return; }
  renderFlow();
  renderOutline();
  updateWriteUi();
  preloadEditor();
  setStatus();
  toast("Writing switched on for this logbook");
}

function newPost() {
  if (writeBlocker() === "off-here") { askToAllowWriting(); return; }
  if (!canWrite()) return;
  openEditor({ logbook: state.logbook.id, logbookName: state.logbook.name, onSaved: placeSaved });
}

function insertNear(id, where = "after") {
  if (!canWrite()) { askToAllowWriting(); return; }
  const p = state.byId.get(id);
  openEditor({ logbook: state.logbook.id, logbookName: state.logbook.name, onSaved: placeSaved,
    near: { id, where, label: `${dayTitle(p.t)} ${clock(p.t)} ${p.title ? `“${p.title.slice(0, 50)}”` : ""}` } });
}

function commentOn(id) {
  if (!canWrite()) { askToAllowWriting(); return; }
  const p = state.byId.get(id);
  openEditor({ logbook: state.logbook.id, logbookName: state.logbook.name, onSaved: (post) => placeSaved(post, "Comment saved", true),
    comment: { id, tags: p.tags, label: postLabel(p) } });
}

function replyTo(id) {
  if (!canWrite()) { askToAllowWriting(); return; }
  const p = state.byId.get(id);
  openEditor({ logbook: state.logbook.id, logbookName: state.logbook.name, onSaved: placeSaved,
    reply: { id, tags: p.tags, label: postLabel(p) } });
}

function editComment(p, c) {
  if (!canWrite()) { askToAllowWriting(); return; }
  openEditor({ logbook: state.logbook.id, logbookName: state.logbook.name, postId: c.id, isComment: true,
    onSaved: (post) => placeSaved(post, "Comment saved", true) });
}

async function deleteComment(p, c) {
  if (!canWrite()) { askToAllowWriting(); return; }
  const teaser = c.text.replace(/\s+/g, " ").trim().slice(0, 80);
  if (!confirm(`Delete this comment from SciLog?\n\n${shortName(c.author)}, ${shortDate(c.t)} ${clock(c.t)}: “${teaser}”\n\nSciLog keeps it in the logbook's history, from where it can be restored.`)) return;
  try {
    const r = await writeApi(`/api/delete/${c.id}`, "POST", { logbook: state.logbook.id });
    if (r.post) placeSaved(r.post, "Comment deleted", true);
  } catch (e) { toast(`Not deleted: ${e.message}`); }
}

function postLabel(p) { return `${dayTitle(p.t)} ${clock(p.t)} ${shortName(p.author)}${p.title ? ` “${p.title.slice(0, 50)}”` : ""}`; }

function showInfo(p) {
  const when = (t) => (t ? `${fullDate(t)} ${clock(t)}` : "");
  const iso = (s) => (s ? when(Date.parse(s) / 1000) : "");
  const rows = [
    ["Written by", p.author],
    ["Written", iso(p.created)],
    p.inserted ? ["Placed at", `${when(p.t)} (inserted there afterwards)`] : null,
    p.edited ? ["Last changed by", p.editor] : null,
    p.edited ? ["Last changed", iso(p.updated)] : null,
    ["Editable until", p.expires ? `${when(p.expires)}${p.expires * 1000 < Date.now() ? " (over)" : ""}` : "no limit"],
    ["Tags", p.tags.join(", ") || "none"],
    ["Comments", String(p.comments?.length || 0)],
    p.quote ? ["Reply to", p.quote.of && state.byId.get(p.quote.of) ? postLabel(state.byId.get(p.quote.of)) : "a post (not found here)"] : null,
    ["Readable by", ((p.groups || []).join(", ") || "not given by SciLog") + (p.shared ? " (shared with everyone with a PSI account)" : "")],
    ["You may", [p.can_edit ? (editable(p) ? "edit" : "edit (period over)") : "", p.can_delete ? "delete" : ""].filter(Boolean).join(", ") || "only read"],
    ["Id", p.id],
  ].filter(Boolean);
  const dialog = el("dialog", { class: "info-dialog" },
    el("h3", { text: "Post info" }),
    el("table", {}, ...rows.map(([k, v]) => el("tr", {}, el("th", { text: k }), el("td", { text: v })))),
    el("div", { class: "info-foot" },
      el("button", { onclick: () => copyText(p.id, "Id") }, "Copy id"),
      el("button", { class: "primary", onclick: () => dialog.close() }, "Close")));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

// a saved post (or the post a saved comment belongs to) into the page: only that post is drawn
// anew, the rest of a large logbook stays as it is; the outline follows a moment later
function placeSaved(post, message = "Saved to SciLog", openComments = false) {
  state.byId.set(post.id, post);
  state.all = [...state.byId.values()].sort((a, b) => a.t - b.t);
  applyView();
  if (!state.posts.some((p) => p.id === post.id)) {
    setView("all");  // not in the current view: the whole flow, once
  } else {
    const node = document.getElementById(`p-${post.id}`);
    if (node) node.replaceWith(postElement(post)); else placeInFlow(post);
    observePosts();
    laterOutline();
  }
  prepareSearch();
  fillViews();
  setStatus();
  jumpToPost(post.id);
  if (openComments) document.querySelector(`#p-${post.id} .comments`)?.classList.add("open");
  toast(message);
}

function headingMenu(ev, postId, index, level) {
  document.querySelector(".o-menu")?.remove();
  const menu = el("div", { class: "o-menu" });
  for (const [lv, label] of [["h2", "Heading"], ["h3", "Subheading"], ["h4", "Minor heading"], ["p", "Normal text"]]) {
    if (lv === level) continue;
    menu.append(el("div", { onclick: async () => {
      menu.remove();
      try { placeSaved(await setHeading(state.logbook.id, postId, index, lv)); }
      catch (e) { toast(`Not changed: ${e.message}`); }
    } }, label));
  }
  menu.style.left = `${ev.clientX}px`;
  menu.style.top = `${ev.clientY}px`;
  document.body.append(menu);
  setTimeout(() => document.addEventListener("click", () => menu.remove(), { once: true }), 0);
}

function setStatus(extra) {
  const now = new Date();
  const shown = state.posts.length === state.all.length ? `${state.all.length} posts` : `${state.posts.length} of ${state.all.length} posts`;
  $("#status").textContent = extra || `${shown} · ${state.identity || ""}${canWrite() ? " · can write" : ""} · ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  updateTokenBadge();
}

// -- the SciLog token: entered here, renewed here ------------------------------------------------
// SciLog's token lasts 14 days from the login and cannot be renewed: then a new one is needed.

const TOKEN_BOOKMARKLET = "javascript:void(prompt('SciLog token (select all, copy):',localStorage.id_token))";

function updateTokenBadge() {
  const t = state.token, badge = $("#token-badge");
  if (!badge) return;
  const left = t?.expires ? (t.expires * 1000 - Date.now()) / 86400000 : null;
  const soon = left !== null && left < 3;
  let text = "";
  if (t?.access === "temporary") text = `🔑 ${(t.identity || "").split(/[<(]/)[0].trim()}: ${left > 1 / 24 ? `${Math.round(left * 24)} h` : `${Math.max(1, Math.round(left * 1440))} min`} left`;
  else if (t?.access === "beamline" && t.options?.shared) text = "👥 beamline account";
  else if (t?.personal && soon) text = left > 1 ? `🔑 token: ${Math.floor(left)} days left` : left > 0 ? `🔑 token: ${Math.max(1, Math.round(left * 24))} h left` : "🔑 token expired";
  badge.hidden = !text;
  badge.textContent = text;
  badge.title = "Whose SciLog access the viewer uses: click to change";
}

// Whose SciLog access the viewer uses: the beamline account, a personal token kept here (not on a
// shared account), or a personal token lent for at most a few hours (held by the viewer's server only).
function openTokenDialog(status = state.token || {}) {
  document.getElementById("token-dialog")?.remove();
  const opts = status.options || {}, shared = !!opts.shared, hours = opts.temporary_hours || 8;
  const until = status.expires ? new Date(status.expires * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : null;
  const input = el("textarea", { class: "tk-input", rows: "3", placeholder: "Paste the token here (eyJ…)", autocomplete: "off", spellcheck: "false" });
  const note = el("div", { class: "tk-note" });
  const keep = el("input", { type: "checkbox", ...(shared ? { disabled: "" } : { checked: "" }) });
  const save = el("button", { class: "primary" }, "Use this token");
  const bm = el("code", { class: "tk-code", text: TOKEN_BOOKMARKLET });
  const dialog = el("dialog", { id: "token-dialog" });
  const choose = async (kind, button) => {
    button.disabled = true;
    note.textContent = "Switching …";
    try {
      const data = await writeApi("/api/access", "POST", { kind });
      note.textContent = `Reading SciLog as ${data.identity}. Reloading …`;
      setTimeout(() => location.reload(), 500);
    } catch (e) { note.textContent = `Not switched: ${e.message}`; note.classList.add("bad"); button.disabled = false; }
  };
  const current = status.ok ? `Reading SciLog as ${status.identity}${until ? `, until ${until}` : ""}.` : `${status.why || "No SciLog access yet"}.`;
  const cards = [];
  if (opts.beamline?.available) {
    const b = el("button", { class: status.access === "beamline" ? "" : "primary", onclick: () => choose("beamline", b) },
      status.access === "beamline" ? "In use" : "Use the beamline account");
    if (status.access === "beamline") b.disabled = true;
    cards.push(el("div", { class: "tk-card" },
      el("div", {}, el("strong", { text: "The beamline account" }), el("span", { class: "tk-who", text: ` ${opts.beamline.who}` })),
      el("p", { text: "Reads the logbooks of this beamline's groups. What you write (posts, comments, tasks) is saved as the beamline account, not as you." }), b));
  }
  if (opts.stored?.available) {
    const b = el("button", { class: status.access === "stored" ? "" : "primary", onclick: () => choose("stored", b) },
      status.access === "stored" ? "In use" : "Use it");
    if (status.access === "stored") b.disabled = true;
    const exp = opts.stored.expires ? new Date(opts.stored.expires * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
    cards.push(el("div", { class: "tk-card" },
      el("div", {}, el("strong", { text: "Your kept token" }), el("span", { class: "tk-who", text: ` ${opts.stored.who || ""}${exp ? `, valid until ${exp}` : ""}` })),
      el("p", { text: "Reads and writes SciLog as you." }), b));
  }
  dialog.append(
    el("div", { class: "ed-head" }, el("strong", { text: "Read SciLog as …" }), el("span", { class: "spacer" }),
      el("button", { title: "Close", onclick: () => dialog.close() }, "✕")),
    el("div", { class: "tk-body" },
      el("p", { class: status.ok ? "" : "bad", text: current }),
      ...cards,
      el("div", { class: "tk-card" },
        el("div", {}, el("strong", { text: "Your own token" }),
          el("span", { class: "tk-who", text: shared ? ` — lent for at most ${hours} hours` : "" })),
        el("p", { text: shared
          ? `This is a shared account, so your token is never written to disk here: the viewer holds it in memory for at most ${hours} hours, and forgets it when the eco session ends.`
          : "SciLog issues a token when you log in; it lasts 14 days." }),
        // in the Android app: SciLog's own login inside the app, which hands the token over by itself
        window.ScilogAndroid ? el("p", {}, el("button", { class: "primary", onclick: () => { dialog.close(); window.ScilogAndroid.login(); } }, "Log in to SciLog here"),
          el("span", { class: "tk-safe", text: " — the token is taken over by itself. Or by hand:" })) : null,
        el("ol", {},
          el("li", {}, "Log in at ", el("a", { href: "https://scilog.psi.ch", target: "_blank", rel: "noopener" }, "scilog.psi.ch"), " (in this browser or any other)."),
          el("li", {}, "Copy the token, either way:",
            el("ul", {},
              el("li", {}, "on a computer: press F12, open Console, type ", el("code", { text: "copy(localStorage.id_token)" }), " and Enter;"),
              el("li", {}, "on a phone, or without the developer tools: once, make a bookmark with this address, then open it while SciLog is shown (Android: type the bookmark's name in the address bar and pick it). It shows the token to copy:",
                el("div", { class: "tk-row" }, bm, el("button", { onclick: () => copyText(TOKEN_BOOKMARKLET, "Bookmark address") }, "Copy"))))),
          el("li", {}, "Paste it here:")),
        input,
        shared ? null : el("label", { class: "tk-keep" }, keep, ` Keep it on this computer (readable only by you); otherwise for ${hours} hours, in memory only`)),
      note),
    el("div", { class: "ed-foot" }, el("span", { class: "spacer" }), save));
  save.onclick = async () => {
    save.disabled = true;
    note.textContent = "Checking the token with SciLog …";
    try {
      const data = await writeApi("/api/token/set", "POST", { token: input.value, keep: !shared && keep.checked });
      note.textContent = `Accepted: ${data.identity}. Reloading …`;
      setTimeout(() => location.reload(), 600);
    } catch (e) { note.textContent = `Not accepted: ${e.message}`; note.classList.add("bad"); save.disabled = false; }
  };
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  if (!cards.length) input.focus();
}

function copyLink(id) {
  const url = `${location.origin}/#lb=${state.logbook.id}&post=${id}`;
  navigator.clipboard?.writeText(url).then(() => toast("Link copied"), () => prompt("Link to this post:", url));
  history.replaceState(null, "", `#lb=${state.logbook.id}&post=${id}`);
}

function fromHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  return { lb: h.get("lb"), post: h.get("post"), only: h.get("only") === "1" };
}

function toggleAllComments() {
  state.commentsOpen = !state.commentsOpen;
  storageSet("eco-scilog-comments", state.commentsOpen ? "open" : "closed");
  document.querySelectorAll(".comments").forEach((c) => c.classList.toggle("open", state.commentsOpen));
  toast(state.commentsOpen ? "Comments shown" : "Comments hidden");
}
state.commentsOpen = storageGet("eco-scilog-comments") === "open";
if (storageGet("eco-scilog-thumbs") === "on") document.body.classList.add("thumbs");

// -- the left pane's width: drag the line between it and the flow ------------------------------

const PANE_DEFAULT = 300;
function setPaneWidth(px, save = true) {
  const w = Math.round(Math.max(160, Math.min(px, window.innerWidth * 0.6)));
  document.documentElement.style.setProperty("--pane-width", `${w}px`);
  if (save) storageSet("eco-scilog-pane-width", String(w));
}

function setUpResizer() {
  const saved = parseInt(storageGet("eco-scilog-pane-width") || "", 10);
  if (saved > 0) setPaneWidth(saved, false);
  const bar = $("#pane-resizer");
  bar.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    bar.setPointerCapture(e.pointerId);
    const left = $("#outline").getBoundingClientRect().left;
    document.body.classList.add("resizing");
    const move = (ev) => setPaneWidth(ev.clientX - left, false);
    const up = (ev) => {
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", up);
      bar.removeEventListener("pointercancel", up);
      document.body.classList.remove("resizing");
      setPaneWidth(ev.clientX - left);
      if (typeof mapState !== "undefined" && mapState.on) renderMap();
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
    bar.addEventListener("pointercancel", up);
  });
  bar.addEventListener("dblclick", () => { setPaneWidth(PANE_DEFAULT); try { localStorage.removeItem("eco-scilog-pane-width"); } catch (e) { /* */ } });
}

// -- start ---------------------------------------------------------------------------------------

async function main() {
  $("#toggle-outline").onclick = () => {
    if (window.matchMedia("(max-width: 760px)").matches) document.body.classList.toggle("outline-open");
    else { document.body.classList.toggle("no-outline"); storageSet("eco-scilog-outline", document.body.classList.contains("no-outline") ? "off" : "on"); }
  };
  if (storageGet("eco-scilog-outline") === "off") document.body.classList.add("no-outline");
  $("#logbook-button").onclick = () => showPicker($("#picker").hidden);
  $("#new-post").onclick = newPost;
  $("#to-bottom").onclick = () => (state.unseen.size ? toUnseen() : toNewest());
  $("#flow").addEventListener("scroll", updateToBottom, { passive: true });
  $("#views-button").onclick = () => openViewsDialog();
  $("#token-badge").onclick = () => openTokenDialog();
  $("#status").onclick = () => openTokenDialog();
  $("#status").title = "Click: the SciLog token (whose, until when, enter a new one)";  // views.js loads after this file
  $("#search-button").onclick = openSearch;
  $("#search-cancel").onclick = endSearch;
  setUpResizer();
  $("#view").onchange = (e) => {
    if (e.target.value === "__views") { e.target.value = state.view; openViewsDialog(); }
    else setView(e.target.value);
  };
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
    else if (e.key === "Escape") endSearch();
  };
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#search-wrap, #flow, #search-button") || (!docked() && !e.target.closest("#search-wrap, #search-button"))) {
      if ($("#bar").classList.contains("search-open")) endSearch(); else closeResults();
    }
    if (!e.target.closest("#picker, #logbook-button")) $("#picker").hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, [contenteditable]")) return;
    if (e.key === "/") { e.preventDefault(); if (narrow()) openSearch(); input.focus(); input.select(); }
    else if (e.key === "o") $("#toggle-outline").click();
    else if (e.key === "l") showPicker(true);
    else if (e.key === "n") newPost();
    else if (e.key === "End" || e.key === "G") { e.preventDefault(); toNewest(); }
    else if (e.key === "c") toggleAllComments();
    else if (e.key === "v") $("#view").focus();
    else if (e.key === "Escape") { $("#picker").hidden = true; document.getElementById("lightbox")?.remove(); }
  });
  window.addEventListener("hashchange", () => {
    const { lb, post } = fromHash();
    const { only } = fromHash();
    if (lb && (!state.logbook || lb !== state.logbook.id)) openLogbook(lb, post, only);
    else if (post && only) showOnly(post);
    else if (post) { if (state.only) showInContext(post); else jumpToPost(post); }
  });

  let tok;
  try { tok = await api("/api/token"); } catch (e) { tok = { ok: false, why: e.message, can_set: true }; }
  state.token = tok;
  updateTokenBadge();
  if (!tok.ok) {
    $("#flow").replaceChildren(el("div", { class: "hint" }, tok.must_choose ? "Choose whose SciLog access the viewer uses. " : `SciLog cannot be read: ${tok.why}. `,
      el("button", { onclick: () => openTokenDialog(tok) }, tok.must_choose ? "Choose …" : "Enter a SciLog token")));
    openTokenDialog(tok);
    return;
  }
  try {
    const [me, books] = await Promise.all([api("/api/me"), api("/api/logbooks")]);
    state.identity = me.identity.split("<")[0].trim();
    state.me = me;
    state.logbooks = books.logbooks;
  } catch (e) {
    $("#flow").replaceChildren(el("p", { class: "hint", text: `SciLog could not be read: ${e.message}` }));
    return;
  }
  const { lb, post, only } = fromHash();
  const last = lb || storageGet("eco-scilog-last");
  if (last && state.logbooks.some((x) => x.id === last)) openLogbook(last, post, only);
  else { $("#flow").replaceChildren(el("p", { class: "hint", text: "Choose a logbook: the most recently active come first." })); showPicker(true); }
}

// A phone's Back button (the Android app calls this): close what is open on top, the topmost first.
// Returns true if something was closed; false means there is nothing left to close (the app then leaves).
// Overlays open without history entries, so this replaces the browser's own Back.
function pageBack() {
  const box = document.getElementById("lightbox");
  if (box) { box.remove(); return true; }
  if (document.getElementById("post-menu")) { closePostMenu(); return true; }
  if (document.querySelector(".ck-balloon-panel_visible")) {
    document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return true;
  }
  const dialog = [...document.querySelectorAll("dialog[open]")].pop();
  if (dialog) {
    const ev = new Event("cancel", { cancelable: true });
    dialog.dispatchEvent(ev);  // the editor closes itself (keeping the draft)
    if (!ev.defaultPrevented && dialog.open) dialog.close();
    return true;
  }
  if (!$("#picker").hidden && state.logbook) { showPicker(false); return true; }
  if ($("#bar").classList.contains("search-open") || document.body.classList.contains("searching")) { endSearch(); return true; }
  if (state.fromSearch) { state.fromSearch = false; if (reopenResults()) return true; }
  if (document.body.classList.contains("outline-open")) { document.body.classList.remove("outline-open"); return true; }
  if (taskState.open) {
    if (taskState.editing !== null) { document.querySelector(".task-edit")?.blur(); return true; }
    openTasks(false); return true;
  }
  if (!$("#pin-pane").hidden) { unpin(); return true; }
  if (state.only) { showInContext(state.only); return true; }
  return false;
}
window.scilogBack = pageBack;

main();
