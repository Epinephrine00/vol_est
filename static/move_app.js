(function () {
  var images = document.getElementById("images");
  var folderImages = document.getElementById("folderImages");
  var model = document.getElementById("model");
  var userPrompt = document.getElementById("userPrompt");
  var extraData = document.getElementById("extraData");
  var btnGo = document.getElementById("btnGo");
  var btnPdf = document.getElementById("btnPdf");
  var btnPdfAgain = document.getElementById("btnPdfAgain");
  var spin = document.getElementById("spin");
  var spinPdf = document.getElementById("spinPdf");
  var resultCard = document.getElementById("resultCard");
  var placeholderHint = document.getElementById("placeholderHint");
  var metaLine = document.getElementById("metaLine");
  var thumbs = document.getElementById("thumbs");
  var whoBlock = document.getElementById("whoBlock");
  var vlmSummary = document.getElementById("vlmSummary");
  var lineTable = document.getElementById("lineTable").querySelector("tbody");
  var feeList = document.getElementById("feeList");
  var totalLine = document.getElementById("totalLine");

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function isImageFile(f) {
    if (!f || !f.name) return false;
    var t = (f.type || "").toLowerCase();
    if (t.indexOf("image/") === 0) return true;
    var n = f.name.toLowerCase();
    return (
      n.endsWith(".jpg") ||
      n.endsWith(".jpeg") ||
      n.endsWith(".png") ||
      n.endsWith(".webp")
    );
  }

  function collectImageFiles() {
    var out = [];
    var i;
    for (i = 0; i < images.files.length; i++) {
      if (isImageFile(images.files[i])) out.push(images.files[i]);
    }
    for (i = 0; i < folderImages.files.length; i++) {
      if (isImageFile(folderImages.files[i])) out.push(folderImages.files[i]);
    }
    return out;
  }

  function buildFormData(output) {
    var fd = new FormData();
    fd.append("model", (model.value || "").trim());
    fd.append("user_prompt", (userPrompt && userPrompt.value) || "");
    fd.append("extra_data", extraData.value || "{}");
    if (output) fd.append("output", output);
    var files = collectImageFiles();
    for (var j = 0; j < files.length; j++) {
      fd.append("images", files[j]);
    }
    return fd;
  }

  function setLoading(on) {
    btnGo.disabled = on;
    btnPdf.disabled = on;
    spin.classList.toggle("d-none", !on);
  }

  function setPdfLoading(on) {
    btnGo.disabled = on;
    btnPdf.disabled = on;
    spinPdf.classList.toggle("d-none", !on);
  }

  function renderJson(d) {
    placeholderHint.style.display = "none";
    resultCard.style.display = "block";
        metaLine.textContent =
          "모델: " +
          (d.model_used || "") +
          (d.user_prompt_sent
            ? " · 사용자 프롬프트: " + (d.user_prompt_sent.length > 80 ? d.user_prompt_sent.slice(0, 80) + "…" : d.user_prompt_sent)
            : "");

    thumbs.innerHTML = "";
    (d.previews_base64 || []).forEach(function (b64) {
      var img = document.createElement("img");
      img.className = "thumb border";
      img.alt = "첨부";
      img.src = "data:image/png;base64," + b64;
      thumbs.appendChild(img);
    });

    var g = d.generated_for || {};
    whoBlock.innerHTML =
      '<dt class="col-4">성함</dt><dd class="col-8">' +
      esc(g.customer_name || "—") +
      "</dd>" +
      '<dt class="col-4">이사일</dt><dd class="col-8">' +
      esc(g.move_date || "—") +
      "</dd>" +
      '<dt class="col-4">출발</dt><dd class="col-8">' +
      esc(g.origin_address || "—") +
      "</dd>" +
      '<dt class="col-4">도착</dt><dd class="col-8">' +
      esc(g.dest_address || "—") +
      "</dd>" +
      '<dt class="col-4">비고</dt><dd class="col-8">' +
      esc(g.special_notes || "—") +
      "</dd>";

    vlmSummary.textContent = (d.vlm && d.vlm.summary_ko) || "—";

    lineTable.innerHTML = "";
    (d.lines || []).forEach(function (ln) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" +
        esc(ln.source) +
        "</td><td>" +
        esc(ln.name) +
        "</td><td>" +
        esc(ln.estimated_volume_m3) +
        "</td><td>" +
        esc(ln.qty) +
        '</td><td class="small">' +
        esc(ln.room_hint || "") +
        " / " +
        esc(ln.confidence || "") +
        "</td>";
      lineTable.appendChild(tr);
    });

    var q = d.quote || {};
    feeList.innerHTML =
      "<li>기본료: " +
      (q.base_fee != null ? q.base_fee.toLocaleString() : "—") +
      " 원</li>" +
      "<li>부피 합계: " +
      (q.volume_m3 != null ? q.volume_m3 : "—") +
      " ㎥ → 부피요금: " +
      (q.volume_fee != null ? q.volume_fee.toLocaleString() : "—") +
      " 원</li>" +
      "<li>거리: " +
      (q.distance_km != null ? q.distance_km : "—") +
      " km → 거리요금: " +
      (q.distance_fee != null ? q.distance_fee.toLocaleString() : "—") +
      " 원</li>" +
      "<li>층/엘리베이터 추가: " +
      (q.floor_surcharge != null ? q.floor_surcharge.toLocaleString() : "—") +
      " 원</li>";
    totalLine.textContent =
      "합계(참고, 세전): " +
      (q.total_ex_tax != null ? q.total_ex_tax.toLocaleString() : "—") +
      " 원";
  }

  function fetchPdf(fd) {
    return fetch("/move/api/estimate", { method: "POST", body: fd }).then(function (r) {
      var ct = (r.headers.get("content-type") || "").toLowerCase();
      if (ct.indexOf("application/pdf") !== -1) {
        return r.blob().then(function (blob) {
          return { ok: r.ok, pdf: blob, err: null };
        });
      }
      return r.json().then(function (j) {
        return { ok: r.ok, pdf: null, err: j };
      });
    });
  }

  function triggerDownload(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name || "견적서.pdf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 2000);
  }

  btnGo.addEventListener("click", function () {
    var fd = buildFormData("");
    setLoading(true);
    fetch("/move/api/estimate", { method: "POST", body: fd })
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, body: j };
        });
      })
      .then(function (res) {
        if (!res.ok) {
          alert(res.body.error || "요청 실패");
          return;
        }
        renderJson(res.body);
      })
      .catch(function (e) {
        alert("네트워크 오류: " + (e && e.message ? e.message : String(e)));
      })
      .finally(function () {
        setLoading(false);
      });
  });

  function runPdf(fd) {
    setPdfLoading(true);
    fetchPdf(fd)
      .then(function (res) {
        if (!res.ok) {
          alert((res.err && res.err.error) || "PDF 생성 실패");
          return;
        }
        if (res.pdf) triggerDownload(res.pdf, "견적서.pdf");
        else alert((res.err && res.err.error) || "PDF 생성 실패");
      })
      .catch(function (e) {
        alert("네트워크 오류: " + (e && e.message ? e.message : String(e)));
      })
      .finally(function () {
        setPdfLoading(false);
      });
  }

  btnPdf.addEventListener("click", function () {
    runPdf(buildFormData("pdf"));
  });

  btnPdfAgain.addEventListener("click", function () {
    runPdf(buildFormData("pdf"));
  });
})();
