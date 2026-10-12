// FilingMind: tema clar/fosc, commutador 10-K/10-Q, animacions i índex actiu.
// Les pàgines funcionen igual sense aquest script (es mostren els dos informes).
(function () {
  "use strict";

  var root = document.documentElement;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var systemDark = window.matchMedia("(prefers-color-scheme: dark)");

  // ---------- Tema clar/fosc ----------
  function currentTheme() {
    return root.getAttribute("data-theme") || (systemDark.matches ? "dark" : "light");
  }

  function paintButton(button) {
    var theme = currentTheme();
    var other = theme === "dark" ? "light" : "dark";
    button.querySelector(".theme__label").textContent = theme === "dark" ? "Dark" : "Light";
    button.setAttribute("aria-label", "Switch to " + other + " mode");
    button.title = "Switch to " + other + " mode";
  }

  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    try { localStorage.setItem("fm-theme", theme); } catch (e) { /* mode privat */ }
  }

  var themeButton = document.querySelector(".theme");
  if (themeButton) {
    themeButton.hidden = false;
    paintButton(themeButton);
    systemDark.addEventListener && systemDark.addEventListener("change", function () { paintButton(themeButton); });
    themeButton.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      var swap = function () { applyTheme(next); paintButton(themeButton); };
      if (reduced) return swap();
      if (!document.startViewTransition) {
        // Sense View Transitions: un fos suau dels colors.
        root.classList.add("theme-fade");
        swap();
        setTimeout(function () { root.classList.remove("theme-fade"); }, 450);
        return;
      }
      // Cercle que s'expandeix des del botó.
      var box = themeButton.getBoundingClientRect();
      var x = box.left + box.width / 2;
      var y = box.top + box.height / 2;
      var r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
      root.style.setProperty("--wipe-x", x + "px");
      root.style.setProperty("--wipe-y", y + "px");
      root.style.setProperty("--wipe-r", r + "px");
      root.classList.add("theme-wipe");
      var transition = document.startViewTransition(swap);
      transition.finished.then(function () { root.classList.remove("theme-wipe"); });
    });
  }

  // ---------- Aparicions en fer scroll ----------
  var revealObserver = null;
  if ("IntersectionObserver" in window && !reduced) {
    revealObserver = new IntersectionObserver(function (entries) {
      var batch = 0;
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.style.setProperty("--d", Math.min(batch++ * 0.06, 0.4) + "s");
        entry.target.classList.add("is-in");
        revealObserver.unobserve(entry.target);
        if (entry.target.matches(".cal-wrap")) {
          var cal = entry.target.querySelector(".cal");
          if (cal) cal.classList.add("is-in");
        }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    document.querySelectorAll(".reveal").forEach(function (el) { revealObserver.observe(el); });
  } else {
    document.querySelectorAll(".reveal, .cal").forEach(function (el) { el.classList.add("is-in"); });
  }

  // Comptadors de l'inici (de 0 fins al valor real).
  document.querySelectorAll("[data-count]").forEach(function (el) {
    var target = parseInt(el.getAttribute("data-count"), 10);
    if (reduced || !target) return;
    var start = null;
    var duration = 900 + Math.min(target, 600);
    el.textContent = "0";
    function step(now) {
      if (start === null) start = now;
      var t = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = String(Math.round(target * eased));
      if (t < 1) requestAnimationFrame(step);
    }
    setTimeout(function () { requestAnimationFrame(step); }, 250);
  });

  // ---------- Commutador d'informes ----------
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".switch [role=tab]"));
  var filings = Array.prototype.slice.call(document.querySelectorAll("article.filing"));

  function show(id, updateHash) {
    if (!document.getElementById(id)) return;
    filings.forEach(function (el) {
      var on = el.id === id;
      el.hidden = !on;
      el.classList.remove("is-shown");
      if (on) {
        void el.offsetWidth; // Reinicia les animacions d'entrada.
        el.classList.add("is-shown");
        el.querySelectorAll(".reveal").forEach(function (r) {
          if (revealObserver) { r.classList.remove("is-in"); revealObserver.observe(r); }
        });
      }
    });
    tabs.forEach(function (tab) {
      var on = tab.getAttribute("data-target") === id;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
    });
    if (updateHash && history.replaceState) history.replaceState(null, "", "#" + id);
    observeChapters();
    updateProgress();
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
  } else {
    filings.forEach(function (el) { el.classList.add("is-shown"); });
  }

  // ---------- Files clicables del llibre ----------
  document.querySelectorAll(".ledger tr[data-href]").forEach(function (row) {
    row.addEventListener("click", function (event) {
      if (event.target.closest("a") || (window.getSelection && String(window.getSelection()))) return;
      location.href = row.getAttribute("data-href");
    });
  });

  // ---------- Barra de lectura ----------
  var bar = document.querySelector(".progress");
  var ticking = false;
  function updateProgress() {
    if (!bar) return;
    var article = filings.filter(function (el) { return !el.hidden; })[0];
    if (!article) { bar.style.setProperty("--read", 0); return; }
    var box = article.getBoundingClientRect();
    var total = box.height - innerHeight * 0.6;
    var read = total > 0 ? Math.min(Math.max(-box.top / total, 0), 1) : 0;
    bar.style.setProperty("--read", read.toFixed(4));
  }
  if (bar && filings.length) {
    window.addEventListener("scroll", function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () { ticking = false; updateProgress(); });
    }, { passive: true });
    window.addEventListener("resize", updateProgress);
    updateProgress();
  }

  // ---------- Índex lateral: apartat que s'està llegint ----------
  var chapterObserver = null;
  function observeChapters() {
    if (!("IntersectionObserver" in window)) return;
    if (chapterObserver) chapterObserver.disconnect();
    var visible = filings.filter(function (el) { return !el.hidden; })[0];
    if (!visible) return;
    var links = {};
    visible.querySelectorAll(".toc a").forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    chapterObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting || !links[entry.target.id]) return;
        Object.keys(links).forEach(function (id) { links[id].classList.toggle("is-active", id === entry.target.id); });
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    visible.querySelectorAll(".chapter").forEach(function (el) { chapterObserver.observe(el); });
  }
  if (!tabs.length) observeChapters();
})();
