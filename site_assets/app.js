// FilingMind: commutador 10-K/10-Q, files clicables i índex actiu.
// Les pàgines funcionen igual sense aquest script (es mostren els dos informes).
(function () {
  "use strict";

  // Commutador d'informes de la pàgina d'empresa.
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".switch [role=tab]"));
  var filings = Array.prototype.slice.call(document.querySelectorAll("article.filing"));

  function show(id, updateHash) {
    if (!document.getElementById(id)) return;
    filings.forEach(function (el) { el.hidden = el.id !== id; });
    tabs.forEach(function (tab) {
      var on = tab.getAttribute("data-target") === id;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
    });
    if (updateHash && history.replaceState) history.replaceState(null, "", "#" + id);
    observeChapters();
  }

  if (tabs.length) {
    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () { show(tab.getAttribute("data-target"), true); });
      tab.addEventListener("keydown", function (event) {
        var step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        var next = tabs[(i + step + tabs.length) % tabs.length];
        next.focus();
        next.click();
      });
    });
    var hash = decodeURIComponent(location.hash.slice(1));
    var target = hash && document.getElementById(hash);
    var owner = target && (target.matches("article.filing") ? target : target.closest("article.filing"));
    var fallback = document.querySelector("article.filing[data-default]") || filings[0];
    show((owner || fallback).id, false);
    if (target && target !== owner) target.scrollIntoView();
  }

  // Files del llibre de la cartera: tota la fila porta a la pàgina de l'empresa.
  document.querySelectorAll(".ledger tr[data-href]").forEach(function (row) {
    row.addEventListener("click", function (event) {
      if (event.target.closest("a") || (window.getSelection && String(window.getSelection()))) return;
      location.href = row.getAttribute("data-href");
    });
  });

  // Índex lateral: marca l'apartat que s'està llegint.
  var observer = null;
  function observeChapters() {
    if (!("IntersectionObserver" in window)) return;
    if (observer) observer.disconnect();
    var visible = filings.filter(function (el) { return !el.hidden; })[0];
    if (!visible) return;
    var links = {};
    visible.querySelectorAll(".toc a").forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting || !links[entry.target.id]) return;
        Object.keys(links).forEach(function (id) { links[id].classList.toggle("is-active", id === entry.target.id); });
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    visible.querySelectorAll(".chapter").forEach(function (el) { observer.observe(el); });
  }
  if (!tabs.length) observeChapters();
})();
