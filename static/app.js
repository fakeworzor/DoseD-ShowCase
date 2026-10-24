// Small enhancements only. Every page works without JavaScript.
(function () {
  // Patient text size (remembered on this phone only)
  var root = document.documentElement;
  var KEY = "dose-d-scale";
  try { var s = parseFloat(localStorage.getItem(KEY)); if (s) root.style.setProperty("--scale", s); } catch (e) {}
  document.querySelectorAll("[data-scale]").forEach(function (b) {
    b.addEventListener("click", function () {
      var cur = parseFloat(getComputedStyle(root).getPropertyValue("--scale")) || 1;
      var next = Math.min(1.5, Math.max(0.85, cur + parseFloat(b.dataset.scale)));
      root.style.setProperty("--scale", next);
      try { localStorage.setItem(KEY, next); } catch (e) {}
    });
  });

  // 5-minute rest timer
  var t = document.querySelector("[data-timer]");
  if (t) {
    t.addEventListener("click", function () {
      var out = document.getElementById(t.dataset.timer), left = 300;
      t.disabled = true;
      var tick = setInterval(function () {
        left -= 1;
        out.textContent = Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0");
        if (left <= 0) { clearInterval(tick); out.textContent = t.dataset.done; t.disabled = false; }
      }, 1000);
      out.textContent = "5:00";
    });
  }

  // Read aloud (Thai) on the medicine screen
  var r = document.querySelector("[data-read]");
  if (r && "speechSynthesis" in window) {
    r.hidden = false;
    r.addEventListener("click", function () {
      var u = new SpeechSynthesisUtterance(document.getElementById(r.dataset.read).innerText);
      u.lang = "th-TH"; speechSynthesis.cancel(); speechSynthesis.speak(u);
    });
  }

  // Patient: "all done" pop-up. Server renders it open; here it becomes a modal and won't reappear on refresh.
  var dlg = document.querySelector("dialog[data-auto]");
  if (dlg) {
    if (dlg.showModal) { dlg.close(); dlg.showModal(); }
    try { history.replaceState(null, "", location.pathname); } catch (e) {}
  }

  // Forms that ask first (patient menu: reset demo data)
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!window.confirm(f.dataset.confirm)) e.preventDefault(); });
  });

  // Check-in attachments: list what was picked, stop files over the limit before uploading
  var media = document.getElementById("media");
  if (media) {
    var list = document.getElementById("media-list");
    media.addEventListener("change", function () {
      var max = parseFloat(media.dataset.maxMb) * 1048576, many = parseInt(media.dataset.maxFiles, 10);
      var files = Array.prototype.slice.call(media.files), err = "";
      if (files.length > many) err = media.dataset.msgMany;
      files.forEach(function (f) { if (f.size > max) err = media.dataset.msgBig; });
      list.textContent = "";
      if (err) {
        media.value = "";
        var e = document.createElement("p"); e.className = "alert red"; e.textContent = err; list.appendChild(e);
        return;
      }
      files.forEach(function (f) {
        var d = document.createElement("div");
        d.textContent = f.name + " · " + (f.size / 1048576).toFixed(1) + " MB";
        list.appendChild(d);
      });
    });
  }

  // Doctor: wide tables open scrolled to the newest column (Today is on the right)
  document.querySelectorAll("[data-scroll-end]").forEach(function (el) { el.scrollLeft = el.scrollWidth; });

  // Doctor: in each decision form, approve stays disabled until its evidence box is ticked (server checks too)
  document.querySelectorAll("form").forEach(function (f) {
    var ev = f.querySelector("[name=evidence_ok]");
    if (!ev) return;
    var btns = f.querySelectorAll("[data-needs-evidence]");
    var sync = function () { btns.forEach(function (b) { b.disabled = !ev.checked; }); };
    ev.addEventListener("change", sync); sync();
  });

  // Doctor: Approve / Edit / Reject buttons that need a step open it in place (one open at a time).
  // Without JavaScript they are links that reload with ?open=…
  var toggles = document.querySelectorAll("[data-panel]");
  toggles.forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      var panel = document.getElementById(a.dataset.panel), show = panel.hidden;
      toggles.forEach(function (o) { document.getElementById(o.dataset.panel).hidden = true; o.setAttribute("aria-expanded", "false"); });
      panel.hidden = !show;
      a.setAttribute("aria-expanded", String(show));
      if (show) panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  });

  // LINE LIFF (only when settings.line.liff_id is set)
  var liffId = document.body.dataset.liff;
  if (liffId && window.liff) { window.liff.init({ liffId: liffId }).catch(function () {}); }
})();
