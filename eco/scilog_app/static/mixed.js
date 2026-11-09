// eco SciLog viewer: "Mixed" -- headings and dated posts interleaved. Headings make
// the structure (nested by level, across posts); the posts without a heading
// appear with their time under the heading before them; runs of similar posts
// (DAQ run 1150, 1151, ...) fold into one row; days stay at the top while
// scrolling. Click jumps there; the entry being read is marked.
"use strict";

const mxState = { on: false, open: new Set(), observer: null };

function setMixedPane(on) { mxState.on = on; renderMixed(); }

// what makes posts "similar": their title with numbers and anything after a colon dropped
function postKind(p) {
  const t = (p.title || "").replace(/:.*$/, "").replace(/\d+/g, "#").replace(/\s+/g, " ").trim().toLowerCase();
  return t && /#/.test(t) ? t : null;
}

function mixedItems() {
  const out = [], stack = [];
  for (const p of state.posts) {
    const hs = headingsOf(p);
    if (hs.length) {
      hs.forEach((h, n) => {
        while (stack.length && stack[stack.length - 1] >= h.level) stack.pop();
        out.push({ kind: "head", post: p, level: h.level, text: h.text, index: h.index, depth: stack.length, first: n === 0,
                   group: hs.length === 1 ? postKind({ title: h.text }) : null });
        stack.push(h.level);
      });
      // the rest of a post with headings is under its last heading: no own row
    } else {
      out.push({ kind: "post", post: p, depth: stack.length, group: postKind(p) });
    }
  }
  // fold runs of 3 or more similar posts in a row (same day, same depth)
  const folded = [];
  for (let i = 0; i < out.length; ) {
    const it = out[i];
    let j = i + 1;
    if (it.group) {
      while (j < out.length && out[j].group === it.group && out[j].depth === it.depth
             && dayKey(out[j].post.t) === dayKey(it.post.t)) j++;
    }
    if (j - i >= 3) { folded.push({ kind: "run", items: out.slice(i, j), depth: it.depth, key: `${it.post.id}:${j - i}` }); i = j; }
    else { folded.push(it); i += 1; }
  }
  return folded;
}

function mixedRow(it) {
  const p = it.post;
  if (it.kind === "head") {
    const row = el("div", { class: `mx-item mx-head hd-l${it.level}`, "data-post": p.id, title: `${it.text}\n${fullDate(p.t)} ${clock(p.t)} · ${shortName(p.author)}`,
      onclick: () => { revealInOutline(p.id); jumpTo(document.getElementById(`h-${p.id}-${it.index}`), true); } },
      el("span", { class: "mx-time", text: it.first ? clock(p.t) : "" }), el("span", { class: "mx-text", text: it.text }), ...(it.first ? postMarks(p) : []));
    row.style.paddingLeft = `${4 + it.depth * 12}px`;
    return row;
  }
  const row = el("div", { class: "mx-item mx-post", "data-post": p.id, title: `${p.title}\n${fullDate(p.t)} ${clock(p.t)} · ${shortName(p.author)}`, onclick: () => jumpToPost(p.id) },
    el("span", { class: "mx-time", text: clock(p.t) }),
    el("span", { class: "mx-text", text: p.title || (p.html.includes("<img") ? "🖼 image" : p.html.includes("plot") ? "📈 plot" : "(empty)") }),
    el("span", { class: "mx-who", text: shortName(p.author) }), ...postMarks(p));
  row.style.paddingLeft = `${4 + it.depth * 12}px`;
  return row;
}

function renderMixed() {
  const box = $("#mixed");
  if (mxState.observer) { mxState.observer.disconnect(); mxState.observer = null; }
  if (!mxState.on) { box.replaceChildren(); return; }
  const frag = document.createDocumentFragment();
  let day = null;
  const dayRow = (p) => {
    if (dayKey(p.t) === day) return;
    day = dayKey(p.t);
    const k = day;
    frag.append(el("div", { class: "mx-day", onclick: () => jumpTo(document.querySelector(`#flow .day[data-day="${k}"]`)) }, dayTitle(p.t)));
  };
  for (const it of mixedItems()) {
    if (it.kind === "run") {
      const first = it.items[0].post, last = it.items[it.items.length - 1].post;
      dayRow(first);
      const open = mxState.open.has(it.key);
      const name = (x) => (x.kind === "head" ? x.text : x.post.title) || "";
      const nums = (x) => (name(x).match(/\d+/) || [""])[0];
      const label = `${name(it.items[0]).replace(/:.*$/, "")} … ${nums(it.items[it.items.length - 1]) || clock(last.t)}`;
      const row = el("div", { class: "mx-item mx-run", title: `${it.items.length} similar posts, ${clock(first.t)}–${clock(last.t)}: click to ${open ? "fold" : "unfold"}`,
        onclick: () => { open ? mxState.open.delete(it.key) : mxState.open.add(it.key); renderMixed(); } },
        el("span", { class: "mx-time", text: clock(first.t) }), el("span", { class: "mx-fold", text: open ? "▾" : "▸" }),
        el("span", { class: "mx-text", text: label }), el("span", { class: "mx-count", text: `${it.items.length}` }));
      row.dataset.posts = it.items.map((x) => x.post.id).join(" ");
      row.style.paddingLeft = `${4 + it.depth * 12}px`;
      frag.append(row);
      if (open) for (const x of it.items) { dayRow(x.post); const r = mixedRow(x); r.style.paddingLeft = `${16 + it.depth * 12}px`; frag.append(r); }
      continue;
    }
    dayRow(it.post);
    frag.append(mixedRow(it));
  }
  if (!state.posts.length) frag.append(el("p", { class: "hint", text: "No posts." }));
  box.replaceChildren(frag);
  mxState.observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const id = e.target.dataset.id;
      let rows = [...box.querySelectorAll(`.mx-item[data-post="${id}"]`)];
      if (!rows.length) rows = [...box.querySelectorAll(".mx-run")].filter((r) => r.dataset.posts.split(" ").includes(id));
      box.querySelectorAll(".mx-item.current").forEach((r) => r.classList.remove("current"));
      rows.forEach((r) => r.classList.add("current"));
      if (rows[0] && !box.matches(":hover")) rows[0].scrollIntoView({ block: "nearest" });
    }
  }, { root: $("#flow"), rootMargin: "-10% 0px -80% 0px" });
  document.querySelectorAll("#flow .post").forEach((p) => mxState.observer.observe(p));
}

if (storageGet("eco-scilog-pane") === "mixed") setPane("mixed");
