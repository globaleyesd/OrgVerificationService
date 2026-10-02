"use strict";
/* Ask page (/ask). Role-checked on the server; this page only reflects what the server says. */
const { $, api, sleep, cap } = App;
let settings = null, mock = false, history = [];
const mockCtx = {};

/* ---------- CONSUMER ---------- */
const SAMPLE_QS = [
  "When does the reporting platform license expire?",
  "What's the overall structure of our cloud services?",
  "Who owns the renewal process?",
  "What's the password policy for the billing system?",
  "And when does that expire?"
];
function initAsk() {
  mock = App.isMock();
  $("#send").addEventListener("click", () => ask());
  $("#q").addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
  $("#q").addEventListener("input", () => { $("#voicehint").hidden = true; hideSuggest(); });   // typing by hand hides the voice notes
  $("#collapse").addEventListener("click", collapseSource);
  $("#srctab").addEventListener("click", reopenSource);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#sources").classList.contains("open")) collapseSource(); });
  initResizer();
  addMsg("note", "Ask a question. Every claim links to the passage it came from.");
  if (mock) {
    const box = document.createElement("div"); box.id = "samples";
    SAMPLE_QS.forEach((t) => { const b = document.createElement("button"); b.type = "button"; b.textContent = t; b.addEventListener("click", () => ask(t)); box.appendChild(b); });
    $("#log").appendChild(box);
  }
  initMic();
}
function goAdd(text) { sessionStorage.setItem("prefill", text); location.href = App.pageUrl("add"); }
/* Microphone. While the user speaks, the words appear in the question box letter by letter as they are
   recognised (interim results, revised as the recogniser firms them up). Listening ends the way a person would
   decide someone has finished: after a natural pause, longer when the last words were still being formed,
   never on the first short breath. The user can also stop it by pressing the mic again. */
const END_PAUSE_MS = 1800;        // a pause this long after finished words ends the turn
const MIDWORD_PAUSE_MS = 2600;    // longer if the recogniser was still mid-phrase (the speaker may be thinking)
const NO_SPEECH_MS = 8000;        // nothing said at all: give up and say so
const MAX_LISTEN_MS = 60000;      // safety cap for one question
let typeTarget = "", typeTimer = null;
function typeTo(target, instant) {   // show recognised text one letter at a time, catching up with the speaker
  typeTarget = target;
  const q = $("#q");
  let keep = 0; while (keep < q.value.length && keep < target.length && q.value[keep] === target[keep]) keep++;
  if (keep < q.value.length) q.value = q.value.slice(0, keep);   // the recogniser revised earlier words
  if (instant) { q.value = target; return; }
  if (typeTimer) return;
  typeTimer = setInterval(() => {
    if (q.value.length >= typeTarget.length) { clearInterval(typeTimer); typeTimer = null; return; }
    const behind = typeTarget.length - q.value.length;
    q.value = typeTarget.slice(0, q.value.length + (behind > 24 ? 3 : 1));   // speed up when far behind
    q.scrollLeft = q.scrollWidth;
  }, 22);
}
function micHint(text) { const h = $("#voicehint"); h.textContent = text; h.hidden = !text; }

/* Speech recognition often mishears. After the microphone, ask the server what the person most likely meant (from the
   passages they may read and the conversation) and offer both: the suggestion and exactly what was heard. Either one
   asks straight away; the box keeps what was heard, so it can also be edited by hand. */
const MOCK_MISHEARD = "whats the over all structure of are cloud services";
let suggestSeq = 0;
function hideSuggest() { suggestSeq++; $("#suggest").hidden = true; }
function choice(kind, label, text) {
  const b = document.createElement("button"); b.type = "button"; b.className = "choice " + kind;
  const k = document.createElement("span"); k.className = "k"; k.textContent = label;
  const v = document.createElement("span"); v.className = "v"; v.textContent = text;
  b.append(k, v);
  b.addEventListener("click", () => { hideSuggest(); $("#q").value = text; ask(text); });
  return b;
}
async function offerSuggestion(heard) {
  const mine = ++suggestSeq, box = $("#suggest");
  $("#suggestlabel").textContent = "Checking what you meant\u2026"; $("#suggestchoices").textContent = ""; box.hidden = false;
  let s = null;
  try {
    if (mock) { await sleep(600); s = SAMPLE_QS[1]; }
    else {
      const r = await api("/suggest", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: heard, history: history.slice(-6) }) });
      if (r.ok) s = (await r.json()).suggestion;
    }
  } catch (e) { s = null; }
  if (mine !== suggestSeq) return;                 // typed, asked or spoke again meanwhile
  if (!s) { box.hidden = true; micHint("Transcribed from voice. Edit if needed, then press Ask."); return; }
  $("#suggestlabel").textContent = "Speech recognition can mishear. Which did you mean?";
  $("#suggestchoices").append(choice("primary", "Did you mean", s), choice("plain", "What I said", heard));
}
function initMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition, mic = $("#mic");
  if (mock) {   // sample mode: simulate a spoken question, typed live, so the demo always works
    mic.hidden = false;
    mic.addEventListener("click", () => {
      const words = MOCK_MISHEARD.split(" "); let n = 0;
      mic.setAttribute("aria-pressed", "true"); $("#q").value = ""; micHint("Listening… speak naturally; it stops after a pause.");
      const t = setInterval(() => {
        n++; typeTo(words.slice(0, n).join(" "));
        if (n >= words.length) { clearInterval(t); setTimeout(() => { mic.setAttribute("aria-pressed", "false"); typeTo(MOCK_MISHEARD, true); micHint(""); $("#q").focus(); offerSuggestion(MOCK_MISHEARD); }, END_PAUSE_MS); }
      }, 260);
    });
    return;
  }
  if (settings.speech_mode !== "browser" || !SR) return;
  mic.hidden = false;
  const rec = new SR();
  rec.lang = navigator.language || "en-US"; rec.interimResults = true; rec.continuous = true; rec.maxAlternatives = 1;
  let listening = false, heard = "", endTimer = null, noSpeechTimer = null, capTimer = null;
  const clearTimers = () => [endTimer, noSpeechTimer, capTimer].forEach((t) => t && clearTimeout(t));
  const stop = () => { if (listening) { try { rec.stop(); } catch (e) {} } };
  rec.onresult = (e) => {
    let finalText = "", interim = "";
    for (let i = 0; i < e.results.length; i++) {
      if (e.results[i].isFinal) finalText += e.results[i][0].transcript; else interim += e.results[i][0].transcript;
    }
    heard = (finalText + interim).replace(/\s+/g, " ").trim();
    typeTo(heard);
    if (noSpeechTimer) { clearTimeout(noSpeechTimer); noSpeechTimer = null; }
    if (endTimer) clearTimeout(endTimer);
    endTimer = setTimeout(stop, interim.trim() ? MIDWORD_PAUSE_MS : END_PAUSE_MS);   // restart the pause clock on every new sound
  };
  rec.onerror = (e) => {
    const why = { "not-allowed": "Microphone access was blocked. Allow it in the browser to speak your question.",
                  "no-speech": "Didn't hear anything. Press the mic and try again.",
                  "network": "Speech recognition needs a network connection in this browser." }[e.error];
    if (why) micHint(why);
  };
  rec.onend = () => {
    listening = false; clearTimers(); mic.setAttribute("aria-pressed", "false");
    if (heard) { typeTo(heard, true); micHint(""); $("#q").focus(); offerSuggestion(heard); }
    else if (!$("#voicehint").textContent.startsWith("Didn't") && !$("#voicehint").textContent.startsWith("Microphone")) micHint("Didn't hear anything. Press the mic and try again.");
  };
  mic.addEventListener("click", () => {
    if (listening) { stop(); return; }
    heard = ""; $("#q").value = ""; typeTarget = "";
    try { rec.start(); } catch (e) { return; }
    listening = true; mic.setAttribute("aria-pressed", "true");
    micHint("Listening… speak naturally; it stops after a pause. Press the mic to stop sooner.");
    noSpeechTimer = setTimeout(() => { micHint("Didn't hear anything. Press the mic and try again."); stop(); }, NO_SPEECH_MS);
    capTimer = setTimeout(stop, MAX_LISTEN_MS);
  });
}
function addMsg(kind, text) {
  const d = document.createElement("div"); d.className = "msg " + kind; d.textContent = text;
  $("#log").appendChild(d); $("#log").scrollTop = $("#log").scrollHeight; return d;
}
async function ask(text) {
  const qEl = $("#q"), question = (typeof text === "string" ? text : qEl.value).trim(); if (!question) return;
  qEl.value = ""; $("#voicehint").hidden = true; hideSuggest();
  const samples = $("#samples"); if (samples) samples.remove();
  addMsg("user", question); history.push({ role: "user", text: question });
  const wait = addMsg("note", "Searching…"); $("#send").disabled = true;
  try {
    let data;
    if (mock) { await sleep(700); data = mockReply(question, App.mockLevel()); }
    else {
      const r = await api("/ask", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history: history.slice(-7, -1) }) });
      if (r.status === 403) throw new Error("You don't have access to ask questions.");
      if (!r.ok) { let m = ""; try { m = (await r.json()).detail; } catch (e) {} throw new Error(typeof m === "string" && m ? m : "Something went wrong (" + r.status + ").");}
      data = await r.json();
    }
    wait.remove(); renderAnswer(data, question); history.push({ role: "assistant", text: data.answer });
  } catch (e) { wait.remove(); if (e.message !== "offline") addMsg("note", e.message || "Network error."); }
  finally { $("#send").disabled = false; qEl.focus(); }
}
/* Colours: every citation gets the next colour in the palette, carrying on across answers, so [1] in one answer
   never looks like [1] in another. Each place its quote appears gets a shade of that colour: the first is
   strongest, later ones lighter (s0..s3). Question words are a neutral grey. */
const PALETTE = 8;
let nextColor = 0;
function colorize(data) {
  data._color = new Map((data.citations || []).map((c) => [c.id, (nextColor++) % PALETTE]));
  const seen = new Map();
  (data.matches || []).forEach((m) => (m.quote_spans || []).forEach((s) => {
    const n = seen.get(s.cite) || 0; seen.set(s.cite, n + 1);
    s.cls = quoteClass(data, s.cite, n);
  }));
}
function colorOf(data, id) { return data && data._color && data._color.has(id) ? data._color.get(id) : 0; }
function quoteClass(data, id, n) { return "hc" + colorOf(data, id) + " s" + Math.min(n, 3); }
function renderAnswer(data, question) {
  colorize(data);
  const d = document.createElement("article"); d.className = "msg answer";
  const cites = new Map((data.citations || []).map((c) => [String(c.id), c]));
  const label = document.createElement("div"); label.className = "label"; label.textContent = cites.size ? "Answer" : "Result";
  const body = document.createElement("p"); body.className = "body";
  String(data.answer || "").split(/(\[\d+\])/).forEach((part) => {   // [1] markers become buttons; the rest stays plain text
    const m = part.match(/^\[(\d+)\]$/);
    if (m && cites.has(m[1])) { const b = citeButton(cites.get(m[1]), m[1], data); b.classList.add("inref"); b.textContent = m[1]; body.appendChild(b); }
    else body.appendChild(document.createTextNode(part));
  });
  d.append(label, body);
  if (cites.size) {   // a reference list, one entry per place each cited quote appears
    const refs = document.createElement("section"); refs.className = "refs";
    const h = document.createElement("div"); h.className = "label"; h.textContent = "Sources";
    const ol = document.createElement("ol");
    cites.forEach((c, id) => occurrences(data, c).forEach((o) => {
      const li = document.createElement("li");
      const b = citeButton(o, id, data); b.classList.add("s" + shadeOf(o)); b.textContent = "[" + id + "]";
      const title = document.createElement("cite"); title.textContent = o.document_title;
      const where = document.createElement("span"); where.className = "where";
      where.textContent = (o.location_label ? ", " + o.location_label.replace(/^Page /, "p. ") : "") + (o.level ? " · " + cap(o.level) : "") + (o.verified ? " · quote verified ✓" : "");
      li.append(b, title, where); li.addEventListener("click", (e) => { if (e.target !== b) showSource(o, data); });
      ol.appendChild(li);
    }));
    refs.append(h, ol); d.appendChild(refs);
  }
  if (data.withheld) {
    const w = document.createElement("div"); w.className = "withheld";
    w.textContent = "Some sources were not available at your access level."; d.appendChild(w);
  }
  const acts = document.createElement("div"); acts.className = "actions";
  (data.actions || []).forEach((a) => {
    if (a === "diagram" && data.diagram) acts.appendChild(actionBtn("Show as diagram", (btn) => toggleDiagram(d, data, btn)));
    if (a === "correct") acts.appendChild(actionBtn("Add a correction", () => goAdd("Correction: ")));
    if (a === "add_knowledge") acts.appendChild(actionBtn("Add knowledge about this", () => goAdd("About: " + question + "\n")));
  });
  const nm = (data.matches || []).length;
  if (nm) acts.appendChild(actionBtn("Show all " + nm + " matching passage" + (nm === 1 ? "" : "s"), () => showMatches(data)));
  if (cites.size) acts.appendChild(actionBtn("Copy answer with sources", (btn) => copyAnswer(data, btn)));
  if (acts.childNodes.length) d.appendChild(acts);
  $("#log").appendChild(d); $("#log").scrollTop = $("#log").scrollHeight;
}
/* Every place a citation's quote appears among the matching passages (other pages, other documents), as sources
   the panel can open. The server marked these spans by finding the verified quote's exact words in each passage. */
function occurrences(data, c) {
  const occ = (data.matches || []).filter((m) => (m.quote_spans || []).some((s) => s.cite === c.id)).map((m) => ({
    id: c.id, document_title: m.document_title, location_label: m.location_label, level: m.level, text: m.text, verified: true,
    highlights: m.quote_spans.filter((s) => s.cite === c.id), terms: m.highlights }));
  if (occ.length) return occ;
  return [Object.assign({}, c, { highlights: (c.highlights || (c.highlight ? [c.highlight] : [])).map((h, n) => Object.assign({}, h, { cls: quoteClass(data, c.id, n) })) })];
}
function shadeOf(o) { const h = (o.highlights || [])[0]; const m = h && h.cls && h.cls.match(/s(\d)/); return m ? m[1] : "0"; }
function sourceLabel(o) { return o.document_title + (o.location_label ? " · " + o.location_label : "") + (o.level ? " · " + cap(o.level) : ""); }
function actionBtn(label, fn) {
  const b = document.createElement("button"); b.type = "button"; b.className = "ghost"; b.textContent = label;
  b.addEventListener("click", () => fn(b)); return b;
}
async function copyAnswer(data, btn) {
  const lines = [String(data.answer || ""), "", "Sources:"].concat((data.citations || []).flatMap((c) =>
    occurrences(data, c).map((o) => "[" + c.id + "] " + o.document_title + (o.location_label ? " (" + o.location_label + ")" : ""))));
  const old = btn.textContent;
  try { await navigator.clipboard.writeText(lines.join("\n")); btn.textContent = "Copied ✓"; }
  catch (e) { btn.textContent = "Couldn't copy"; }
  setTimeout(() => { btn.textContent = old; }, 1600);
}
function toggleDiagram(msgEl, data, btn) {
  const old = msgEl.querySelector("svg.diagram");
  if (old) { old.remove(); btn.textContent = "Show as diagram"; return; }
  const cites = new Map((data.citations || []).map((c) => [String(c.id), c]));
  const NS = "http://www.w3.org/2000/svg", nodes = data.diagram.nodes, W = 320, H = 54, GAP = 34;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "diagram"); svg.setAttribute("viewBox", "0 0 " + W + " " + (nodes.length * (H + GAP) - GAP + 4));
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Diagram of the services and how they connect"); svg.setAttribute("width", W);
  nodes.forEach((n, i) => {
    const y = 2 + i * (H + GAP);
    if (i > 0) {
      const ln = document.createElementNS(NS, "line"); ln.setAttribute("x1", W / 2); ln.setAttribute("x2", W / 2); ln.setAttribute("y1", y - GAP); ln.setAttribute("y2", y - 6); svg.appendChild(ln);
      const ar = document.createElementNS(NS, "path"); ar.setAttribute("class", "arrow");
      ar.setAttribute("d", "M" + (W / 2 - 5) + " " + (y - 8) + " L" + (W / 2 + 5) + " " + (y - 8) + " L" + (W / 2) + " " + (y - 1) + " Z"); svg.appendChild(ar);
    }
    const g = document.createElementNS(NS, "g"); g.setAttribute("class", "node"); g.setAttribute("tabindex", "0"); g.setAttribute("role", "button"); g.setAttribute("aria-label", n.label + ". Show source.");
    const r = document.createElementNS(NS, "rect"); r.setAttribute("x", 2); r.setAttribute("y", y); r.setAttribute("width", W - 4); r.setAttribute("height", H); r.setAttribute("rx", 10);
    const t = document.createElementNS(NS, "text"); t.setAttribute("x", W / 2); t.setAttribute("y", y + H / 2 + 5); t.setAttribute("text-anchor", "middle"); t.textContent = n.label;
    g.append(r, t);
    const open = () => { const c = cites.get(String(n.cite)); if (c) showSource(c); };
    g.addEventListener("click", open); g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    svg.appendChild(g);
  });
  msgEl.insertBefore(svg, msgEl.querySelector(".actions")); btn.textContent = "Hide diagram";
}
function citeButton(c, id, data) {
  const b = document.createElement("button"); b.type = "button"; b.className = "cite hc" + colorOf(data, Number(id)); b.textContent = id;
  b.setAttribute("aria-label", "Show source " + id + ": " + c.document_title);
  b.addEventListener("click", () => showSource(c, data)); return b;
}

/* ---------- source panel: collapsible and resizable ---------- */
let haveSource = false;
function showSource(c, data) {
  haveSource = true;
  $("#srctitle").textContent = c.document_title;
  $("#srcwhere").textContent = (c.location_label || "") + (c.level ? " · " + cap(c.level) : "");
  $("#srcverified").hidden = !c.verified;
  const el = $("#passage"), text = String(c.text || ""); el.textContent = ""; el.classList.remove("placeholder", "list");
  const occ = data ? occurrences(data, c) : [c], box = $("#srcocc"); box.textContent = "";
  const here = occ.find((o) => o.text === c.text && o.location_label === c.location_label) || c;
  if (occ.length > 1) {
    const label = document.createElement("div"); label.className = "where"; label.textContent = "This quote appears in " + occ.length + " places:"; box.appendChild(label);
    occ.forEach((o) => {
      const b = document.createElement("button"); b.type = "button";
      b.className = "cite hc" + colorOf(data, c.id) + " s" + shadeOf(o) + (o === here ? " current" : "");
      b.textContent = sourceLabel(o); b.addEventListener("click", () => showSource(o, data)); box.appendChild(b);
    });
  }
  box.hidden = occ.length < 2;
  const nm = data && (data.matches || []).length;
  $("#srcall").hidden = !nm;
  if (nm) { $("#srcall").textContent = "All " + nm + " matching passages"; $("#srcall").onclick = () => showMatches(data); }
  el.appendChild(markedText(text, here.highlights || [], here.terms || c.terms));
  legend(data, c.id ? [c.id] : []);
  const first = el.querySelector("mark.quote") || el.querySelector("mark");
  if (first) setTimeout(() => first.scrollIntoView({ block: "center" }), 0);
  reopenSource();
}
/* Every passage the search found for this question, from every document the user may read, with the
   question's words highlighted. Ranges come from the server; text is only ever set with textContent. */
function markedText(text, quoteSpans, termSpans) {
  // per character: 0 = plain, 1 = a question word, k + 2 = quote span k (quotes win where they overlap).
  // Every occurrence is marked; each quote span keeps its colour and shade class (cls).
  const kind = new Int32Array(text.length), quotes = quoteSpans || [];
  const ok = (h) => Number.isInteger(h.start) && Number.isInteger(h.end) && h.start >= 0 && h.end <= text.length && h.end > h.start;
  (termSpans || []).forEach((h) => { if (ok(h)) for (let i = h.start; i < h.end; i++) if (!kind[i]) kind[i] = 1; });
  quotes.forEach((h, k) => { if (ok(h)) for (let i = h.start; i < h.end; i++) kind[i] = k + 2; });
  const frag = document.createDocumentFragment();
  for (let i = 0; i < text.length;) {
    let j = i; while (j < text.length && kind[j] === kind[i]) j++;
    if (kind[i]) {
      const mk = document.createElement("mark");
      mk.className = kind[i] === 1 ? "term" : "quote " + (quotes[kind[i] - 2].cls || "hc0 s0");
      mk.textContent = text.slice(i, j); frag.appendChild(mk);
    } else frag.appendChild(document.createTextNode(text.slice(i, j)));
    i = j;
  }
  return frag;
}
function legend(data, ids) {
  const box = $("#srclegend"); box.textContent = "";
  ids.forEach((id) => {
    const sw = document.createElement("span"); sw.className = "swatch hc" + colorOf(data, id); sw.textContent = "[" + id + "]"; box.appendChild(sw);
  });
  const t = document.createElement("span");
  t.textContent = (ids.length ? "Each source has its own colour; the first place a quote appears is darkest, later places lighter. " : "") + "Grey: words from your question.";
  box.appendChild(t); box.hidden = false;
}
function showMatches(data) {
  haveSource = true;
  const ms = data.matches || [], docs = new Set(ms.map((m) => m.document_id));
  const cites = new Map((data.citations || []).map((c) => [c.id, c]));
  $("#srctitle").textContent = "All matching passages";
  $("#srcwhere").textContent = ms.length + " passage" + (ms.length === 1 ? "" : "s") + " in " + docs.size + " document" + (docs.size === 1 ? "" : "s") + ", best match first.";
  legend(data, (data.citations || []).map((c) => c.id));
  $("#srcverified").hidden = true; $("#srcall").hidden = true; $("#srcocc").hidden = true;
  const el = $("#passage"); el.textContent = ""; el.classList.remove("placeholder"); el.classList.add("list");
  ms.forEach((m) => {
    const card = document.createElement("article"); card.className = "match" + (m.cited ? " cited" : "");
    const meta = document.createElement("div"); meta.className = "meta";
    const num = document.createElement("span"); num.className = "num"; num.textContent = "Excerpt " + (ms.indexOf(m) + 1);
    const title = document.createElement("cite"); title.textContent = m.document_title;
    meta.appendChild(num);
    meta.append(title, document.createTextNode((m.location_label ? " · " + m.location_label : "") + (m.level ? " · " + cap(m.level) : "")));
    (m.cites || (m.cited ? [m.cited] : [])).forEach((n) => {
      if (!cites.has(n)) return;
      const b = citeButton(cites.get(n), n, data); b.textContent = "cited [" + n + "]"; meta.appendChild(b);
    });
    const body = document.createElement("div"); body.className = "text"; body.appendChild(markedText(String(m.text || ""), m.quote_spans, m.highlights));
    card.append(meta, body); el.appendChild(card);
  });
  el.scrollTop = 0; $("#sources").scrollTop = 0;
  reopenSource();
}
function reopenSource() {
  if (!haveSource) {   // opened from the sidebar before any source was picked
    $("#srctitle").textContent = "Sources"; $("#srcwhere").textContent = ""; $("#srcverified").hidden = true; $("#srcall").hidden = true; $("#srcocc").hidden = true; $("#srclegend").hidden = true;
    const el = $("#passage"); el.textContent = "Select a source marker in an answer to see the passage here."; el.classList.add("placeholder");
  }
  $("#sources").classList.add("open"); $("#srctab").hidden = true;
}
function collapseSource() { $("#sources").classList.remove("open"); $("#srctab").hidden = false; }
function initResizer() {
  const panel = $("#sources"), grip = $("#resizer"), host = $("#consumer");
  const setW = (px) => { const max = host.getBoundingClientRect().width * 0.72; panel.style.setProperty("--srcw", Math.max(240, Math.min(max, px)) + "px"); };
  grip.addEventListener("pointerdown", (e) => { grip.setPointerCapture(e.pointerId); e.preventDefault(); });
  grip.addEventListener("pointermove", (e) => { if (grip.hasPointerCapture(e.pointerId)) setW(host.getBoundingClientRect().right - e.clientX); });
  grip.addEventListener("dblclick", () => panel.style.removeProperty("--srcw"));
  grip.addEventListener("keydown", (e) => {
    const w = panel.getBoundingClientRect().width;
    if (e.key === "ArrowLeft") { setW(w + 32); e.preventDefault(); }
    if (e.key === "ArrowRight") { setW(w - 32); e.preventDefault(); }
  });
}

function mkCite(id, title, loc, level, text, phrase, showLevel) {
  const i = text.indexOf(phrase);
  return { id, document_title: title, location_label: loc, text, level: showLevel ? level : undefined, verified: true, highlight: { start: i, end: i + phrase.length } };
}
function mockReply(question, level) {
  const q = question.toLowerCase(), sup = level === "super";
  const none = { answer: "I couldn't find this in the documents you can access.", citations: [], withheld: false, actions: ["add_knowledge"] };
  if (mockCtx.topic === "cloud" && /expire/.test(q) && !/licen/.test(q)) {
    if (!sup) return none;
    return { answer: "The database tier's hosting contract runs to 31 March 2028 [1].", withheld: false, actions: [],
      citations: [mkCite(1, "Data tier contract (sample).pdf", "Page 4", "super", "Data tier contract. The hosting term for the database tier ends on 31 March 2028 and renews only by written notice.", "31 March 2028", true)] };
  }
  if (/password|billing/.test(q)) return none;
  if (/cloud|structure/.test(q)) {
    mockCtx.topic = "cloud";
    const c1 = mkCite(1, "Hosting overview (sample).docx", "Section 2", "employee", "Hosting overview. The public web front end is hosted on Service A, behind the standard firewall.", "hosted on Service A", sup);
    const c2 = mkCite(2, "Data tier notes (sample).md", "Lines 12-14", "employee", "Data tier notes. The primary database tier runs on Service B in a private network.", "runs on Service B", sup);
    if (!sup) return { answer: "Here is what I can show from the documents available to you:\n- The web front end is hosted on Service A [1]\n- The database tier runs on Service B [2]", citations: [c1, c2], withheld: true, actions: [] };
    const c3 = mkCite(3, "Backup runbook (sample).pdf", "Page 2", "super", "Backup runbook. Nightly backups are stored in Service C and kept for 35 days.", "stored in Service C", true);
    const c4 = mkCite(4, "Identity design (sample).pdf", "Page 1", "super", "Identity design. Sign-in and staff accounts are handled by Service D.", "handled by Service D", true);
    return { answer: "Here is the structure, from sign-in to storage:\n- Identity is handled by Service D [4]\n- The web front end is hosted on Service A [1]\n- The database tier runs on Service B [2]\n- Backups are stored in Service C [3]",
      citations: [c1, c2, c3, c4], withheld: false, actions: ["diagram"],
      diagram: { nodes: [{ label: "Identity · Service D", cite: 4 }, { label: "Web front end · Service A", cite: 1 }, { label: "Database · Service B", cite: 2 }, { label: "Backups · Service C", cite: 3 }] } };
  }
  if (/own|who/.test(q) && /renewal/.test(q)) {
    const vendor = (id) => mkCite(id, "Vendor summary, September (sample).docx", "Paragraph 2", "employee", "Vendor summary (September). Procurement now owns the renewal process and all vendor contact.", "Procurement now owns the renewal process", sup);
    if (!sup) return { answer: "The Procurement team owns the renewal process, per the vendor summary dated September [1].", citations: [vendor(1)], withheld: true, actions: [] };
    const check = mkCite(1, "Renewal checklist, March (sample).docx", "Paragraph 1", "super", "Renewal checklist (March). Owner: Facilities team. Facilities coordinates the renewal each year.", "Owner: Facilities team", true);
    return { answer: "Sources disagree on who owns the renewal process.\n- Facilities team, per the renewal checklist dated March [1]\n- Procurement team, per the vendor summary dated September [2]\nThe September document is newer, so it is more likely current, but I can't confirm that.",
      citations: [check, vendor(2)], withheld: false, actions: ["correct"] };
  }
  if (/licen|expire/.test(q)) {
    mockCtx.topic = "license";
    const c1 = mkCite(1, "Vendor agreement (sample).pdf", "Page 6", "employee", "Vendor agreement — Section 4.2. The software license for the reporting platform remains valid through 30 June 2027 unless renewed in writing.", "30 June 2027", sup);
    if (!sup) return { answer: "The reporting platform license expires on 30 June 2027 [1].", citations: [c1], withheld: true, actions: [] };
    const c2 = mkCite(2, "Renewal checklist (sample).docx", "Paragraph 3", "super", "Renewal checklist. Owner: Facilities team. The platform license expires on 30 June 2027; start renewal 90 days earlier.", "start renewal 90 days earlier", true);
    return { answer: "The reporting platform license expires on 30 June 2027 [1]. Renewal should start 90 days earlier [2].", citations: [c1, c2], withheld: false, actions: [] };
  }
  return none;
}

App.start({
  page: "ask",
  access: (s) => s.ui.can_ask === true,      // and a role that is allowed to ask (the server decides)
  init: async (s) => { settings = s; initAsk(); $("#q").focus(); }
});
