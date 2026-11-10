// eco SciLog viewer: the logbook's tasks, in a pane of their own (☑ in the bar, ✕ or Back closes it).
// Unlike SciLog's own page, a task's text can be changed at any time and the list put in order by
// dragging; the order is stored in SciLog (the tasks' defaultOrder, as for posts).
"use strict";

const taskState = { open: false, tasks: [], logbook: null, hideDone: false, timer: null, busy: 0, editing: null };

function taskApi(path, body) {
  return writeApi(path, "POST", { logbook: state.logbook.id, ...body });
}

async function loadTasks(quiet = false) {
  if (!state.logbook) return;
  const lb = state.logbook.id;
  try {
    const data = await api(`/api/tasks?logbook=${lb}`);
    if (state.logbook?.id !== lb) return;
    showTasks(data.tasks, lb);
  } catch (e) {
    if (!quiet) toast(`Tasks not read: ${e.message}`);
  }
}

function showTasks(tasks, lb = state.logbook?.id) {
  taskState.tasks = tasks;
  taskState.logbook = lb;
  const open = tasks.filter((t) => !t.done).length;
  const badge = $("#tasks-count");
  badge.textContent = open ? String(open) : "";
  badge.hidden = !open;
  $("#tasks-button").hidden = !state.logbook;
  if (taskState.open && taskState.editing === null && !taskState.dragging) renderTasks();
}

function openTasks(open = !taskState.open) {
  taskState.open = open;
  $("#task-pane").hidden = !open;
  document.body.classList.toggle("has-tasks", open);
  $("#tasks-button").classList.toggle("on", open);
  storageSet("eco-scilog-tasks", open ? "open" : "closed");
  clearInterval(taskState.timer);
  if (open) {
    renderTasks();
    loadTasks();
    // others' changes: SciLog announces none, so the list is read again now and then while it is open
    taskState.timer = setInterval(() => { if (!document.hidden) loadTasks(true); }, 30000);
    if (!narrow()) $("#task-new")?.focus();
  }
}

function renderTasks() {
  const pane = $("#task-pane");
  const writing = canWrite();
  const list = el("ol", { id: "task-list" });
  const shown = taskState.tasks.filter((t) => !(taskState.hideDone && t.done));
  for (const t of shown) list.append(taskElement(t, writing));
  const open = taskState.tasks.filter((t) => !t.done).length, done = taskState.tasks.length - open;
  const head = el("div", { id: "task-head" },
    el("strong", { text: "Tasks" }),
    el("span", { class: "task-sum", text: `${open} open${done ? ` · ${done} done` : ""}` }),
    el("span", { class: "spacer" }),
    done ? el("button", { class: "task-hide", title: "Show or hide the tasks that are done",
      onclick: () => { taskState.hideDone = !taskState.hideDone; storageSet("eco-scilog-tasks-hide-done", taskState.hideDone ? "1" : ""); renderTasks(); } },
    taskState.hideDone ? "Show done" : "Hide done") : null,
    el("button", { class: "task-close", title: "Close the tasks (Back on a phone)", "aria-label": "Close", onclick: () => openTasks(false) }, "✕"));
  const input = el("input", { id: "task-new", placeholder: writing ? "Add a task … (Enter)" : "Writing is off for this logbook", autocomplete: "off",
    enterkeyhint: "done", ...(writing ? {} : { disabled: "" }) });
  input.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || !input.value.trim()) return;
    e.preventDefault();
    const text = input.value;
    input.value = "";
    await taskWrite(() => taskApi("/api/task", { text, first: e.shiftKey }), "Task added");
    input.focus();
  });
  const add = el("div", { id: "task-add" }, input,
    writing ? el("span", { class: "task-tip", text: "Shift+Enter: at the top · drag ⠿ to reorder · click a task to edit it" }) : null);
  const empty = taskState.tasks.length ? null : el("p", { class: "hint", text: "No tasks in this logbook yet." });
  const note = el("p", { class: "task-note", text: "The order is kept in SciLog; SciLog's own page still lists tasks by when they were added." });
  pane.replaceChildren(head, add, el("div", { id: "task-scroll" }, list, empty, note));
  if (taskState.busy) pane.classList.add("busy");
}

function taskElement(t, writing) {
  const li = el("li", { class: `task${t.done ? " done" : ""}`, "data-id": t.id });
  const handle = writing && t.can_edit
    ? el("span", { class: "task-grip", title: "Drag to put it elsewhere (Alt+↑/↓ with the keyboard)", "aria-hidden": "true", text: "⠿" })
    : el("span", { class: "task-grip off", "aria-hidden": "true", text: "" });
  const box = el("input", { type: "checkbox", title: t.done ? "Done: click to open it again" : "Mark as done",
    ...(t.done ? { checked: "" } : {}), ...(writing && t.can_edit ? {} : { disabled: "" }) });
  box.addEventListener("change", () => taskWrite(() => taskApi(`/api/task/${t.id}`, { done: box.checked }), box.checked ? "Done" : "Open again"));
  const text = el("span", { class: "task-text", text: t.text, title: `${shortName(t.author)}, ${t.created ? shortDate(Date.parse(t.created) / 1000) : ""}` +
    (t.editor && t.updated !== t.created ? ` · changed by ${shortName(t.editor)}` : "") });
  if (writing && t.can_edit) {
    text.tabIndex = 0;
    text.addEventListener("click", () => editTask(li, t));
    text.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); editTask(li, t); }
      else if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); stepTask(t.id, e.key === "ArrowUp" ? -1 : 1); }
    });
  }
  const who = el("span", { class: "task-who", text: shortName(t.author) });
  const del = writing && t.can_delete
    ? el("button", { class: "task-del", title: "Delete this task", "aria-label": "Delete", onclick: () => deleteTask(t) }, "🗑") : null;
  li.append(...[handle, box, text, who, del].filter(Boolean));
  if (writing && t.can_edit) handle.addEventListener("pointerdown", (e) => dragTask(e, li, t));
  return li;
}

function editTask(li, t) {
  if (li.querySelector("textarea")) return;
  taskState.editing = t.id;
  const area = el("textarea", { class: "task-edit", rows: "2" });
  area.value = t.text;
  const text = li.querySelector(".task-text");
  text.replaceWith(area);
  area.focus();
  area.setSelectionRange(area.value.length, area.value.length);
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    taskState.editing = null;
    const value = area.value.trim();
    if (save && value && value !== t.text) await taskWrite(() => taskApi(`/api/task/${t.id}`, { text: value }), "Task changed");
    else renderTasks();
  };
  area.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
  });
  area.addEventListener("blur", () => finish(true));
}

async function deleteTask(t) {
  if (!confirm(`Delete this task from SciLog?\n\n“${t.text.slice(0, 120)}”\n\nSciLog keeps it in the logbook's history.`)) return;
  await taskWrite(() => taskApi(`/api/task/${t.id}/delete`, {}), "Task deleted");
}

// one write at a time; the list SciLog sends back replaces the shown one
async function taskWrite(fn, message) {
  if (!canWrite()) { askToAllowWriting(); return; }
  taskState.busy++;
  $("#task-pane").classList.add("busy");
  try {
    const data = await fn();
    showTasks(data.tasks);
    if (message) toast(message);
  } catch (e) {
    toast(`Not saved: ${e.message}`);
    loadTasks(true);
  } finally {
    taskState.busy--;
    if (!taskState.busy) $("#task-pane").classList.remove("busy");
  }
}

function moveTask(id, neighbour, where) {
  return taskWrite(() => taskApi(`/api/task/${id}/move`, { [where]: neighbour }), "Order saved");
}

// Alt+↑/↓: one place up or down among the shown tasks
function stepTask(id, by) {
  const shown = taskState.tasks.filter((t) => !(taskState.hideDone && t.done));
  const i = shown.findIndex((t) => t.id === id), j = i + by;
  if (i < 0 || j < 0 || j >= shown.length) return;
  moveTask(id, shown[j].id, by < 0 ? "before" : "after").then(() => document.querySelector(`#task-list [data-id="${id}"] .task-text`)?.focus());
}

// Dragging by the handle, with a mouse or a finger: the task follows the pointer, a gap shows where
// it will land, and on release it is stored there (before the task below the gap, else after the last).
function dragTask(e, li, t) {
  if (e.button !== undefined && e.button !== 0) return;
  e.preventDefault();
  const list = $("#task-list"), scroll = $("#task-scroll");
  const rect = li.getBoundingClientRect();
  const offset = e.clientY - rect.top;
  const gap = el("li", { class: "task-gap" });
  gap.style.height = `${rect.height}px`;
  li.classList.add("dragging");
  li.style.width = `${rect.width}px`;
  li.style.top = `${rect.top}px`;
  li.style.left = `${rect.left}px`;
  li.after(gap);
  document.body.append(li);
  taskState.dragging = true;
  const handle = e.target;
  try { handle.setPointerCapture(e.pointerId); } catch (err) { /* */ }
  let autoscroll = null;
  const place = (y) => {
    li.style.top = `${y - offset}px`;
    const others = [...list.querySelectorAll(".task:not(.dragging)")];
    const below = others.find((o) => { const r = o.getBoundingClientRect(); return y < r.top + r.height / 2; });
    if (below) below.before(gap); else list.append(gap);
    const box = scroll.getBoundingClientRect();
    clearInterval(autoscroll);
    if (y < box.top + 30) autoscroll = setInterval(() => { scroll.scrollTop -= 12; }, 30);
    else if (y > box.bottom - 30) autoscroll = setInterval(() => { scroll.scrollTop += 12; }, 30);
  };
  const move = (ev) => { ev.preventDefault(); place(ev.clientY); };
  const end = (ev) => {
    handle.removeEventListener("pointermove", move);
    handle.removeEventListener("pointerup", end);
    handle.removeEventListener("pointercancel", end);
    clearInterval(autoscroll);
    taskState.dragging = false;
    const next = gap.nextElementSibling?.classList.contains("task") ? gap.nextElementSibling : null;
    const prev = gap.previousElementSibling?.classList.contains("task") ? gap.previousElementSibling : null;
    li.classList.remove("dragging");
    li.removeAttribute("style");
    gap.replaceWith(li);
    if (ev.type === "pointercancel") { renderTasks(); return; }
    const oldNext = taskState.tasks.filter((x) => !(taskState.hideDone && x.done));
    const k = oldNext.findIndex((x) => x.id === t.id);
    const wasNext = oldNext[k + 1]?.id, wasPrev = oldNext[k - 1]?.id;
    if ((next && next.dataset.id === wasNext) || (!next && prev && prev.dataset.id === wasPrev) || (!next && !prev)) return;  // where it was
    if (next) moveTask(t.id, next.dataset.id, "before");
    else moveTask(t.id, prev.dataset.id, "after");
  };
  handle.addEventListener("pointermove", move);
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
}

document.getElementById("tasks-button")?.addEventListener("click", () => openTasks());
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, [contenteditable]")) return;
  if (e.key === "t" && state.logbook) openTasks();
});
try { taskState.hideDone = localStorage.getItem("eco-scilog-tasks-hide-done") === "1"; } catch (e) { /* private */ }
