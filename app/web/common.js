"use strict";
/* ---------------------------------------------------------------
   Shared by every page: header (who is signed in), offline screen, cost dashboard. Signing in, switching user
   and signing out happen only on the sign-in screen (/signin).

   SECURITY NOTES
   - The fixed page chrome below is static text written in this file. Anything that comes from the
     server or from a document is inserted with textContent / text nodes only, never innerHTML.
   - Showing or hiding things here is cosmetic. The server decides who may do what.
   - Passwords are only sent in a request body, and the fields are cleared afterwards.
   - No inline scripts or styles: the content-security policy forbids them.
   --------------------------------------------------------------- */
const App = (() => {
const $ = (s) => document.querySelector(s);
const API = "/api";
const params = new URLSearchParams(location.search);
const wantMock = params.get("mock") === "1";
let mock = false, started = false, settings = null, opts = null;
let controlMode = false;   // AWS: /control starts and stops the server; this page then talks to it for on/off
let mockOn = params.get("state") !== "off";
/* Sample mode only: the demo accounts. Any non-empty password works. */
const MOCK_USERS = {
  eileen: { username: "eileen", display_name: "Eileen", role: "super" },
  allminuseileen: { username: "allminuseileen", display_name: "AllMinusEileen", role: "employee" },
  mark: { username: "mark", display_name: "Mark", role: "super" },
  allminusmark: { username: "allminusmark", display_name: "AllMinusMark", role: "employee" }
};
const mockUser = () => MOCK_USERS[sessionStorage.getItem("mockUser")] || null;
const mockLevel = () => (mockUser() || {}).role || "employee";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const cap = (s) => { s = String(s || "").replace(/_/g, " "); return s.charAt(0).toUpperCase() + s.slice(1); };

/* Address of another page. Works on the server (/ask) and when a page is opened as a plain file. */
function pageUrl(name, next) {
  const file = location.protocol === "file:";
  const path = { home: file ? "index.html" : "/", ask: file ? "ask.html" : "/ask", add: file ? "add.html" : "/add", review: file ? "review.html" : "/review",
                 signin: file ? "signin.html" : "/signin" }[name];
  const q = [];
  if (wantMock) { q.push("mock=1"); if (params.get("as")) q.push("as=" + encodeURIComponent(params.get("as"))); }
  if (next) q.push("next=" + encodeURIComponent(next));   // where the sign-in screen returns to
  return path + (q.length ? "?" + q.join("&") : "");
}

const CHROME_TOP = `
<header>
  <h1><a id="title" href="#">Knowledge Verifier</a></h1>
  <span id="who" hidden></span>
  <button type="button" class="ghost" id="costbtn" hidden>Costs</button>
</header>
<div id="fatal" role="alert"></div>
<section id="offline" aria-live="polite">
  <div class="card">
    <h2>Service offline</h2>
    <p id="offmsg">The service is switched off. Enter the switch password to turn it on.</p>
  </div>
</section>`;
const CHROME_DIALOGS = `
<dialog id="costdlg" class="wide" aria-labelledby="costt">
  <div class="dlghead"><h2 id="costt">Costs</h2><button type="button" class="ghost" id="costclose">Close</button></div>
  <div class="rangebar" role="group" aria-label="Time period">
    <div class="field grow">
      <label for="crange">Period: <strong id="crangeout">Last 30 days</strong></label>
      <input type="range" id="crange" min="0" max="7" step="1" value="3" aria-describedby="crangeout">
      <div class="ticks" aria-hidden="true"><span>1d</span><span>7d</span><span>14d</span><span>30d</span><span>60d</span><span>90d</span><span>6m</span><span>1y</span></div>
    </div>
    <div class="custom">
      <div class="field"><label for="cfrom">From</label><input id="cfrom" type="date"></div>
      <div class="field"><label for="cto">To</label><input id="cto" type="date"></div>
      <button type="button" class="ghost" id="capply">Apply dates</button>
    </div>
  </div>
  <p class="err" id="costerr" role="alert"></p>
  <div id="costdash" aria-live="polite"></div>
</dialog>`;

/* ---------- branding (remembered in this browser so the offline screen is branded even when the server is stopped) ---------- */
function applyBranding(b) {
  if (!b) return;
  try { localStorage.setItem("branding", JSON.stringify(b)); } catch (e) {}
  if (b.app_name) { document.title = b.app_name; $("#title").textContent = b.app_name; }
  const map = { accent: "--accent", accent_text: "--accent-text", background: "--bg", surface: "--panel",
                text: "--text", muted: "--muted", line: "--line", highlight: "--mark" };
  if (b.theme) for (const k in map) {
    if (/^#[0-9a-fA-F]{6}$/.test(b.theme[k] || "")) document.documentElement.style.setProperty(map[k], b.theme[k]);
  }
}

/* ---------- talking to the server ---------- */
async function api(path, o) {
  const r = await fetch(API + path, o);
  if (r.status === 401) { goSignIn(); throw new Error("offline"); }   // signed out or session expired: already handled
  if (r.status === 503) {
    let j = null; try { j = await r.clone().json(); } catch (e) {}
    if (j && j.offline) { showOffline(offMessage()); throw new Error("offline"); }
  }
  return r;
}
async function getStatus() {
  try {
    const r = await fetch(API + "/service/status");
    if (!r.ok) throw new Error("status " + r.status);
    return await r.json();
  } catch (e) {
    if (wantMock) return { on: mockOn, allow_mock: true, branding: null };   // opened as a plain file: sample mode only
    try {   // AWS: the server may simply be stopped. The control function is always reachable.
      const c = await fetch("/control/status");
      if (c.ok) { controlMode = true; const j = await c.json(); return { on: false, state: j.state, allow_mock: false, branding: null }; }
    } catch (e2) {}
    return null;
  }
}
function offMessage(state) {
  if (!controlMode) return "The service is switched off. Turn it on from the Control Center, or on this machine with: python -m app.cli service on";
  if (state === "pending") return "The server is starting. This takes about a minute.";
  if (state === "stopping") return "The server is shutting down.";
  if (state === "zero") return "This project is at zero to save cost: no server is running. Bring it back from the Control Center (about 15 minutes).";
  return "The service is stopped to save cost. Turn it on from the Control Center. Starting takes about a minute.";
}
function fatal(msg) { const f = $("#fatal"); f.textContent = msg; f.classList.add("show"); }

/* ---------- offline screen ----------
   Turning a project on or off is done from the Control Center (AWS) or with "python -m app.cli service on|off"
   (local). This page only says that it is off and, while a server starts, waits for it. */
function showOffline(msg) {
  document.body.classList.add("offline");
  $("#offmsg").textContent = msg;
}
async function waitForServer() {   // AWS: a server takes about a minute to boot after the Control Center starts it
  for (let i = 0; i < 40; i++) {
    $("#offmsg").textContent = "The server is starting. This takes about a minute. (" + i * 5 + " s)";
    await sleep(5000);
    try {
      const r = await fetch(API + "/service/status");
      if (r.ok && (await r.json()).on) { document.body.classList.remove("offline"); settings = null; await enter(); return; }
    } catch (e) {}
  }
  showOffline("It is taking longer than expected. Reload this page in a minute.");
}

/* ---------- sign in / out ---------- */
function showUser(u) {   // who is signed in, for reference only: switching user and signing out happen on /signin
  $("#who").hidden = !u;
  if (u) $("#who").textContent = u.display_name + " · " + cap(u.role);
}
const demoUsers = (s) => (mock ? Object.values(MOCK_USERS) : (s && s.demo_users) || []);
/* Demo mode: type the account's name. Capitals and spaces around it don't matter; the server checks it the same
   way. Suggestions come from the server's list of demo accounts. */
function findDemoUser(typed, users) {
  const k = String(typed || "").trim().toLowerCase();
  return users.find((u) => u.username === k || u.display_name.toLowerCase() === k) || null;
}
async function signInByName(typed, users) {   // shared with the sign-in screen
  if (!String(typed || "").trim()) return { ok: false, msg: "Type a name." };
  const u = findDemoUser(typed, users);
  if (!u) return { ok: false, msg: "No account with that name." };   // never lists the accounts
  return postDemoLogin(u.username);
}
async function signOut() {
  try { if (mock) sessionStorage.removeItem("mockUser"); else await fetch(API + "/auth/logout", { method: "POST" }); } catch (e) {}
}
async function loginRequest(path, payload) {
  const r = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (r.ok) return { ok: true };
  let d = ""; try { d = (await r.clone().json()).detail; } catch (e) {}
  return { ok: false, msg: r.status === 401 ? "Wrong username or password." : (d || "Something went wrong (" + r.status + ").") };
}
async function postLogin(username, password) {
  if (mock) {
    await sleep(300);
    const u = MOCK_USERS[String(username).trim().toLowerCase()];
    if (!u || !password) return { ok: false, msg: "Wrong username or password." };
    sessionStorage.setItem("mockUser", u.username); return { ok: true };
  }
  return loginRequest("/auth/login", { username, password });
}
async function postDemoLogin(username) {   // demo only: the server refuses this unless demo mode is on
  if (mock) {
    await sleep(250);
    const u = MOCK_USERS[String(username).trim().toLowerCase()];
    if (!u) return { ok: false, msg: "Unknown demo account." };
    sessionStorage.setItem("mockUser", u.username); return { ok: true };
  }
  return loginRequest("/auth/demo-login", { username });
}
/* Every page needs a signed-in user. Without one, and whenever the server answers 401, the page goes to the sign-in
   screen, which brings you back here afterwards. */
function goSignIn() {
  location.replace(pageUrl("signin", opts && opts.page !== "signin" ? opts.page : null));
}

/* ---------- start-up, shared by every page ---------- */
async function start(options) {
  opts = options;
  document.body.insertAdjacentHTML("afterbegin", CHROME_TOP);
  document.body.insertAdjacentHTML("beforeend", CHROME_DIALOGS);
  $("#title").href = pageUrl("home");
  initCosts();
  try { applyBranding(JSON.parse(localStorage.getItem("branding") || "null")); } catch (e) {}
  const st = await getStatus();
  if (!st) { showOffline("Service offline. The server can't be reached right now."); return; }
  mock = wantMock && st.allow_mock === true;
  applyBranding(st.branding);
  if (st.mode === "aws") controlMode = true;
  if (!(mock ? mockOn : st.on)) {
    showOffline(offMessage(st.state));
    if (controlMode && st.state === "pending") waitForServer();   // it is already starting: just wait for it
    return;
  }
  await enter();
}
async function enter() {
  try {
    if (!settings) {
      if (mock) settings = mockSettings();
      else {
        const r = await api("/settings/public");
        if (!r.ok) throw new Error("settings " + r.status);
        settings = await r.json();
      }
    }
    applyBranding(settings.branding);
    showUser(settings.user);
    $("#costbtn").hidden = !settings.can_view_costs;
    if (!settings.user && opts.page !== "signin") { goSignIn(); return; }   // nothing is shown before signing in
    if (opts.access && !opts.access(settings)) {
      document.body.classList.add("noaccess");
      fatal("You don't have access to this page. Please ask an administrator.");
      return;
    }
    if (!started) { started = true; await opts.init(settings); }
  } catch (e) { if (e.message !== "offline") fatal("Something went wrong loading the page."); }
}

/* ---------- cost dashboard ----------
   Which services cost money in the chosen period, what each costs, what is running right now, and the cost day by
   day. Period: a slider over fixed stops (1 day .. 1 year), or exact dates. Colours follow the service, never its
   rank: each service keeps its slot whatever else is shown. Every number in the chart is also in the table. */
const STOPS = [1, 7, 14, 30, 60, 90, 182, 365];
const STOP_NAMES = ["Today", "Last 7 days", "Last 14 days", "Last 30 days", "Last 60 days", "Last 90 days", "Last 6 months", "Last 12 months"];
const SERIES_SLOT = { server: 1, hosted_ai: 2, storage: 3, disk: 4, public_ip: 5, local_ai: 6 };
const STATE = { running: [String.fromCharCode(0x25CF), "Running"], idle: [String.fromCharCode(0x25D0), "Idle"], stopped: [String.fromCharCode(0x25A0), "Stopped"], aws_only: [String.fromCharCode(0x25CB), "AWS only"], off: [String.fromCharCode(0x25CB), "Not in use"] };   // filled, half, square, hollow
const pad = (n) => String(n).padStart(2, "0");
const dateInput = (d) => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
const money = (v) => "$" + (v >= 1000 ? Math.round(v).toLocaleString() : v === 0 || v >= 0.01 ? v.toFixed(2) : v.toFixed(4));   // cents; 4 places only below a cent
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const SVGNS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs) => { const e = document.createElementNS(SVGNS, tag); Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v)); return e; };
let costReq = 0, costTimer = null;
function initCosts() {
  const range = $("#crange");
  $("#costbtn").addEventListener("click", () => { $("#costerr").textContent = ""; $("#costdlg").showModal(); loadCosts({ days: STOPS[+range.value] }); });
  $("#costclose").addEventListener("click", () => $("#costdlg").close());
  const today = dateInput(new Date()); $("#cfrom").max = today; $("#cto").max = today;
  range.addEventListener("input", () => {   // move the slider: update the label now, fetch once it settles
    $("#crangeout").textContent = STOP_NAMES[+range.value]; range.classList.remove("unset");
    clearTimeout(costTimer); costTimer = setTimeout(() => loadCosts({ days: STOPS[+range.value] }), 250);
  });
  $("#capply").addEventListener("click", () => {
    const f = $("#cfrom").value, to = $("#cto").value;
    if (!f || !to) { $("#costerr").textContent = "Pick both dates."; return; }
    const a = new Date(f + "T00:00:00"), b = new Date(to + "T23:59:59");
    if (b < a) { $("#costerr").textContent = "The end date must be on or after the start date."; return; }
    $("#crangeout").textContent = a.toLocaleDateString() + " – " + b.toLocaleDateString(); range.classList.add("unset");
    loadCosts({ start: a.toISOString(), end: b.toISOString() });
  });
}
async function loadCosts(body) {
  const dash = $("#costdash"), err = $("#costerr"), mine = ++costReq; err.textContent = "";
  if (dash.childNodes.length) dash.classList.add("loading"); else dash.textContent = "Calculating…";   // refetch keeps the frame
  try {
    let data;
    if (App.isMock()) { await sleep(400); data = mockServices(body); }
    else {
      const r = await api("/costs/services", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch (e) {} throw new Error(typeof d === "string" && d ? d : "Something went wrong (" + r.status + ")."); }
      data = await r.json();
    }
    if (mine === costReq) renderDashboard(data);
  } catch (e) { if (mine === costReq) { dash.classList.remove("loading"); if (e.message !== "offline") err.textContent = e.message || "Network error."; } }
}
function renderDashboard(d) {
  const dash = $("#costdash"); dash.textContent = ""; dash.classList.remove("loading");
  const costing = d.services.filter((s) => s.costing), free = d.services.filter((s) => !s.costing);
  const first = d.daily.length ? d.daily[0].date : "", last = d.daily.length ? d.daily[d.daily.length - 1].date : "";

  // headline: one number, then what it covers
  const head = el("section", "costhead");
  head.append(el("div", "kicker", "Estimated cost, " + fmtDay(first) + " – " + fmtDay(last)), el("div", "hero", money(d.total_usd)));
  const sub = el("div", "fine");
  sub.textContent = d.running_now + " service" + (d.running_now === 1 ? "" : "s") + " running now · " + costing.length + " costing at least a cent" +
    (d.tracking_since ? " · counted from " + new Date(d.tracking_since).toLocaleDateString() + ", when this project first recorded usage" : " · no usage recorded yet");
  head.appendChild(sub);
  if (d.mode === "local") head.appendChild(el("p", "note", "Running on this machine: amounts are what this usage would cost on AWS at the rates in config.yaml. Nothing here is billed to you."));
  dash.appendChild(head);

  // services costing a cent or more, each with its own cost, status and share
  const sec = el("section", "svc"); sec.appendChild(el("h3", null, "Services costing money in this period"));
  if (!costing.length) sec.appendChild(el("p", "fine", "Nothing cost a cent in this period."));
  const max = Math.max(...costing.map((s) => s.usd), 0);
  const list = el("ul", "svclist");
  costing.forEach((s) => {
    const li = el("li"), name = el("div", "svcname");
    name.append(el("span", "key s" + (SERIES_SLOT[s.key] || 7)), el("strong", null, s.label), stateBadge(s.state));
    const what = el("div", "fine", s.detail || s.what);
    const bar = el("div", "track"), fill = el("div", "fill s" + (SERIES_SLOT[s.key] || 7));
    fill.style.setProperty("--w", (max ? Math.max(1, 100 * s.usd / max) : 0) + "%"); bar.appendChild(fill);
    const amt = el("div", "amt", money(s.usd)); amt.appendChild(el("small", null, d.total_usd ? Math.round(100 * s.usd / d.total_usd) + "% of total" : ""));
    li.append(name, amt, what, bar); list.appendChild(li);
  });
  sec.appendChild(list);
  if (free.length) {
    const p = el("p", "fine freeline"); p.appendChild(document.createTextNode("Also running, at no cost: "));
    free.forEach((s, i) => { if (i) p.appendChild(document.createTextNode(", ")); p.append(stateBadge(s.state, true), document.createTextNode(" " + s.label)); p.lastChild.title = s.detail; });
    sec.appendChild(p);
  }
  dash.appendChild(sec);

  // cost over time, stacked by service
  if (d.series.length) dash.appendChild(costChart(d));
  dash.appendChild(costTable(d));
  const fine = el("p", "fine", "Estimate from this project's own usage meters, priced from config.yaml. Not included: " + (d.not_included || []).join(", ") + ". AWS's own bill can differ and arrives up to a day late.");
  dash.appendChild(fine);
  (d.warnings || []).forEach((w) => dash.appendChild(el("p", "fine warn", w)));
}
function fmtDay(iso) { return iso ? new Date(iso + "T12:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }) : ""; }
function stateBadge(state, small) {
  const [icon, label] = STATE[state] || STATE.off, b = el("span", "state st-" + state + (small ? " small" : ""));
  b.append(el("span", "ico", icon), document.createTextNode(small ? "" : " " + label)); b.setAttribute("aria-label", label); if (small) b.title = label;
  return b;
}
/* Stacked columns: one per day (one per week past 90 days). Columns cap at 24px, 2px surface gap between columns
   and between segments, 4px rounded top on the top segment only. Hover or arrow keys show every service for that
   day; the values are also in the table below. */
function bucket(d) {
  if (d.daily.length <= 90) return d.daily.map((x) => ({ label: fmtDay(x.date), from: x.date, usd: x.usd, total: x.total_usd }));
  const out = [];
  for (let i = 0; i < d.daily.length; i += 7) {
    const wk = d.daily.slice(i, i + 7), usd = {};
    wk.forEach((x) => Object.entries(x.usd).forEach(([k, v]) => { usd[k] = (usd[k] || 0) + v; }));
    out.push({ label: "Week of " + fmtDay(wk[0].date), from: wk[0].date, usd, total: wk.reduce((s, x) => s + x.total_usd, 0) });
  }
  return out;
}
function niceMax(v) { if (v <= 0) return 0.01; const p = Math.pow(10, Math.floor(Math.log10(v))); return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v); }
function costChart(d) {
  const wrap = el("section", "chart"), cols = bucket(d), weekly = d.daily.length > 90;
  wrap.appendChild(el("h3", null, "Cost per " + (weekly ? "week" : "day") + " by service"));
  const legend = el("div", "legend");
  d.series.forEach((s) => { const it = el("span", "item"); it.append(el("span", "key s" + (SERIES_SLOT[s.key] || 7)), document.createTextNode(s.label)); legend.appendChild(it); });
  wrap.appendChild(legend);
  const W = 640, H = 210, L = 52, R = 8, T = 10, B = 26, pw = W - L - R, ph = H - T - B;
  const top = niceMax(Math.max(...cols.map((c) => c.total), 0)), y = (v) => T + ph - (v / top) * ph;
  const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, class: "costsvg", role: "img", tabindex: "0",
    "aria-label": "Cost per " + (weekly ? "week" : "day") + " by service, " + cols.length + " " + (weekly ? "weeks" : "days") + ". Use the left and right arrow keys to read each one; all values are also in the table." });
  [0, 0.5, 1].forEach((f) => {   // recessive hairline grid with clean tick labels
    const v = top * f, gy = y(v);
    svg.appendChild(svgEl("line", { x1: L, x2: W - R, y1: gy, y2: gy, class: f ? "grid" : "base" }));
    const tx = svgEl("text", { x: L - 6, y: gy + 4, class: "tick", "text-anchor": "end" }); tx.textContent = money(v); svg.appendChild(tx);
  });
  const slot = pw / cols.length, bw = Math.max(1, Math.min(24, slot - 2));
  const hilite = svgEl("rect", { class: "hover", y: T, height: ph, width: slot, x: -999 }); svg.appendChild(hilite);
  cols.forEach((c, i) => {
    const x = L + i * slot + (slot - bw) / 2; let acc = 0;
    const segs = d.series.filter((s) => c.usd[s.key] > 0);
    segs.forEach((s, j) => {
      const v = c.usd[s.key], y0 = y(acc), y1 = y(acc + v); acc += v;
      const h = Math.max(0, y0 - y1 - (j ? 2 : 0));   // 2px surface gap above every segment but the first
      if (h < 0.5) return;
      const r = j === segs.length - 1 ? Math.min(4, h, bw / 2) : 0, yt = y0 - (j ? 2 : 0) - h;
      svg.appendChild(svgEl("path", { class: "seg s" + (SERIES_SLOT[s.key] || 7),
        d: "M" + x + "," + (yt + h) + "V" + (yt + r) + "Q" + x + "," + yt + " " + (x + r) + "," + yt + "H" + (x + bw - r) + "Q" + (x + bw) + "," + yt + " " + (x + bw) + "," + (yt + r) + "V" + (yt + h) + "Z" }));
    });
  });
  [0, Math.floor((cols.length - 1) / 2), cols.length - 1].filter((v, i, a) => a.indexOf(v) === i).forEach((i) => {
    const tx = svgEl("text", { x: L + (i + 0.5) * slot, y: H - 8, class: "tick", "text-anchor": i === 0 ? "start" : i === cols.length - 1 ? "end" : "middle" });
    tx.textContent = cols[i].label; svg.appendChild(tx);
  });
  const box = el("div", "plot"), tip = el("div", "tip"); tip.hidden = true; box.append(svg, tip); wrap.appendChild(box);
  let cur = -1;
  const show = (i) => {
    cur = Math.max(0, Math.min(cols.length - 1, i)); const c = cols[cur];
    hilite.setAttribute("x", L + cur * slot); tip.textContent = "";
    tip.appendChild(el("div", "tiptitle", c.label));
    tip.appendChild(el("div", "tiptotal", money(c.total)));
    d.series.forEach((s) => {
      const row = el("div", "tiprow"); row.append(el("span", "line s" + (SERIES_SLOT[s.key] || 7)), el("strong", null, money(c.usd[s.key] || 0)), document.createTextNode(" " + s.label));
      tip.appendChild(row);
    });
    tip.hidden = false;
    const frac = (cur + 0.5) / cols.length; tip.classList.toggle("left", frac > 0.6); tip.style.setProperty("--x", (100 * (L + (cur + 0.5) * slot) / W) + "%");
  };
  const hide = () => { tip.hidden = true; hilite.setAttribute("x", -999); };
  svg.addEventListener("pointermove", (e) => { const rect = svg.getBoundingClientRect(), px = (e.clientX - rect.left) * W / rect.width; if (px < L) return hide(); show(Math.floor((px - L) / slot)); });
  svg.addEventListener("pointerleave", hide); svg.addEventListener("blur", hide);
  svg.addEventListener("focus", () => show(cur < 0 ? cols.length - 1 : cur));
  svg.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") { show(cur - 1); e.preventDefault(); } else if (e.key === "ArrowRight") { show(cur + 1); e.preventDefault(); }
    else if (e.key === "Escape" && !tip.hidden) { hide(); e.stopPropagation(); e.preventDefault(); }
  });
  return wrap;
}
function costTable(d) {
  const det = el("details", "tableview"), sum = el("summary", null, "Show as a table"); det.appendChild(sum);
  const keys = d.series.map((s) => s.key), t = el("table", "costs"), hr = el("tr");
  ["Date"].concat(d.series.map((s) => s.label), ["Total"]).forEach((h, i) => hr.appendChild(el("th", i ? "amt" : null, h)));
  t.appendChild(hr);
  const days = d.daily.filter((x) => x.total_usd > 0);
  if (!days.length) { const tr = el("tr"), td = el("td", "fine", "No costs in this period."); td.colSpan = keys.length + 2; tr.appendChild(td); t.appendChild(tr); }
  days.forEach((x) => {
    const tr = el("tr"); tr.appendChild(el("td", null, fmtDay(x.date)));
    keys.forEach((k) => tr.appendChild(el("td", "amt", money(x.usd[k] || 0)))); tr.appendChild(el("td", "amt", money(x.total_usd))); t.appendChild(tr);
  });
  const tot = el("tr", "total"); tot.appendChild(el("td", null, "Total"));
  keys.forEach((k) => tot.appendChild(el("td", "amt", money((d.services.find((s) => s.key === k) || {}).usd || 0)))); tot.appendChild(el("td", "amt", money(d.total_usd)));
  t.appendChild(tot); det.appendChild(el("div", "tablewrap")).appendChild(t);
  return det;
}

/* ===== Sample mode only (?mock=1). Fictional numbers. ===== */
function mockSettings() {
  const u = mockUser();
  return { project_name: "kb-verifier", allowed_extensions: ["pdf", "docx", "txt", "csv"], max_file_mb: 25, speech_mode: "browser",
           user: u, can_view_costs: !!u && u.role === "super", can_review: !!u && u.role === "super", ui: { demo_mode: true, demo_login: true, can_ask: !!u, allow_mock: true },
           demo_users: Object.values(MOCK_USERS) };
}
function mockServices(body) {
  const end = body.end ? new Date(body.end) : new Date(), start = body.start ? new Date(body.start) : new Date(end.getTime() - (body.days - 1) * 864e5);
  const daily = [], tot = { server: 0, hosted_ai: 0, storage: 0, disk: 0, public_ip: 0 };
  for (let d = new Date(start.getFullYear(), start.getMonth(), start.getDate()); d <= end; d.setDate(d.getDate() + 1)) {
    const wd = d.getDay() % 6 !== 0, usd = { server: (wd ? 10 : 3) * 0.0168, hosted_ai: wd ? 0.02 + (d.getDate() % 5) * 0.01 : 0, storage: 0.0003, disk: 0.0526, public_ip: 0.12 };
    Object.keys(usd).forEach((k) => { tot[k] += usd[k]; });
    daily.push({ date: d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"), usd, total_usd: Object.values(usd).reduce((s, v) => s + v, 0) });
  }
  const svc = (key, label, state, detail) => ({ key, label, state, detail, usd: tot[key] || 0, costing: (tot[key] || 0) >= 0.005 });
  const services = [svc("server", "App server", "running", "Answering requests now"), svc("public_ip", "Public IP address", "running", "Allocated; billed every hour"),
    svc("disk", "Server disk", "running", "20 GB"), svc("hosted_ai", "Hosted AI", "running", "Charged per question"), svc("storage", "Document storage", "running", "12 files, 4.2 MB stored"),
    svc("database", "Database", "running", "Included in the server's cost")].sort((a, b) => (b.costing - a.costing) || (b.usd - a.usd));
  return { daily, services, series: ["server", "hosted_ai", "storage", "disk", "public_ip"].filter((k) => tot[k] >= 0.005).map((k) => ({ key: k, label: services.find((s) => s.key === k).label })),
    total_usd: Object.values(tot).reduce((s, v) => s + v, 0), running_now: services.length, tracking_since: daily[0] && daily[0].date + "T00:00:00Z", mode: "aws",
    warnings: [], not_included: ["CloudFront and data transfer", "Request charges (S3, CloudFront)", "Container registry storage", "Taxes"] };
}

return { $, api, sleep, cap, mockLevel, isMock: () => mock, pageUrl, start, demoUsers: (s) => demoUsers(s), demoLogin: postDemoLogin, login: postLogin, signOut,
         signInByName };
})();
