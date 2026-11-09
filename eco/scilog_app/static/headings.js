// eco SciLog viewer: the headings of all posts as one table of contents, nested by
// level across posts (a heading in a later post continues the structure of the
// earlier ones). Optionally with the days in between and the posts that have no
// heading. Click jumps there; the entry being read is marked.
"use strict";

const hdState = {
  on: false,
  dates: storageGet("eco-scilog-hd-dates") !== "off",
  loose: storageGet("eco-scilog-hd-loose") === "on",
  collapsed: new Set(),
  observer: null,
};

function setHeadingsPane(on) { hdState.on = on; renderHeadings(); }

const HEAD_RX = /<(h[1-6])\b[^>]*>(.*?)<\/h[1-6]>/gis;
function headingsOf(p) {
  return [...p.html.matchAll(HEAD_RX)].map((h, n) => ({ level: Number(h[1][1]), text: h[2].replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").trim(), index: n }))
    .filter((h) => h.text);
}

function headingItems() {
  const items = [], stack = [];
  for (const p of state.posts) {
    const hs = headingsOf(p);
    const list = hs.length ? hs.map((h) => ({ ...h, post: p, key: `${p.id}:${h.index}` }))
      : hdState.loose ? [{ level: 7, text: p.title || "(no title)", post: p, key: p.id, loose: true }] : [];
    for (const it of list) {
      while (stack.length && stack[stack.length - 1].level >= it.level) stack.pop();
      it.depth = stack.length;
      it.parent = stack[stack.length - 1] || null;
      if (it.parent) it.parent.hasChildren = true;
      if (!it.loose) stack.push(it);
      items.push(it);
    }
  }
  return items;
}

function renderHeadings() {
  const box = $("#headings");
  if (hdState.observer) { hdState.observer.disconnect(); hdState.observer = null; }
  if (!hdState.on) { box.replaceChildren(); return; }
  const toggle = (key, label, title) => el("button", { class: `hd-opt${hdState[key] ? " on" : ""}`, title,
    onclick: () => { hdState[key] = !hdState[key]; storageSet(`eco-scilog-hd-${key}`, hdState[key] ? "on" : "off"); renderHeadings(); } }, label);
  const frag = document.createDocumentFragment();
  frag.append(el("div", { class: "hd-opts" },
    toggle("dates", "📅 days", "Show the days and times between the headings"),
    toggle("loose", "· posts without heading", "Also list the posts that have no heading, under the heading before them"),
    el("button", { class: "hd-opt", title: "Fold everything to the top level / unfold all", onclick: () => {
      const items = headingItems();
      if (hdState.collapsed.size) hdState.collapsed.clear();
      else items.filter((i) => i.hasChildren && i.depth === 0).forEach((i) => hdState.collapsed.add(i.key));
      renderHeadings();
    } }, "⇕")));
  const items = headingItems();
  if (!items.length) frag.append(el("p", { class: "hint", text: "No post here has a heading. Headings (H1–H3 in the editor) make this table of contents; ⤓ on a post inserts one after it." }));
  let day = null;
  const hidden = (it) => { for (let a = it.parent; a; a = a.parent) if (hdState.collapsed.has(a.key)) return true; return false; };
  for (const it of items) {
    if (hidden(it)) continue;
    const p = it.post;
    if (hdState.dates && dayKey(p.t) !== day) {
      day = dayKey(p.t);
      frag.append(el("div", { class: "hd-date", onclick: () => jumpTo(document.querySelector(`#flow .day[data-day="${day}"]`)) }, dayTitle(p.t)));
    }
    const fold = it.hasChildren ? el("span", { class: "hd-fold", text: hdState.collapsed.has(it.key) ? "▸" : "▾",
      onclick: (ev) => { ev.stopPropagation(); hdState.collapsed.has(it.key) ? hdState.collapsed.delete(it.key) : hdState.collapsed.add(it.key); renderHeadings(); } })
      : el("span", { class: "hd-fold" });
    const row = el("div", { class: `hd-item hd-l${Math.min(it.level, 7)}${it.loose ? " hd-loose" : ""}`, "data-post": p.id, title: `${it.text}\n${fullDate(p.t)} ${clock(p.t)} · ${shortName(p.author)}`,
      onclick: () => it.loose ? jumpToPost(p.id) : (revealInOutline(p.id), jumpTo(document.getElementById(`h-${p.id}-${it.index}`), true)) },
      fold, el("span", { class: "hd-text", text: it.text }), ...(it.index === 0 || it.loose ? postMarks(p) : []),
      hdState.dates ? el("span", { class: "hd-time", text: clock(p.t) }) : null);
    row.style.paddingLeft = `${4 + it.depth * 14}px`;
    frag.append(row);
  }
  box.replaceChildren(frag);
  // mark what is being read
  hdState.observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const id = e.target.dataset.id;
      let rows = box.querySelectorAll(`.hd-item[data-post="${id}"]`);
      if (!rows.length) {  // a post without heading: the heading before it
        const i = state.posts.findIndex((p) => p.id === id);
        for (let k = i - 1; k >= 0 && !rows.length; k--) rows = box.querySelectorAll(`.hd-item[data-post="${state.posts[k].id}"]`);
        rows = rows.length ? [rows[rows.length - 1]] : [];
      }
      box.querySelectorAll(".hd-item.current").forEach((r) => r.classList.remove("current"));
      rows.forEach((r) => r.classList.add("current"));
      if (rows[0] && !box.matches(":hover")) rows[0].scrollIntoView({ block: "nearest" });
    }
  }, { root: $("#flow"), rootMargin: "-10% 0px -80% 0px" });
  document.querySelectorAll("#flow .post").forEach((p) => hdState.observer.observe(p));
}

if (storageGet("eco-scilog-pane") === "headings") setPane("headings");
