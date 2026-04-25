(function () {
  var form = document.getElementById("estForm");
  var images = document.getElementById("images");
  var model = document.getElementById("model");
  var extraData = document.getElementById("extraData");
  var btnGo = document.getElementById("btnGo");
  var spin = document.getElementById("spin");
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

  function setLoading(on) {
    btnGo.disabled = on;
    spin.classList.toggle("d-none", !on);
  }

  btnGo.addEventListener("click", function () {
    var fd = new FormData();
    fd.append("model", (model.value || "").trim());
    fd.append("extra_data", extraData.value || "{}");
    var files = images.files;
    for (var i = 0; i < files.length; i++) {
      fd.append("images", files[i]);
    }

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
        var d = res.body;
        placeholderHint.style.display = "none";
        resultCard.style.display = "block";
        metaLine.textContent = "모델: " + (d.model_used || "");

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
          "<dt class=\"col-4\">성함</dt><dd class=\"col-8\">" +
          esc(g.customer_name || "—") +
          "</dd>" +
          "<dt class=\"col-4\">이사일</dt><dd class=\"col-8\">" +
          esc(g.move_date || "—") +
          "</dd>" +
          "<dt class=\"col-4\">출발</dt><dd class=\"col-8\">" +
          esc(g.origin_address || "—") +
          "</dd>" +
          "<dt class=\"col-4\">도착</dt><dd class=\"col-8\">" +
          esc(g.dest_address || "—") +
          "</dd>" +
          "<dt class=\"col-4\">비고</dt><dd class=\"col-8\">" +
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
            "</td><td class=\"small\">" +
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
      })
      .catch(function (e) {
        alert("네트워크 오류: " + (e && e.message ? e.message : String(e)));
      })
      .finally(function () {
        setLoading(false);
      });
  });
})();
