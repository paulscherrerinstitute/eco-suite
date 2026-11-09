// eco SciLog viewer: writing. CKEditor 5 (the editor SciLog itself uses, same
// version, so posts look the same in both), in a dialog that does not take
// reading space. Images and files are uploaded to SciLog as soon as they are
// inserted; every change is kept as a draft in this browser until it is saved.
"use strict";

const CKE_VERSION = "48.5.2";
const CKE_BASE = `https://cdn.jsdelivr.net/npm/ckeditor5@${CKE_VERSION}/dist/browser`;
let ckeLoading = null;

function loadEditorCode() {
  if (window.CKEDITOR) return Promise.resolve(window.CKEDITOR);
  if (ckeLoading) return ckeLoading;
  ckeLoading = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `${CKE_BASE}/ckeditor5.css`;
    document.head.append(css);
    const script = document.createElement("script");
    script.src = `${CKE_BASE}/ckeditor5.umd.js`;
    script.onload = () => resolve(window.CKEDITOR);
    script.onerror = () => { ckeLoading = null; reject(new Error("the editor could not be loaded (no internet access from this browser?)")); };
    document.head.append(script);
  });
  return ckeLoading;
}

async function writeApi(path, method, body, isForm = false) {
  const r = await fetch(path, {
    method, credentials: "same-origin",
    headers: Object.assign({ "X-Eco-Scilog": "1" }, isForm ? {} : { "Content-Type": "application/json" }),
    body: isForm ? body : JSON.stringify(body),
  });
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

// -- uploads at insertion ----------------------------------------------------------------

class EcoUploadAdapter {
  constructor(loader, ctx) { this.loader = loader; this.ctx = ctx; }
  async upload() {
    const file = await this.loader.file;
    const form = new FormData();
    form.append("file", file, file.name || "pasted.png");
    this.ctx.pending++;
    this.ctx.status(`uploading ${file.name || "image"} ...`);
    try {
      const r = await writeApi(`/api/upload?logbook=${this.ctx.logbook}${this.ctx.postId ? `&post=${this.ctx.postId}` : ""}`, "POST", form, true);
      this.ctx.status(`${file.name || "image"} uploaded`);
      return { default: r.url };
    } catch (e) {
      this.ctx.status(`upload failed: ${e.message}`, true);
      throw e.message;
    } finally {
      this.ctx.pending--;
    }
  }
  abort() {}
}

// -- the dialog ----------------------------------------------------------------------------------

const draftKey = (logbook, postId) => `eco-scilog-draft:${logbook}:${postId || "new"}`;

function readDraft(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { return null; } }
function writeDraft(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* full or private */ } }
function dropDraft(key) { try { localStorage.removeItem(key); } catch (e) { /* private */ } }

// how long a new post stays editable; SciLog's own default is 3 days
const EDIT_DAYS_DEFAULT = 30;

async function openEditor({ logbook, logbookName, postId = null, onSaved, near = null }) {
  if (document.getElementById("editor-dialog")) { document.getElementById("editor-dialog").showModal(); return; }
  let start = { html: "", tags: [], updated: null };
  const key = draftKey(logbook, postId) + (near ? `:${near.where}:${near.id}` : "");
  const dialog = el("dialog", { id: "editor-dialog" });
  const statusLine = el("span", { class: "ed-status" });
  const title = el("strong", { text: postId ? "Edit post" : near ? `New post, inserted ${near.where === "before" ? "before" : "after"} ${near.label}` : `New post in ${logbookName}` });
  const days = el("input", { type: "number", min: "1", max: "3650", step: "1", class: "ed-days",
    value: storageGet("eco-scilog-edit-days") || String(EDIT_DAYS_DEFAULT) });
  const period = postId ? null : el("label", { class: "ed-period", title: "How long this post stays editable in SciLog (SciLog's own default is 3 days). Set once, when posting." },
    "Edit period ", days, " days");
  const tagsInput = el("input", { class: "ed-tags", placeholder: "Tags, comma separated", autocomplete: "off" });
  const area = el("div", { class: "ed-area" }, el("p", { class: "hint", text: "Loading the editor ..." }));
  const saveButton = el("button", { class: "primary", title: "Save to SciLog (Ctrl+Enter)" }, postId ? "Save changes" : "Post");
  const attach = el("input", { type: "file", multiple: "", hidden: "" });
  const ctx = {
    logbook, postId, pending: 0,
    status(text, bad = false) { statusLine.textContent = text; statusLine.classList.toggle("bad", bad); },
  };
  dialog.append(
    el("div", { class: "ed-head" }, title, el("span", { class: "spacer" }),
      el("button", { title: "Full screen", onclick: () => dialog.classList.toggle("full") }, "⤢"),
      el("button", { title: "Close (the draft is kept)", onclick: () => close() }, "✕")),
    area,
    el("div", { class: "ed-foot" },
      tagsInput, period,
      el("button", { title: "Attach files: HTML files become interactive plots", onclick: () => attach.click() }, "📎 Attach"),
      attach, statusLine, el("span", { class: "spacer" }),
      el("button", { onclick: () => close() }, "Cancel"), saveButton));
  document.body.append(dialog);
  dialog.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  dialog.showModal();

  let editor = null;
  let dirty = false;

  function close(force = false) {
    if (!force && dirty && !confirm("Close the editor? Your changes stay as a draft in this browser and come back the next time you open it.")) return;
    editor?.destroy();
    dialog.close();
    dialog.remove();
  }

  try {
    if (postId) {
      start = await api(`/api/edit/${postId}?logbook=${logbook}`);
    }
    const CK = await loadEditorCode();
    const draft = readDraft(key);
    let initial = start.html, tags = start.tags;
    if (draft && draft.html !== start.html && confirm(`Restore the unsaved draft from ${new Date(draft.t).toLocaleString()}?`)) {
      initial = draft.html;
      tags = draft.tags;
    }
    tagsInput.value = (tags || []).join(", ");
    area.replaceChildren();
    const holder = el("div");
    area.append(holder);
    editor = await CK.ClassicEditor.create(holder, editorConfig(CK, ctx, initial));
    editor.plugins.get("FileRepository").createUploadAdapter = (loader) => new EcoUploadAdapter(loader, ctx);
    window.ecoEditor = editor;  // the open editor, for the browser console (and tests)
    const keep = () => { dirty = true; writeDraft(key, { html: editor.getData(), tags: tagList(), t: Date.now() }); ctx.status("draft kept in this browser"); };
    editor.model.document.on("change:data", keep);
    tagsInput.addEventListener("input", keep);
    editor.editing.view.focus();
  } catch (e) {
    area.replaceChildren(el("p", { class: "hint bad", text: `The editor could not open: ${e.message}` }));
    return;
  }

  function tagList() { return tagsInput.value.split(",").map((t) => t.trim()).filter(Boolean); }

  attach.onchange = async () => {
    for (const file of attach.files) {
      const form = new FormData();
      form.append("file", file, file.name);
      ctx.pending++;
      ctx.status(`uploading ${file.name} ...`);
      try {
        const r = await writeApi(`/api/upload?logbook=${logbook}${postId ? `&post=${postId}` : ""}`, "POST", form, true);
        if (r.image) editor.execute("insertImage", { source: r.url });
        else editor.model.change((writer) => {
          const link = writer.createText(r.name, { linkHref: r.url });
          editor.model.insertContent(link, editor.model.document.selection);
        });
        ctx.status(`${file.name} attached${/\.html?$/i.test(file.name) ? " (shown as an interactive plot)" : ""}`);
      } catch (e) {
        ctx.status(`${file.name}: ${e.message}`, true);
      } finally {
        ctx.pending--;
      }
    }
    attach.value = "";
  };

  async function save() {
    if (ctx.pending) { ctx.status("wait until the uploads have finished", true); return; }
    const html = editor.getData();
    if (/<img[^>]+src="(data|blob):/.test(html)) { ctx.status("an image has not been uploaded yet", true); return; }
    if (!html.trim()) { ctx.status("the post is empty", true); return; }
    saveButton.disabled = true;
    ctx.status("saving to SciLog ...");
    try {
      const body = { logbook, html, tags: tagList(), updated: start.updated };
      if (!postId) {
        const d = Number(days.value);
        if (!(d >= 1 && d <= 3650)) { ctx.status("the edit period must be 1 to 3650 days", true); saveButton.disabled = false; return; }
        body.edit_days = d;
        storageSet("eco-scilog-edit-days", String(d));
        if (near) body[near.where === "before" ? "before" : "after"] = near.id;
      }
      const post = postId ? await writeApi(`/api/save/${postId}`, "PATCH", body) : await writeApi("/api/save", "POST", body);
      dropDraft(key);
      dirty = false;
      close(true);
      onSaved && onSaved(post);
    } catch (e) {
      ctx.status(`not saved: ${e.message}`, true);  // the draft stays
      saveButton.disabled = false;
    }
  }
  saveButton.onclick = save;
  dialog.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); } });
}

function editorConfig(CK, ctx, initial) {
  const want = [
    "Essentials", "Autoformat", "BlockQuote", "Bold", "Italic", "Underline", "Strikethrough", "Subscript", "Superscript",
    "Code", "CodeBlock", "Heading", "Highlight", "FontColor", "FontBackgroundColor", "Image", "ImageCaption", "ImageResize",
    "ImageStyle", "ImageToolbar", "ImageUpload", "ImageInsertViaUrl", "Link", "List", "ListProperties", "Paragraph", "PasteFromOffice",
    "RemoveFormat", "Table", "TableToolbar", "TableProperties", "TableCellProperties", "Indent", "IndentBlock",
    "HorizontalLine", "SpecialCharacters", "SpecialCharactersEssentials", "Alignment", "FindAndReplace", "SelectAll",
  ];
  return {
    licenseKey: "GPL",
    plugins: want.map((n) => CK[n]).filter(Boolean),
    initialData: initial,
    toolbar: {
      items: ["heading", "|", "bold", "italic", "underline", "strikethrough", "code", "subscript", "superscript", "highlight",
        "fontColor", "|", "bulletedList", "numberedList", "outdent", "indent", "alignment", "|", "link", "uploadImage",
        "insertTable", "codeBlock", "blockQuote", "horizontalLine", "|", "findAndReplace", "removeFormat", "undo", "redo"],
      shouldNotGroupWhenFull: false,
    },
    heading: {
      options: [
        { model: "paragraph", title: "Text", class: "ck-heading_paragraph" },
        { model: "heading2", view: "h2", title: "Heading", class: "ck-heading_heading2" },
        { model: "heading3", view: "h3", title: "Subheading", class: "ck-heading_heading3" },
        { model: "heading4", view: "h4", title: "Minor heading", class: "ck-heading_heading4" },
      ],
    },
    image: {
      // fixed sizes in pixels, set with the mouse or the size menu: shown at
      // that size everywhere, only ever shrunk to fit a narrow screen
      resizeUnit: "px",
      resizeOptions: [
        { name: "resizeImage:original", value: null, label: "Original size" },
        { name: "resizeImage:300", value: "300", label: "300 px" },
        { name: "resizeImage:500", value: "500", label: "500 px" },
        { name: "resizeImage:800", value: "800", label: "800 px" },
      ],
      toolbar: ["imageStyle:inline", "imageStyle:alignBlockLeft", "imageStyle:block", "imageStyle:alignBlockRight",
        "imageStyle:wrapText", "|", "resizeImage", "|", "toggleImageCaption", "imageTextAlternative"],
    },
    table: { contentToolbar: ["tableColumn", "tableRow", "mergeTableCells", "tableProperties", "tableCellProperties"] },
    link: { defaultProtocol: "https://", addTargetToExternalLinks: true },
    codeBlock: {
      languages: [
        { language: "python", label: "Python" }, { language: "bash", label: "Bash" }, { language: "plaintext", label: "Text" },
        { language: "json", label: "JSON" }, { language: "yaml", label: "Yaml" }, { language: "cpp", label: "C++" },
        { language: "matlab", label: "MATLAB" }, { language: "julia", label: "Julia" }, { language: "html", label: "HTML" },
      ],
    },
    placeholder: "Write here. Paste or drop images: they are uploaded at once.",
  };
}

// -- heading levels from the outline ----------------------------------------------------------

async function setHeading(logbook, postId, index, level) {
  return writeApi(`/api/heading/${postId}`, "POST", { logbook, index, level });
}
