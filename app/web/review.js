"use strict";
/* Review page (/review). Top role only: the server enforces it. Mark which level can read each document. */
const { $, api, sleep, cap } = App;
let mock = false, levels = [], mockDocs = [
  { id: 3, title: "Hosting overview (sample).docx", file_type: "docx", level: "employee", chunks: 4, size_bytes: 9000 },
  { id: 2, title: "Backup runbook (sample).pdf", file_type: "pdf", level: "super", chunks: 6, size_bytes: 52000 },
  { id: 1, title: "Vendor agreement (sample).pdf", file_type: "pdf", level: "super", chunks: 12, size_bytes: 88000 }];

async function load() {
  let data;
  if (mock) data = { levels: ["employee", "super"], documents: mockDocs };
  else {
    const r = await api("/documents");
    if (!r.ok) { $("#rverr").textContent = r.status === 403 ? "Only the top role can review documents." : "Could not load documents (" + r.status + ")."; return; }
    data = await r.json();
  }
  levels = data.levels; render(data.documents);
}
function render(docs) {
  const body = $("#docbody"); body.textContent = "";
  $("#rvempty").hidden = docs.length > 0;
  docs.forEach((d) => {
    const tr = document.createElement("tr");
    const t = document.createElement("td"); t.textContent = d.title;
    const ty = document.createElement("td"); ty.textContent = d.file_type;
    const n = document.createElement("td"); n.textContent = String(d.chunks);
    const sel = document.createElement("select"); sel.setAttribute("aria-label", "Who can read " + d.title);
    levels.forEach((l) => { const o = document.createElement("option"); o.value = l; o.textContent = cap(l) + (l === levels[levels.length - 1] ? " only" : " and above"); sel.appendChild(o); });
    sel.value = d.level;
    const note = document.createElement("span"); note.className = "fine";
    sel.addEventListener("change", async () => {
      $("#rverr").textContent = ""; note.textContent = "saving…"; sel.disabled = true;
      try {
        if (mock) { await sleep(250); mockDocs.find((x) => x.id === d.id).level = sel.value; }
        else {
          const r = await api("/documents/level", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: d.id, level: sel.value }) });
          if (!r.ok) { let m = ""; try { m = (await r.json()).detail; } catch (e) {} throw new Error(m || "Could not save (" + r.status + ")"); }
        }
        d.level = sel.value; note.textContent = "saved ✓";
      } catch (e) { sel.value = d.level; note.textContent = ""; if (e.message !== "offline") $("#rverr").textContent = e.message; }
      finally { sel.disabled = false; }
    });
    const c = document.createElement("td"); c.append(sel, document.createTextNode(" "), note);
    tr.append(t, ty, n, c); body.appendChild(tr);
  });
}
App.start({
  page: "review",
  access: (s) => s.can_review === true,
  init: async () => { mock = App.isMock(); await load(); }
});
