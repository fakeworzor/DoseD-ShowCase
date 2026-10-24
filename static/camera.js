// BP photo screen. Live camera in the page when the browser allows it (HTTPS or localhost);
// otherwise the "open camera" button uses the phone's own camera. Without JavaScript the plain file input still works.
(function () {
  var form = document.getElementById("photo-form");
  if (!form) return;
  var $ = function (id) { return document.getElementById(id); };
  var input = $("image"), pick = $("image-pick"), send = $("pick-send"), preview = $("pick-preview");
  var cam = $("cam"), video = $("cam-video"), shot = $("cam-shot"), shoot = $("cam-shoot"), after = $("cam-after");
  var pending = null, stream = null, loading = $("photo-loading");

  // iOS shows its own play/pause button over a <video> unless it is playing and untouchable
  video.controls = false;
  video.setAttribute("disablepictureinpicture", "");
  video.setAttribute("disableremoteplayback", "");
  function play() { var p = video.play(); if (p && p.catch) p.catch(function () {}); }

  // full-screen "reading the numbers" screen while the photo uploads and is read
  function busy(on) { if (loading) loading.hidden = !on; document.body.classList.toggle("is-busy", on); }
  window.addEventListener("pageshow", function () { busy(false); });   // back button: never leave it stuck

  input.required = false;               // checked below instead, so a hidden input never blocks sending
  pick.closest("label").hidden = false;
  send.hidden = true;

  function setFile(file) {
    pending = file;
    try { var dt = new DataTransfer(); dt.items.add(file); input.files = dt.files; } catch (e) { /* sent with fetch below */ }
  }
  function showPreview(file) {
    preview.src = URL.createObjectURL(file);
    preview.hidden = false;
    send.hidden = false;
  }
  input.addEventListener("change", function () { if (input.files[0]) { pending = input.files[0]; showPreview(pending); } });
  pick.addEventListener("change", function () { if (pick.files[0]) { setFile(pick.files[0]); showPreview(pick.files[0]); } });

  form.addEventListener("submit", function (e) {
    if (!input.files.length && !pending) { e.preventDefault(); return; }
    busy(true);
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    if (input.files.length) return;      // normal form post; the loading screen stays until the next page
    e.preventDefault();
    var fd = new FormData();
    fd.append("image", pending, "bp.jpg");
    fetch(form.action, { method: "POST", body: fd }).then(function (r) {
      if (r.redirected) { location.href = r.url; return; }
      return r.text().then(function (html) { document.open(); document.write(html); document.close(); });
    }).catch(function () { busy(false); });
  });

  // live camera
  if (!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) return;
  navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } }, audio: false })
    .then(function (s) {
      stream = s;
      video.srcObject = s;
      cam.hidden = false;
      play();
      input.closest("label").hidden = true;   // the in-page camera replaces the "open camera" button
    })
    .catch(function () { $("cam-denied").hidden = false; });

  shoot.addEventListener("click", function () {
    var c = document.createElement("canvas");
    c.width = video.videoWidth; c.height = video.videoHeight;
    c.getContext("2d").drawImage(video, 0, 0);
    c.toBlob(function (b) {
      setFile(new File([b], "bp.jpg", { type: "image/jpeg" }));
      shot.src = URL.createObjectURL(b);
      shot.hidden = false; video.hidden = true; shoot.hidden = true; after.hidden = false;
    }, "image/jpeg", 0.9);
  });
  $("cam-retake").addEventListener("click", function () {
    pending = null; input.value = "";
    shot.hidden = true; video.hidden = false; shoot.hidden = false; after.hidden = true;
    play();
  });
})();
