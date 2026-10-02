"use strict";
/* Add knowledge page (/add). NO role check here: anyone can add. Access to what is added is set later by a reviewer. */
const { $, api, sleep } = App;
let settings = null, mock = false, queue = [], nextId = 1;

/* ---------- PROVIDER ---------- */
function initProvider() {
  mock = App.isMock();
  $("#limits").textContent = "Allowed: " + settings.allowed_extensions.join(", ") + " · max " + settings.max_file_mb + " MB";
  const box = $("#dropbox");
  ["dragenter", "dragover"].forEach((ev) => box.addEventListener(ev, (e) => { e.preventDefault(); box.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => box.addEventListener(ev, (e) => { e.preventDefault(); box.classList.remove("over"); }));
  box.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
  window.addEventListener("dragover", (e) => e.preventDefault());   // a file that misses the box must not open in the browser
  window.addEventListener("drop", (e) => e.preventDefault());
  $("#browse").addEventListener("click", () => $("#picker").click());
  $("#picker").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  $("#entry").addEventListener("input", refreshQueue);
  $("#clear").addEventListener("click", () => { queue = []; $("#entry").value = ""; refreshQueue(); });
  $("#submit").addEventListener("click", submitAll);
}
function addFiles(list) {
  for (const f of list) {
    const ext = (f.name.split(".").pop() || "").toLowerCase();
    const item = { id: nextId++, file: f, name: f.name, size: f.size, status: "wait", note: "ready" };
    if (!settings.allowed_extensions.includes(ext)) { item.status = "bad"; item.note = "file type not allowed"; }
    else if (f.size > settings.max_file_mb * 1024 * 1024) { item.status = "bad"; item.note = "too large"; }
    queue.push(item);
  }
  refreshQueue();
}
function refreshQueue() {
  const ul = $("#queue"); ul.textContent = "";
  queue.forEach((it) => {
    const li = document.createElement("li");
    const n = document.createElement("span"); n.className = "name"; n.textContent = it.name; n.title = it.name;
    const s = document.createElement("span"); s.className = "st-" + it.status; s.textContent = it.note;
    const x = document.createElement("button"); x.className = "ghost"; x.type = "button"; x.textContent = "✕"; x.setAttribute("aria-label", "Remove " + it.name);
    x.addEventListener("click", () => { queue = queue.filter((q) => q.id !== it.id); refreshQueue(); });
    li.append(n, s, x); ul.appendChild(li);
  });
  const hasText = $("#entry").value.trim().length > 0;
  $("#qempty").hidden = queue.length > 0 || hasText;
  $("#submit").disabled = !(hasText || queue.some((q) => q.status === "wait"));
}
async function submitAll() {
  const btn = $("#submit"); btn.disabled = true;
  const jobs = queue.filter((q) => q.status === "wait");
  const text = $("#entry").value.trim();
  if (text) jobs.push({ id: nextId++, text, name: "Typed entry", status: "wait", note: "ready", isText: true });
  for (const it of jobs) {
    it.status = "wait"; it.note = "sending…"; if (!queue.includes(it)) queue.push(it); refreshQueue();
    try {
      if (mock) { await sleep(500); it.status = "ok"; it.note = "sent"; }
      else {
        const fd = new FormData();
        if (it.isText) fd.append("text", it.text); else fd.append("file", it.file);
        const r = await api("/upload", { method: "POST", body: fd });
        if (r.ok) { it.status = "ok"; it.note = "uploaded"; }
        else { let m = ""; try { m = (await r.json()).detail; } catch (e) {} it.status = "bad"; it.note = typeof m === "string" && m ? m : "failed (" + r.status + ")"; }
      }
    } catch (e) { it.status = "bad"; it.note = e.message === "offline" ? "service offline" : "network error"; }
    if (it.isText && it.status === "ok") $("#entry").value = "";
    refreshQueue();
  }
}

App.start({
  page: "add",
  init: async (s) => {
    settings = s; initProvider();
    const pre = sessionStorage.getItem("prefill");   // set by the Ask page ("Add a correction")
    if (pre) { sessionStorage.removeItem("prefill"); $("#entry").value = pre; refreshQueue(); }
    $("#entry").focus();
  }
});
