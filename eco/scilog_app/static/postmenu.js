// eco SciLog viewer: one "⋯" menu per post instead of a row of buttons -- a small
// menu beside the button, a sheet from the bottom on a phone. Groups fold out in
// place: "Insert a post" (above / below) and "Link & sharing".
"use strict";

function closePostMenu() { document.getElementById("post-menu")?.remove(); document.getElementById("post-menu-shade")?.remove(); }

function copyText(text, what) {
  const done = () => toast(`${what} copied`);
  if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done, () => prompt(`${what}:`, text));
  else prompt(`${what}:`, text);
}

function scilogLogbookLink(p) {
  const lb = state.logbooks.find((l) => l.id === state.logbook.id) || state.logbook;
  const url = lb.url || `https://scilog.psi.ch/logbooks/${lb.id}/dashboard`;
  return `${p.title ? `“${p.title}” — ` : ""}${fullDate(p.t)} ${clock(p.t)}, ${shortName(p.author)}, in ${lb.name}: ${url}`;
}

async function setShared(p, on) {
  try {
    const post = await writeApi(`/api/share/${p.id}`, "POST", { logbook: state.logbook.id, on });
    state.byId.set(post.id, post);
    const i = state.all.findIndex((x) => x.id === post.id);
    if (i >= 0) state.all[i] = post;
    applyView();
    document.getElementById(`p-${post.id}`)?.replaceWith(postElement(post));
    toast(on ? "Readable now by everyone with a PSI account" : "No longer readable by everyone");
  } catch (e) { toast(`Not changed: ${e.message}`); }
}

function openPostMenu(p, button, pinned = false) {
  closePostMenu();
  const sheet = window.matchMedia("(max-width: 760px)").matches;
  const item = (icon, label, fn, opts = {}) => el("button", { class: `pm-item${opts.cls ? ` ${opts.cls}` : ""}`, title: opts.title || "", ...(opts.disabled ? { disabled: "" } : {}),
    onclick: (ev) => { ev.stopPropagation(); if (opts.keep) fn(ev); else { closePostMenu(); fn(ev); } } },
    el("span", { class: "pm-icon", text: icon }), el("span", { class: "pm-label" }, label), opts.hint ? el("span", { class: "pm-hint", text: opts.hint }) : null);
  const group = (icon, label, children, title) => {
    const sub = el("div", { class: "pm-sub", hidden: "" }, ...children);
    const head = item(icon, label, () => { sub.hidden = !sub.hidden; head.classList.toggle("open", !sub.hidden); place(); }, { keep: true, title, cls: "pm-group" });
    return [head, sub];
  };
  const menu = el("div", { id: "post-menu", class: sheet ? "sheet" : "", role: "menu" });
  // beside the button: below it if it fits, else above, else as high as the window allows (scrolling inside)
  function place() {
    if (sheet || !menu.isConnected) return;
    const r = button.getBoundingClientRect(), w = menu.offsetWidth;
    menu.style.maxHeight = `${window.innerHeight - 16}px`;
    const h = menu.offsetHeight;
    menu.style.left = `${Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8))}px`;
    const top = r.bottom + 4 + h <= window.innerHeight - 8 ? r.bottom + 4 : r.top - h - 4 >= 8 ? r.top - h - 4 : window.innerHeight - h - 8;
    menu.style.top = `${Math.max(8, top)}px`;
  }
  menu.append(el("div", { class: "pm-title", text: `${dayTitle(p.t)} ${clock(p.t)} · ${shortName(p.author)}${p.title ? ` · ${p.title}` : ""}` }));
  if (pinned) {
    menu.append(item("⇠", "Show in the main pane", () => showPinnedInMain(p.id)));
  } else {
    const writing = canWrite();
    if (p.can_edit) {
      menu.append(editable(p)
        ? item("✏️", "Edit", () => (writing ? editPost(p.id) : askToAllowWriting()), { hint: p.expires ? `until ${shortDate(p.expires)}` : "" })
        : item("✏️", "Edit", () => {}, { disabled: true, hint: "edit period over", title: `SciLog allows changes only until ${editableUntil(p)}` }));
    }
    menu.append(item("💬", "Add comment", () => commentOn(p.id), { title: "A note under this post, as in SciLog" }),
      item("↩", "Reply", () => replyTo(p.id), { title: "A new post at the end that quotes this one, as in SciLog" }));
    menu.append(...group("T", "Insert a post", [
      item("↑", "above this one", () => insertNear(p.id, "before"), { title: "e.g. a heading for the outline" }),
      item("↓", "below this one", () => insertNear(p.id, "after")),
    ], "Write a new post placed right above or below this one"));
    const shareItems = [
      item("🔗", "Link for this viewer", () => copyLink(p.id), { title: "Opens this post in the SciLog viewer (for people who run it)" }),
      item("📋", "SciLog link and time", () => copyText(scilogLogbookLink(p), "SciLog link"),
        { title: "SciLog has no link to a single post (yet): the logbook's address with the post's time and title" }),
    ];
    if (p.can_edit) {
      shareItems.push(p.shared
        ? item("🚫", "Stop sharing with everyone", () => setShared(p, false), { hint: "🌐 shared" })
        : item("🌐", "Share with everyone with a PSI account", async () => {
          if (!confirm("Make this post, its comments and its attachments readable by everyone logged in to SciLog (any PSI account)?\n\nThis can be undone here.")) return;
          await setShared(p, true);
        }, { title: "Also after the edit period; you can undo it" }));
    }
    shareItems.push(el("div", { class: "pm-note", text: "Shared posts are readable through SciLog's API. SciLog's page can show a single post only once its link-to-an-entry page exists (prepared pull request)." }));
    menu.append(...group("🔗", "Link & sharing", shareItems));
    menu.append(item("⧉", "Show only this post", () => showOnly(p.id)));
    if (!window.matchMedia("(max-width: 760px)").matches) menu.append(item("📌", "Pin beside the flow", () => pinPost(p.id)));
    menu.append(item("ℹ️", "Info", () => showInfo(p), { title: "Who wrote and changed it, when, the edit period, who can read it" }));
    if (p.comments?.length) menu.append(item("🗨", "Show / hide comments", () => document.querySelector(`#p-${p.id} .comments`)?.classList.toggle("open")));
    if (p.can_delete) menu.append(el("div", { class: "pm-sep" }), item("🗑", "Delete …", () => deletePost(p), { cls: "pm-danger", title: "SciLog moves it to the logbook's history, where it can be restored" }));
  }
  if (sheet) {
    const shade = el("div", { id: "post-menu-shade", onclick: closePostMenu });
    document.body.append(shade);
  }
  document.body.append(menu);
  place();
  menu.querySelector(".pm-item:not([disabled])")?.focus();
}

document.addEventListener("pointerdown", (e) => { if (!e.target.closest("#post-menu, .actions .more")) closePostMenu(); }, true);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePostMenu(); });
window.addEventListener("resize", closePostMenu);
document.getElementById("flow")?.addEventListener("scroll", () => { if (!document.querySelector("#post-menu.sheet")) closePostMenu(); }, { passive: true });

async function deletePost(p) {
  if (!canWrite()) { askToAllowWriting(); return; }
  const what = `${dayTitle(p.t)} ${clock(p.t)} ${shortName(p.author)}${p.title ? `: “${p.title.slice(0, 60)}”` : ""}`;
  if (!confirm(`Delete this post from SciLog?\n\n${what}\n\nIt disappears for everyone; SciLog keeps it in the logbook's history, from where it can be restored.`)) return;
  try {
    await writeApi(`/api/delete/${p.id}`, "POST", { logbook: state.logbook.id });
  } catch (e) { toast(`Not deleted: ${e.message}`); return; }
  state.byId.delete(p.id);
  state.all = state.all.filter((x) => x.id !== p.id);
  applyView();
  prepareSearch();
  // in place, so the reading position stays: the post, and its day header if it was the day's only post
  const node = document.getElementById(`p-${p.id}`);
  const prev = node?.previousElementSibling, next = node?.nextElementSibling;
  node?.remove();
  if (prev?.classList.contains("day") && (!next || !next.classList.contains("post"))) prev.remove();
  renderOutline();
  fillViews();
  setStatus();
  toast("Deleted");
}
