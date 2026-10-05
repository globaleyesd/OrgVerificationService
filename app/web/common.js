"use strict";
/* ---------------------------------------------------------------
   Shared by every page: header (who is signed in), offline screen. Signing in, switching user
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
</header>
<div id="fatal" role="alert"></div>
<section id="offline" aria-live="polite">
  <div class="card">
    <h2>Service offline</h2>
    <p id="offmsg">The service is switched off. Enter the switch password to turn it on.</p>
  </div>
</section>`;
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
    if (j && j.offline) {
      // On AWS, CloudFront answers "offline" for ANY request the server didn't answer (502/504), so one failed or slow
      // request looked like the whole site going away. The offline screen is only for a server that is really off;
      // otherwise this one request failed, the page stays, and the caller shows the message.
      if (await serverRunning()) throw new Error("The server didn't answer this request in time. Please try again.");
      showOffline(offMessage()); throw new Error("offline");
    }
  }
  return r;
}
async function serverRunning() {
  try {   // AWS: the status function always answers, and says whether the server is running
    const c = await fetch("/control/status");
    if (c.ok) { const s = await c.json(); return s.state === "running"; }
  } catch (e) {}
  try { const s = await fetch(API + "/service/status"); return s.ok && (await s.json()).on === true; } catch (e) { return false; }
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
  $("#title").href = pageUrl("home");
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
    if (!settings.user && opts.page !== "signin") { goSignIn(); return; }   // nothing is shown before signing in
    if (opts.access && !opts.access(settings)) {
      document.body.classList.add("noaccess");
      fatal("You don't have access to this page. Please ask an administrator.");
      return;
    }
    if (!started) { started = true; await opts.init(settings); }
  } catch (e) { if (e.message !== "offline") fatal("Something went wrong loading the page."); }
}

/* ===== Sample mode only (?mock=1). Fictional numbers. ===== */
function mockSettings() {
  const u = mockUser();
  return { project_name: "kb-verifier", allowed_extensions: ["pdf", "docx", "txt", "csv"], max_file_mb: 25, speech_mode: "browser",
           user: u, can_review: !!u && u.role === "super", ui: { demo_mode: true, demo_login: true, can_ask: !!u, allow_mock: true },
           demo_users: Object.values(MOCK_USERS) };
}
return { $, api, sleep, cap, mockLevel, isMock: () => mock, pageUrl, start, demoUsers: (s) => demoUsers(s), demoLogin: postDemoLogin, login: postLogin, signOut,
         signInByName };
})();
