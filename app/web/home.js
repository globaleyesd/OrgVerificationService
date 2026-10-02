"use strict";
/* Launcher page (/). Two plain links; the pages themselves do the access checks. */
App.start({
  page: "home",
  init: async () => {
    document.getElementById("lask").href = App.pageUrl("ask");
    document.getElementById("ladd").href = App.pageUrl("add");
    document.getElementById("lreview").href = App.pageUrl("review");
  }
});
