(function () {
  "use strict";
  var form = document.getElementById("booking-form");
  if (!form) return;
  var $ = function (s, r) { return (r || form).querySelector(s); };
  var $all = function (s, r) { return Array.prototype.slice.call((r || form).querySelectorAll(s)); };
  var summary = document.getElementById("summary"), providers = document.getElementById("providers"), alts = document.getElementById("alternatives");
  var service = form.getAttribute("data-service"), houseHelp = form.getAttribute("data-house-help") === "1";
  var inr = function (n) { return "₹" + Number(n).toLocaleString("en-IN"); };
  var timer, seq = 0;

  // show/hide sections by mode / address choice
  function toggles() {
    var mode = ($("input[name=mode]:checked") || {}).value || "once";
    $all("[data-mode]").forEach(function (el) { var on = el.getAttribute("data-mode") === mode; el.hidden = !on; $all("input,select", el).forEach(function (i) { i.disabled = !on; }); });
    var addr = $("[name=address_id]");
    if (addr) $all("[data-new-address]").forEach(function (el) { el.hidden = addr.value !== "new"; $all("input,select", el).forEach(function (i) { i.disabled = addr.value !== "new"; }); });
  }

  function localityId() {
    var addr = $("[name=address_id]");
    if (addr && addr.value !== "new") { var o = addr.options[addr.selectedIndex]; return o && o.getAttribute("data-locality"); }
    var l = $("[name=locality_id]"); return l && l.value;
  }

  function line(label, value, cls) {
    var d = document.createElement("div"); d.className = "line" + (cls ? " " + cls : "");
    var a = document.createElement("span"); a.textContent = label; var b = document.createElement("span"); b.textContent = value; d.appendChild(a); d.appendChild(b); return d;
  }

  function render(d) {
    summary.textContent = "";
    if (d.error && !d.price) { summary.appendChild(line("Error", d.error)); return; }
    var p = d.price;
    if (p.visit_fee) summary.appendChild(line("Visit fee", inr(p.visit_fee)));
    if (p.service_charge) summary.appendChild(line(p.is_estimate ? "Service (estimate)" : "Service charge", inr(p.service_charge)));
    if (p.duration_charge) summary.appendChild(line(houseHelp ? "Time (" + p.minutes / 60 + " hr)" : "Duration charge", inr(p.duration_charge)));
    summary.appendChild(line(p.is_quote ? "Due now" : (p.is_estimate ? "Estimated total" : "Total"), inr(p.total), "total"));
    if (p.note) { var n = document.createElement("p"); n.className = "hint"; n.textContent = p.note; summary.appendChild(n); }
    var pay = document.createElement("p"); pay.className = "hint"; pay.textContent = "Pay the professional in cash after the job. QuickFix recalculates this price on the server when you confirm."; summary.appendChild(pay);

    providers.textContent = ""; alts.textContent = "";
    if (!d.checked) { var h = document.createElement("p"); h.className = "muted small"; h.textContent = d.error || "Choose an address, date and time to see who is available."; providers.appendChild(h); return; }
    var any = choice("any", "Any available professional", "The first eligible professional to accept gets the job. It is confirmed only once someone accepts.", true);
    providers.appendChild(any);
    d.providers.forEach(function (pr) {
      var meta = pr.experience + " yrs experience" + (pr.rating ? " · " + pr.rating + " ★ (" + pr.reviews + ")" : " · no reviews yet") + " · " + inr(pr.total);
      providers.appendChild(choice(String(pr.id), pr.name, meta, false));
    });
    if (!d.providers.length) {
      var m = document.createElement("div"); m.className = "notice warn"; m.textContent = "Nobody is free at that exact time. You can still send an open request, or pick a nearby time:"; alts.appendChild(m);
      var row = document.createElement("div"); row.className = "chips";
      d.alternatives.forEach(function (a) {
        var b = document.createElement("button"); b.type = "button"; b.className = "chip"; b.textContent = a.label;
        b.addEventListener("click", function () { $("[name=date]").value = a.date; $("[name=time]").value = a.time; refresh(); }); row.appendChild(b);
      });
      if (!d.alternatives.length) { var none = document.createElement("p"); none.className = "muted small"; none.textContent = "No professionals are free in the next 3 days in this area. An open request will wait for someone to accept."; alts.appendChild(none); } else alts.appendChild(row);
    }
  }

  function choice(value, title, meta, checked) {
    var l = document.createElement("label"); l.className = "choice";
    var i = document.createElement("input"); i.type = "radio"; i.name = "professional_id"; i.value = value; i.checked = checked;
    var s = document.createElement("span"); var t = document.createElement("strong"); t.textContent = title; var m = document.createElement("span"); m.className = "meta"; m.textContent = meta;
    s.appendChild(t); s.appendChild(m); l.appendChild(i); l.appendChild(s); return l;
  }

  function refresh() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      var q = new URLSearchParams(); var my = ++seq;
      q.set("service", service);
      var mins = $("[name=minutes]"); if (mins && mins.value) q.set("minutes", mins.value);
      var mode = ($("input[name=mode]:checked") || {}).value || "once";
      var date = mode === "recurring" ? ($("[name=start_date]") || {}).value : ($("[name=date]") || {}).value;
      var time = ($("[name=time]") || {}).value, loc = localityId();
      $all("input[name=task]:checked").forEach(function (t) { q.append("task", t.value); });
      if (houseHelp) { var first = $("input[name=task]:checked"); if (first) q.set("service", first.value); }
      if (loc) q.set("locality_id", loc);
      if (date && mode !== "recurring") q.set("date", date);
      if (time && mode !== "recurring") q.set("time", time);
      summary.setAttribute("aria-busy", "true");
      fetch("/api/quote?" + q.toString(), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
        if (my !== seq) return; summary.removeAttribute("aria-busy"); render(d);
      }).catch(function () { summary.textContent = "Could not load the price. Check your connection."; });
    }, 250);
  }

  form.addEventListener("change", function () { toggles(); refresh(); });
  form.addEventListener("input", function (e) { if (e.target.type === "date" || e.target.type === "time") refresh(); });
  toggles(); refresh();
})();
