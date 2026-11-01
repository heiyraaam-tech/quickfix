(function () {
  "use strict";
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  window.QF = { csrf: csrf };
  function $(s, r) { return (r || document).querySelector(s); }
  function $all(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function post(url, data) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(data || {}) });
  }

  // mobile nav
  var toggle = $(".nav-toggle"), nav = $("#site-nav");
  if (toggle && nav) toggle.addEventListener("click", function () {
    var open = nav.getAttribute("data-open") === "true";
    nav.setAttribute("data-open", String(!open)); toggle.setAttribute("aria-expanded", String(!open));
  });
  // account menu
  $all(".menu").forEach(function (m) {
    var b = $("button", m);
    if (!b) return;
    b.addEventListener("click", function (e) { e.stopPropagation(); var o = m.getAttribute("data-open") === "true"; m.setAttribute("data-open", String(!o)); b.setAttribute("aria-expanded", String(!o)); });
    document.addEventListener("click", function () { m.setAttribute("data-open", "false"); b.setAttribute("aria-expanded", "false"); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") m.setAttribute("data-open", "false"); });
  });

  // toasts
  var box = $("#toasts");
  function toast(msg, kind) {
    if (!box) return;
    var t = document.createElement("div"); t.className = "toast " + (kind || "info"); t.setAttribute("role", kind === "error" ? "alert" : "status");
    var s = document.createElement("span"); s.textContent = msg;
    var x = document.createElement("button"); x.type = "button"; x.setAttribute("aria-label", "Dismiss"); x.textContent = "×";
    x.addEventListener("click", function () { t.remove(); });
    t.appendChild(s); t.appendChild(x); box.appendChild(t);
    if (kind !== "error") setTimeout(function () { t.remove(); }, 7000);
  }
  window.QF.toast = toast;
  $all(".toast").forEach(function (t) {
    var x = $("button", t); if (x) x.addEventListener("click", function () { t.remove(); });
    if (!t.classList.contains("error")) setTimeout(function () { t.remove(); }, 7000);
  });

  // confirmation dialog
  var dlg = $("#confirm-dialog");
  if (dlg && dlg.showModal) {
    var pending = null;
    document.addEventListener("submit", function (e) {
      var f = e.target; var msg = f.getAttribute && f.getAttribute("data-confirm");
      if (!msg || f.__confirmed) return;
      e.preventDefault(); pending = f; $("#confirm-text", dlg).textContent = msg; dlg.showModal(); $("#confirm-no", dlg).focus();
    }, true);
    $("#confirm-yes", dlg).addEventListener("click", function () { var f = pending; dlg.close(); if (f) { f.__confirmed = true; f.requestSubmit ? f.requestSubmit() : f.submit(); } });
    $("#confirm-no", dlg).addEventListener("click", function () { dlg.close(); pending = null; });
  }

  // avoid double submits
  document.addEventListener("submit", function (e) {
    if (e.defaultPrevented) return;
    $all('button[type="submit"],button:not([type])', e.target).forEach(function (b) { setTimeout(function () { b.setAttribute("aria-busy", "true"); b.disabled = true; }, 0); });
  });

  // locality selector (stored in the server session)
  $all("[data-locality-select]").forEach(function (sel) {
    sel.addEventListener("change", function () {
      post("/api/locality", { id: parseInt(sel.value, 10) || 0 }).then(function (r) { return r.json(); }).then(function (d) {
        toast(d.name ? "Showing services for " + d.name : "Locality cleared", "info");
        document.dispatchEvent(new CustomEvent("qf:locality", { detail: sel.value }));
      });
    });
  });

  // service search suggestions
  $all("[data-search]").forEach(function (input) {
    var list = $("#" + input.getAttribute("data-search")), timer;
    if (!list) return;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2) { list.setAttribute("data-open", "false"); return; }
      timer = setTimeout(function () {
        fetch("/api/search?q=" + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (d) {
          list.textContent = "";
          if (!d.results.length) { var p = document.createElement("div"); p.className = "muted small"; p.style.padding = "10px 12px"; p.textContent = "No match. Press Search to browse everything."; list.appendChild(p); }
          d.results.forEach(function (r) {
            var a = document.createElement("a"); a.href = r.url;
            var n = document.createElement("span"); n.textContent = r.name; var c = document.createElement("span"); c.className = "muted small"; c.textContent = r.category;
            a.appendChild(n); a.appendChild(c); list.appendChild(a);
          });
          list.setAttribute("data-open", "true");
        });
      }, 200);
    });
    document.addEventListener("click", function (e) { if (!list.contains(e.target) && e.target !== input) list.setAttribute("data-open", "false"); });
  });

  // unread notification polling (30s)
  if (document.body.getAttribute("data-auth") === "1") {
    var poll = function () {
      fetch("/api/notifications/count", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        if (!d) return;
        $all("[data-unread]").forEach(function (el) { el.textContent = d.unread; el.hidden = d.unread === 0; });
      }).catch(function () {});
    };
    setInterval(poll, 30000);
  }
})();
