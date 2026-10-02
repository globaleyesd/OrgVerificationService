"use strict";
/* Sign-in screen (/signin). In demo mode: type the name of a demo account (capitals don't matter; suggestions
   appear as you type) and you become that account, then go back to the page you came from (?next=).
   Otherwise: username and password. The server decides everything; this page only asks it. */
function destination() {   // only ever return to one of our own pages
  const next = new URLSearchParams(location.search).get("next") || "";
  const known = { ask: 1, add: 1, review: 1, home: 1 };
  return known[next] ? App.pageUrl(next) : App.pageUrl("home");
}
function render(settings) {
  const me = settings.user, el = (id) => document.getElementById(id);
  el("spnow").textContent = me ? "Signed in as " + me.display_name + " (" + App.cap(me.role) + "). Type another name to switch."
                               : "Not signed in.";
  el("spout").hidden = !me;
  el("spback").href = destination();
  const demo = App.isMock() || (settings.ui && settings.ui.demo_login === true);
  el("sppicker").hidden = !demo; el("spform").hidden = demo;
  if (!demo) { el("spuser").focus(); return; }
  el("spname").focus();
}
App.start({
  page: "signin",
  init: async (settings) => {
    const el = (id) => document.getElementById(id);
    render(settings);
    el("sppicker").addEventListener("submit", async (e) => {
      e.preventDefault(); el("sperr").textContent = ""; el("spgo").disabled = true;
      try {
        const res = await App.signInByName(el("spname").value, App.demoUsers(settings));
        if (!res.ok) { el("sperr").textContent = res.msg; return; }
        location.href = destination();
      } catch (err) { el("sperr").textContent = "Can't reach the server."; }
      finally { el("spgo").disabled = false; }
    });
    el("spform").addEventListener("submit", async (e) => {
      e.preventDefault(); el("spbtn").disabled = true;
      try {
        const res = await App.login(el("spuser").value, el("sppw").value); el("sppw").value = "";
        if (!res.ok) { el("sperr").textContent = res.msg; return; }
        location.href = destination();
      } catch (err) { el("sperr").textContent = "Can't reach the server."; }
      finally { el("spbtn").disabled = false; }
    });
    el("spout").addEventListener("click", async () => { await App.signOut(); location.reload(); });
  }
});
