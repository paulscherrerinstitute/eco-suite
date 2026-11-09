// eco SciLog viewer: SciLog's own views -- the dashboard layouts SciLog's
// page offers for a logbook. SciLog's page can only add views, so old copies
// pile up under the same name; here they are listed side by side, told apart
// (what differs between views of the same name), and can be renamed,
// changed (panel titles, tags, the other logbooks a panel shows) or deleted.
// The server keeps a copy of every view before it changes or deletes it.
"use strict";

async function openViewsDialog() {
  document.getElementById("views-dialog")?.remove();
  const dialog = el("dialog", { id: "views-dialog" });
  const list = el("div", { class: "vw-list" }, el("p", { class: "hint", text: "Loading SciLog's views ..." }));
  const note = el("div", { class: "ed-foot vw-note" });
  dialog.append(
    el("div", { class: "ed-head" }, el("strong", { text: `SciLog views of ${state.logbook.name}` }), el("span", { class: "spacer" }),
      el("button", { title: "Close", onclick: () => dialog.close() }, "✕")),
    list, note);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  await loadViews(list, note);
}

async function loadViews(list, note) {
  let data;
  try { data = await api(`/api/views?logbook=${state.logbook.id}`); }
  catch (e) { list.replaceChildren(el("p", { class: "hint bad", text: `SciLog's views could not be read: ${e.message}` })); return; }
  note.replaceChildren(data.can_edit
    ? el("span", { text: "Changes apply in SciLog for everyone who uses this logbook. A copy of each view is kept on this computer before it is changed or deleted." })
    : el("span", {}, "Read only: writing to this logbook is off. ", el("button", { onclick: () => askToAllowWriting().then(() => loadViews(list, note)) }, "✎ Allow writing here")));
  if (!data.views.length) { list.replaceChildren(el("p", { class: "hint", text: "SciLog has no views for this logbook." })); return; }
  const byName = new Map();
  for (const v of data.views) byName.set(v.name, [...(byName.get(v.name) || []), v]);
  const names = new Map(data.logbooks.map((l) => [l.id, l.name]));
  const frag = document.createDocumentFragment();
  frag.append(el("p", { class: "vw-intro", text: `${data.views.length} views` +
    ([...byName.values()].some((g) => g.length > 1) ? `; ${[...byName.values()].filter((g) => g.length > 1).length} names are used more than once` : "") +
    ". Oldest first." }));
  for (const v of data.views) frag.append(viewCard(v, byName.get(v.name).filter((o) => o.id !== v.id), data, names, () => loadViews(list, note)));
  list.replaceChildren(frag);
}

function made(v) { return v.created ? `${fullDate(v.created)} ${clock(v.created)}` : "?"; }
function logbookName(names, id) { return names.get(id) || `${id.slice(0, 6)}… (not readable for you)`; }

// what tells two views of the same name apart, in words
function viewDifferences(v, other, names) {
  const out = [];
  const n = Math.max(v.panels.length, other.panels.length);
  if (v.panels.length !== other.panels.length) out.push(`${v.panels.length} panels, the other ${other.panels.length}`);
  for (let i = 0; i < n; i++) {
    const a = v.panels[i], b = other.panels[i];
    if (!a || !b) continue;
    const label = a.title || a.type || `panel ${i + 1}`;
    if (a.type !== b.type) out.push(`${label}: a ${a.type} panel, the other a ${b.type} panel`);
    if (a.title !== b.title) out.push(`${label}: titled "${a.title}", the other "${b.title}"`);
    const mine = new Set(a.logbooks.map((l) => l.id)), theirs = new Set(b.logbooks.map((l) => l.id));
    const more = [...mine].filter((x) => !theirs.has(x)), fewer = [...theirs].filter((x) => !mine.has(x));
    if (more.length) out.push(`${label}: shows ${more.length} logbook${more.length > 1 ? "s" : ""} the other does not (${more.map((x) => logbookName(names, x)).join(", ")})`);
    if (fewer.length) out.push(`${label}: lacks ${fewer.length} logbook${fewer.length > 1 ? "s" : ""} the other shows (${fewer.map((x) => logbookName(names, x)).join(", ")})`);
    for (const key of ["tags", "excludeTags"]) {
      if (a[key].join(",") !== b[key].join(",")) out.push(`${label}: ${key === "tags" ? "tags" : "hidden tags"} ${a[key].join(", ") || "none"}, the other ${b[key].join(", ") || "none"}`);
    }
    if (String(a.size) !== String(b.size) || String(a.at) !== String(b.at)) out.push(`${label}: placed or sized differently`);
  }
  return out;
}

function viewCard(v, sameName, data, names, reload) {
  const editable = data.can_edit;
  const changes = { panels: new Map() };
  const save = el("button", { class: "primary", disabled: "" }, "Save changes");
  const dirty = () => save.removeAttribute("disabled");
  const panelChange = (i) => { if (!changes.panels.has(i)) changes.panels.set(i, { index: i }); return changes.panels.get(i); };

  const nameInput = el("input", { class: "vw-name", value: v.name, title: "The name SciLog's page shows" });
  if (!editable) nameInput.setAttribute("readonly", "");
  nameInput.oninput = () => { changes.name = nameInput.value; dirty(); };

  const badges = el("span", { class: "vw-badges" },
    el("span", { class: "vw-badge", text: `made ${made(v)}`, title: `id ${v.id}` }),
    el("span", { class: "vw-badge", text: v.on === "logbook" ? "this logbook" : "the whole location" }),
    v.owner ? el("span", { class: "vw-badge", text: `owner ${v.owner}` }) : null,
    v.personal ? el("span", { class: "vw-badge", text: "personal", title: "SciLog makes one of these per person; the name says whose" }) : null,
    sameName.length ? el("span", { class: "vw-badge warn", text: `name used ${sameName.length + 1}×` }) : null);

  const card = el("section", { class: `vw-card${sameName.length ? " vw-dup" : ""}`, id: `vw-${v.id}` }, el("div", { class: "vw-head" }, nameInput, badges));

  for (const other of sameName) {
    const diff = viewDifferences(v, other, names);
    card.append(el("div", { class: "vw-diff" },
      el("strong", { text: `Compared with the other "${other.name}" (made ${made(other)}): ` }),
      diff.length ? el("ul", {}, ...diff.map((d) => el("li", { text: d }))) : el("span", { text: "no difference in what they show -- one of them can go." })));
  }

  for (const p of v.panels) {
    const box = el("div", { class: "vw-panel" });
    const title = el("input", { class: "vw-title", value: p.title, placeholder: "(no title)" });
    if (!editable) title.setAttribute("readonly", "");
    title.oninput = () => { panelChange(p.index).title = title.value; dirty(); };
    box.append(el("div", { class: "vw-ptitle" }, el("span", { class: "vw-type", text: p.type || "panel" }), title,
      el("span", { class: "vw-size", text: `${p.size[0]}×${p.size[1]} at ${p.at[0]},${p.at[1]}` })));
    if (p.type === "logbook") {
      const ids = p.logbooks.map((l) => l.id);
      const chips = el("div", { class: "vw-chips" });
      const drawChips = () => {
        chips.replaceChildren(el("span", { class: "vw-chip main", text: p.target_name || (p.target === state.logbook.id ? state.logbook.name : "this logbook") }),
          ...ids.map((id, k) => el("span", { class: `vw-chip${names.has(id) ? "" : " unknown"}`, title: id }, logbookName(names, id),
            editable ? el("button", { title: "Remove from this panel", onclick: () => { ids.splice(k, 1); panelChange(p.index).logbooks = [...ids]; drawChips(); dirty(); } }, "×") : null)));
      };
      drawChips();
      box.append(el("div", { class: "vw-row" }, el("span", { class: "vw-label", text: `Shows (${ids.length + 1}):` }), chips));
      if (p.duplicates > 0) box.append(el("div", { class: "vw-hint", text: `${p.duplicates} logbook${p.duplicates > 1 ? "s are" : " is"} listed twice; saving this panel drops the doubles.` }));
      if (editable) {
        const listId = `vw-lbs-${v.id}-${p.index}`;
        const add = el("input", { class: "vw-add", placeholder: "Add a logbook: type its name", list: listId });
        const options = el("datalist", { id: listId }, ...data.logbooks.map((l) => el("option", { value: l.name })));
        add.onchange = () => {
          const hit = data.logbooks.find((l) => l.name === add.value);
          if (!hit) return;
          if (!ids.includes(hit.id) && hit.id !== p.target) { ids.push(hit.id); panelChange(p.index).logbooks = [...ids]; drawChips(); dirty(); }
          add.value = "";
        };
        box.append(el("div", { class: "vw-row" }, el("span", { class: "vw-label" }), add, options));
      }
    }
    for (const [key, label] of [["tags", "Only tags"], ["excludeTags", "Hide tags"]]) {
      const input = el("input", { class: "vw-tags", value: p[key].join(", "), placeholder: key === "tags" ? "(all)" : "(none)" });
      if (!editable) input.setAttribute("readonly", "");
      input.oninput = () => { panelChange(p.index)[key] = input.value.split(",").map((t) => t.trim()).filter(Boolean); dirty(); };
      box.append(el("div", { class: "vw-row" }, el("span", { class: "vw-label", text: `${label}:` }), input));
    }
    card.append(box);
  }

  if (editable) {
    save.onclick = async () => {
      save.disabled = true;
      const body = { logbook: state.logbook.id, version: v.version,
                     changes: Object.assign({}, "name" in changes ? { name: changes.name } : {}, changes.panels.size ? { panels: [...changes.panels.values()] } : {}) };
      try { await writeApi(`/api/view/${v.id}`, "POST", body); toast(`View "${body.changes.name || v.name}" saved in SciLog`); reload(); }
      catch (e) { toast(`Not saved: ${e.message}`); save.disabled = false; }
    };
    const del = el("button", { class: "vw-delete", onclick: async () => {
      if (!confirm(`Delete the view "${v.name}" (made ${made(v)}) from SciLog?\n\nIt disappears for everyone who uses this logbook. ` +
                   "A copy is kept on this computer (scilog_view_backups next to the viewer's cache).")) return;
      try { await writeApi(`/api/view/${v.id}/delete`, "POST", { logbook: state.logbook.id, version: v.version }); toast(`View "${v.name}" deleted`); reload(); }
      catch (e) { toast(`Not deleted: ${e.message}`); }
    } }, "Delete …");
    card.append(el("div", { class: "vw-actions" }, del, el("span", { class: "spacer" }), save));
  }
  return card;
}
