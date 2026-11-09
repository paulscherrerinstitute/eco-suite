// eco SciLog viewer: a pinned post -- kept open in a second pane on the right, scrollable
// through its logbook, while the main pane moves on (another place, another logbook, a
// search). Search results dock between the two. The pin is remembered in the browser.
"use strict";

const pinState = { logbook: null, posts: [], postId: null };

function pinWidthDefault() { return Math.round(window.innerWidth * 0.36); }
function setPinWidth(px, save = true) {
  const w = Math.round(Math.max(260, Math.min(px, window.innerWidth * 0.6)));
  document.documentElement.style.setProperty("--pin-width", `${w}px`);
  if (save) storageSet("eco-scilog-pin-width", String(w));
}

// pin a post of the logbook shown in the main pane
function pinPost(postId) {
  showPin(state.logbook, state.all, postId);
  storageSet("eco-scilog-pin", JSON.stringify({ logbook: state.logbook.id, post: postId }));
}

function showPin(logbook, posts, postId) {
  pinState.logbook = logbook;
  pinState.posts = posts;
  pinState.postId = postId;
  const p = posts.find((x) => x.id === postId);
  const head = $("#pin-head");
  head.replaceChildren(
    el("span", { class: "pin-title", title: `${logbook.name} — pinned: ${p ? `${fullDate(p.t)} ${clock(p.t)} ${p.author}` : postId}` },
      "📌 ", el("strong", { text: logbook.name }), p ? el("span", { class: "pin-when", text: ` · ${shortDate(p.t)} ${clock(p.t)} ${shortName(p.author)}` }) : null),
    el("span", { class: "spacer" }),
    el("button", { title: "Back to the pinned post", onclick: () => toPinned() }, "⤒ pinned"),
    el("button", { title: "Close the pinned pane", "aria-label": "Close", onclick: unpin }, "✕"));
  const frag = document.createDocumentFragment();
  let day = null;
  for (const q of posts) {
    const k = dayKey(q.t);
    if (k !== day) { frag.append(dayElement(q.t)); day = k; }
    const node = postElement(q, true);
    if (q.id === postId) node.classList.add("pinned");
    frag.append(node);
  }
  $("#pin-flow").replaceChildren(frag);
  $("#pin-pane").hidden = false;
  document.body.classList.add("has-pin");
  toPinned();
}

function toPinned() {
  const node = document.getElementById(`pin-p-${pinState.postId}`);
  if (!node) return;
  node.scrollIntoView({ block: "start" });
  // posts above get their real height only once drawn: settle on the pinned post again,
  // unless the pane has been scrolled by hand meanwhile
  const pane = $("#pin-flow");
  let expected = pane.scrollTop;
  for (const ms of [120, 400, 900]) {
    setTimeout(() => {
      if (Math.abs(pane.scrollTop - expected) > 2) return;
      node.scrollIntoView({ block: "start" });
      expected = pane.scrollTop;
    }, ms);
  }
  node.classList.remove("flash");
  void node.offsetWidth;
  node.classList.add("flash");
}

function unpin() {
  $("#pin-pane").hidden = true;
  $("#pin-flow").replaceChildren();
  document.body.classList.remove("has-pin");
  pinState.posts = [];
  try { localStorage.removeItem("eco-scilog-pin"); } catch (e) { /* */ }
}

// a post of the pinned pane in the main pane (switching logbook if needed)
function showPinnedInMain(postId) {
  if (state.logbook?.id === pinState.logbook.id) {
    if (!state.posts.some((p) => p.id === postId)) { state.view = "all"; $("#view").value = "all"; applyView(); renderFlow(); renderOutline(); }
    jumpToPost(postId);
  } else openLogbook(pinState.logbook.id, postId);
}

async function restorePin() {
  let saved;
  try { saved = JSON.parse(storageGet("eco-scilog-pin") || "null"); } catch (e) { saved = null; }
  if (!saved?.logbook || !saved?.post || narrow()) return;
  const lb = state.logbooks.find((x) => x.id === saved.logbook);
  if (!lb) return;
  try {
    const posts = state.logbook?.id === lb.id && state.all.length ? state.all : (await api(`/api/posts?logbook=${lb.id}`)).posts;
    if (posts.some((p) => p.id === saved.post)) showPin(lb, posts, saved.post);
  } catch (e) { /* the pin just does not come back */ }
}

(function setUpPin() {
  const saved = parseInt(storageGet("eco-scilog-pin-width") || "", 10);
  if (saved > 0) setPinWidth(saved, false);
  const bar = $("#pin-resizer");
  bar.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    bar.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    const move = (ev) => setPinWidth(window.innerWidth - ev.clientX, false);
    const up = (ev) => {
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", up);
      bar.removeEventListener("pointercancel", up);
      document.body.classList.remove("resizing");
      setPinWidth(window.innerWidth - ev.clientX);
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
    bar.addEventListener("pointercancel", up);
  });
  bar.addEventListener("dblclick", () => { setPinWidth(pinWidthDefault(), false); try { localStorage.removeItem("eco-scilog-pin-width"); } catch (e) { /* */ } });
  // after the logbooks are known (main() fills state.logbooks)
  const wait = setInterval(() => { if (state.logbooks.length) { clearInterval(wait); restorePin(); } }, 300);
  setTimeout(() => clearInterval(wait), 60000);
})();
