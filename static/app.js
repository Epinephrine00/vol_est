/* global fetch, FormData */
(function () {
  const form = document.getElementById("analyzeForm");
  const imageInput = document.getElementById("image");
  const modelInput = document.getElementById("model");
  const submitBtn = document.getElementById("submitBtn");
  const btnSpinner = document.getElementById("btnSpinner");
  const alertArea = document.getElementById("alertArea");
  const previewWrap = document.getElementById("previewWrap");
  const previewImg = document.getElementById("previewImg");
  const previewPlaceholder = document.getElementById("previewPlaceholder");
  const bboxLayer = document.getElementById("bboxLayer");
  const metaLine = document.getElementById("metaLine");
  const resultTable = document.getElementById("resultTable");
  const resultBody = document.getElementById("resultBody");
  const ollamaStatus = document.getElementById("ollamaStatus");
  const calBox = document.getElementById("calBox");
  const calAxis = document.getElementById("calAxis");
  const calCm = document.getElementById("calCm");

  const colors = [
    "#ff6b6b",
    "#4ecdc4",
    "#ffe66d",
    "#95e1d3",
    "#f38181",
    "#aa96da",
    "#fcbad3",
    "#a8d8ea",
  ];

  const svgNS = "http://www.w3.org/2000/svg";

  let lastDetections = null;

  function showAlert(type, message) {
    alertArea.innerHTML =
      `<div class="alert alert-${type} alert-dismissible fade show" role="alert">` +
      escapeHtml(message) +
      '<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button></div>';
  }

  function clearAlert() {
    alertArea.innerHTML = "";
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function setLoading(on) {
    submitBtn.disabled = on;
    btnSpinner.classList.toggle("d-none", !on);
  }

  function fetchHealth() {
    fetch("/api/health")
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, body: j };
        });
      })
      .then(function (ref) {
        if (ref.ok && ref.body.ollama_reachable) {
          ollamaStatus.textContent = "Ollama 연결됨 · " + (ref.body.host || "");
          ollamaStatus.className = "mt-3 small text-success";
        } else {
          ollamaStatus.textContent =
            "Ollama에 연결할 수 없습니다. (" +
            (ref.body && ref.body.host ? ref.body.host : "") +
            ")";
          ollamaStatus.className = "mt-3 small text-warning";
        }
      })
      .catch(function () {
        ollamaStatus.textContent = "상태 확인 요청 실패.";
        ollamaStatus.className = "mt-3 small text-danger";
      });
  }

  function setCalibrationEnabled(enabled, maxIndex) {
    calBox.disabled = !enabled;
    calAxis.disabled = !enabled;
    calCm.disabled = !enabled;
    if (enabled && maxIndex >= 0) {
      calBox.max = String(maxIndex);
      if (parseInt(calBox.value, 10) > maxIndex) calBox.value = "0";
    }
  }

  setCalibrationEnabled(false, -1);

  function addLine(parent, x1, y1, x2, y2, strokeColor) {
    const ln = document.createElementNS(svgNS, "line");
    ln.setAttribute("x1", x1);
    ln.setAttribute("y1", y1);
    ln.setAttribute("x2", x2);
    ln.setAttribute("y2", y2);
    ln.setAttribute("stroke", strokeColor);
    ln.setAttribute("stroke-width", "2.5");
    ln.setAttribute("stroke-linecap", "round");
    parent.appendChild(ln);
  }

  /**
   * extent_xyz 비율의 직육면체를 등각(isometric)에 가까운 선형 투영 후,
   * 2D 검출 bbox 안에 맞춰 스케일·이동한 8점·12모서리 와이어를 그림.
   * (실제 3D 복원 아님, 점유 공간을 박스로 시각화한 2D 오버레이)
   */
  function isoProject(x, y, z) {
    const c30 = 0.8660254037844386;
    const px = (x - z) * c30;
    const py = -y + (x + z) * 0.5;
    return [px, py];
  }

  function drawOccupancyWireframe(parent, fx1, fy1, fx2, fy2, extent, strokeColor) {
    const fw = Math.max(fx2 - fx1, 1);
    const fh = Math.max(fy2 - fy1, 1);
    let ex = 1;
    let ey = 1;
    let ez = 1;
    if (extent && extent.length === 3) {
      ex = Math.max(extent[0], 1e-6);
      ey = Math.max(extent[1], 1e-6);
      ez = Math.max(extent[2], 1e-6);
    }
    const m = Math.max(ex, ey, ez);
    ex /= m;
    ey /= m;
    ez /= m;
    const base = Math.min(fw, fh) * 0.42;
    const hx = (ex * base) / 2;
    const hy = (ey * base) / 2;
    const hz = (ez * base) / 2;

    const bits = [
      [0, 0, 0],
      [1, 0, 0],
      [1, 1, 0],
      [0, 1, 0],
      [0, 0, 1],
      [1, 0, 1],
      [1, 1, 1],
      [0, 1, 1],
    ];
    const corners3 = bits.map(function (t) {
      const ix = t[0];
      const iy = t[1];
      const iz = t[2];
      return [
        ix ? hx : -hx,
        iy ? hy : -hy,
        iz ? hz : -hz,
      ];
    });

    const proj = corners3.map(function (p) {
      return isoProject(p[0], p[1], p[2]);
    });

    let minX = proj[0][0];
    let maxX = proj[0][0];
    let minY = proj[0][1];
    let maxY = proj[0][1];
    for (let k = 1; k < proj.length; k++) {
      const q = proj[k];
      if (q[0] < minX) minX = q[0];
      if (q[0] > maxX) maxX = q[0];
      if (q[1] < minY) minY = q[1];
      if (q[1] > maxY) maxY = q[1];
    }
    const spanX = Math.max(maxX - minX, 1e-6);
    const spanY = Math.max(maxY - minY, 1e-6);
    const cxB = (fx1 + fx2) / 2;
    const cyB = (fy1 + fy2) / 2;
    const scale = Math.min(fw / spanX, fh / spanY) * 0.88;
    const mx = (minX + maxX) / 2;
    const my = (minY + maxY) / 2;

    const pts = proj.map(function (q) {
      return [scale * (q[0] - mx) + cxB, scale * (q[1] - my) + cyB];
    });

    const edges = [
      [0, 1],
      [1, 2],
      [2, 3],
      [3, 0],
      [4, 5],
      [5, 6],
      [6, 7],
      [7, 4],
      [0, 4],
      [1, 5],
      [2, 6],
      [3, 7],
    ];
    for (let e = 0; e < edges.length; e++) {
      const a = edges[e][0];
      const b = edges[e][1];
      const pa = pts[a];
      const pb = pts[b];
      addLine(parent, pa[0], pa[1], pb[0], pb[1], strokeColor);
    }
  }

  function drawBoxes(detections) {
    while (bboxLayer.firstChild) bboxLayer.removeChild(bboxLayer.firstChild);
    const w = previewImg.clientWidth;
    const h = previewImg.clientHeight;
    bboxLayer.setAttribute("viewBox", "0 0 " + w + " " + h);
    bboxLayer.setAttribute("preserveAspectRatio", "none");

    detections.forEach(function (d, i) {
      const b = d.bbox_xyxy;
      const fx1 = b[0] * w;
      const fy1 = b[1] * h;
      const fx2 = b[2] * w;
      const fy2 = b[3] * h;
      const col = colors[i % colors.length];
      drawOccupancyWireframe(bboxLayer, fx1, fy1, fx2, fy2, d.extent_xyz, col);

      const text = document.createElementNS(svgNS, "text");
      text.setAttribute("x", fx1 + 4);
      text.setAttribute("y", fy1 + 16);
      text.setAttribute("fill", col);
      text.setAttribute("font-size", "14");
      text.setAttribute("font-weight", "600");
      text.setAttribute("stroke", "#000");
      text.setAttribute("stroke-width", "0.35");
      text.setAttribute("paint-order", "stroke");
      text.textContent = String(i);
      bboxLayer.appendChild(text);
    });
  }

  previewImg.addEventListener("load", function () {
    const d = previewImg.dataset.detections;
    if (!d) return;
    try {
      drawBoxes(JSON.parse(d));
    } catch (_e) {
      /* ignore */
    }
  });

  window.addEventListener("resize", function () {
    const d = previewImg.dataset.detections;
    if (!d || !previewImg.complete) return;
    try {
      drawBoxes(JSON.parse(d));
    } catch (_e) {
      /* ignore */
    }
  });

  ["input", "change"].forEach(function (ev) {
    calBox.addEventListener(ev, recalculateVolumes);
    calAxis.addEventListener(ev, recalculateVolumes);
    calCm.addEventListener(ev, recalculateVolumes);
  });

  function axisIndex(axisVal) {
    const a = String(axisVal).toLowerCase();
    if (a === "x" || a === "ex" || a === "0") return 0;
    if (a === "y" || a === "ey" || a === "1") return 1;
    return 2;
  }

  function cmPerUnitFromCalibration(detections, boxIndex, axisVal, cm) {
    if (!detections[boxIndex] || !detections[boxIndex].extent_xyz) return null;
    const ext = detections[boxIndex].extent_xyz;
    const ai = axisIndex(axisVal);
    const ev = ext[ai];
    if (!(ev > 0) || !(cm > 0)) return null;
    return cm / ev;
  }

  function volumeCm3FromExtent(ext, s) {
    return ext[0] * s * ext[1] * s * ext[2] * s;
  }

  function recalculateVolumes() {
    if (!lastDetections || !lastDetections.length) return;

    const cells = resultBody.querySelectorAll(".vol-cell");
    const cmVal = parseFloat(String(calCm.value), 10);
    const useCal =
      !calCm.disabled &&
      calCm.value !== "" &&
      !Number.isNaN(cmVal) &&
      cmVal > 0;

    let s = null;
    if (useCal) {
      const bi = Math.min(
        Math.max(parseInt(calBox.value, 10) || 0, 0),
        lastDetections.length - 1
      );
      s = cmPerUnitFromCalibration(lastDetections, bi, calAxis.value, cmVal);
    }

    lastDetections.forEach(function (row, idx) {
      const ext = row.extent_xyz;
      let text = "—";
      if (s != null && ext && ext.length === 3) {
        const v = volumeCm3FromExtent(ext, s);
        text = Number.isFinite(v) ? String(Math.round(v * 10000) / 10000) : "—";
      }
      const cell = cells[idx];
      if (cell) cell.textContent = text;
    });
  }

  function buildResultRows(detections) {
    resultBody.innerHTML = "";
    detections.forEach(function (row, idx) {
      const tr = document.createElement("tr");
      const ext = row.extent_xyz
        ? escapeHtml(JSON.stringify(row.extent_xyz))
        : "—";
      tr.innerHTML =
        "<td>" +
        idx +
        "</td><td>" +
        escapeHtml(row.label) +
        "</td><td><code class=\"small\">" +
        escapeHtml(JSON.stringify(row.bbox_xyxy)) +
        "</code></td><td><code class=\"small\">" +
        ext +
        "</code></td><td>" +
        row.volume_index +
        "</td><td class=\"vol-cell\" data-idx=\"" +
        idx +
        "\">—</td>";
      resultBody.appendChild(tr);
    });
    recalculateVolumes();
  }

  function runAnalyze() {
    clearAlert();
    const file = imageInput.files && imageInput.files[0];
    if (!file) {
      showAlert("warning", "이미지를 선택하세요.");
      return;
    }

    const fd = new FormData();
    fd.append("image", file);
    fd.append("model", (modelInput.value || "").trim());

    setLoading(true);
    fetch("/api/analyze", { method: "POST", body: fd })
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, body: j };
        });
      })
      .then(function (res) {
        if (!res.ok) {
          let msg = res.body.error || "요청 실패";
          if (res.body.raw_excerpt) {
            msg += "\n\n모델 출력 일부:\n" + res.body.raw_excerpt;
          }
          showAlert("danger", msg);
          return;
        }
        const data = res.body;
        metaLine.textContent =
          "모델: " +
          (data.model_used || "") +
          " · 이미지: " +
          data.image_width +
          "×" +
          data.image_height +
          " px";

        previewPlaceholder.hidden = true;
        previewWrap.hidden = false;

        const dets = data.detections || [];
        lastDetections = JSON.parse(JSON.stringify(dets));

        previewImg.dataset.detections = JSON.stringify(dets);
        previewImg.src = "data:image/png;base64," + (data.preview_png_base64 || "");

        buildResultRows(dets);

        resultTable.hidden = dets.length === 0;
        const n = dets.length;
        setCalibrationEnabled(n > 0, Math.max(0, n - 1));
        recalculateVolumes();

        showAlert("success", "분석이 완료되었습니다. 박스 수: " + n);
      })
      .catch(function (e) {
        showAlert("danger", "네트워크 오류: " + (e && e.message ? e.message : String(e)));
      })
      .finally(function () {
        setLoading(false);
      });
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    runAnalyze();
  });

  submitBtn.addEventListener("click", function (ev) {
    ev.preventDefault();
    runAnalyze();
  });

  fetchHealth();
})();
