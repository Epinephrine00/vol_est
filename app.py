"""Flask app: VLM 3D extent + 2D projection, volume index / optional calibration."""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.parse import parse_qs, unquote

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from image_prep import prepare_image
from move_estimate import move_bp
from volume_calc import enrich_detections
from vlm_client import parse_detections, run_detection

load_dotenv()

app = Flask(__name__)
app.register_blueprint(move_bp)

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llava:7b").strip()
MAX_IMAGE_EDGE = int(os.environ.get("MAX_IMAGE_EDGE", "1280"))
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "180"))


ALLOWED_CT = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/jpg", "image/pjpeg"}
)


def _query_model_and_image() -> tuple[str, str]:
    """Parse ?model= & ?image= ; tolerate once-encoded query (e.g. ?image%3D...%26model%3D...)."""
    q_model = (request.args.get("model") or "").strip()
    q_image = (request.args.get("image") or "").strip()
    if q_model or q_image:
        return q_model, q_image
    raw = request.query_string.decode("utf-8", errors="replace")
    if not raw or "=" not in raw:
        return "", ""
    decoded = unquote(raw)
    if "=" not in decoded:
        return "", ""
    parsed = parse_qs(decoded, keep_blank_values=True)
    q_model = (parsed.get("model") or [""])[0].strip()
    q_image = (parsed.get("image") or [""])[0].strip()
    return q_model, q_image


@app.get("/")
def index():
    q_model, url_image_hint = _query_model_and_image()
    display_model = q_model or OLLAMA_MODEL
    return render_template(
        "index.html",
        default_model=display_model,
        max_image_edge=MAX_IMAGE_EDGE,
        url_image_hint=url_image_hint,
    )


@app.get("/api/health")
def health():
    from ollama import Client, ResponseError

    client = Client(host=OLLAMA_HOST, timeout=5.0)
    try:
        client.list()
        return jsonify({"ok": True, "ollama_reachable": True, "host": OLLAMA_HOST})
    except (ResponseError, OSError, ConnectionError) as e:
        return (
            jsonify(
                {
                    "ok": False,
                    "ollama_reachable": False,
                    "host": OLLAMA_HOST,
                    "error": str(e),
                }
            ),
            503,
        )


@app.post("/api/analyze")
def analyze():
    if "image" not in request.files:
        return jsonify({"error": "Missing file field `image`."}), 400
    f = request.files["image"]
    if not f or not f.filename:
        return jsonify({"error": "Empty upload."}), 400

    ct = (f.mimetype or "").lower()
    if ct not in ALLOWED_CT:
        return jsonify({"error": f"Unsupported content type: {ct!r}."}), 400

    raw = f.read()
    if not raw:
        return jsonify({"error": "Empty file body."}), 400

    model = (request.form.get("model") or OLLAMA_MODEL).strip() or OLLAMA_MODEL

    calibration: dict[str, Any] | None = None
    cal_raw = request.form.get("calibration")
    if cal_raw:
        try:
            calibration = json.loads(cal_raw)
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid JSON in `calibration` field."}), 400
        if not isinstance(calibration, dict):
            return jsonify({"error": "`calibration` must be a JSON object."}), 400
        for key in ("box_index", "axis", "cm"):
            if key not in calibration:
                return jsonify({"error": f"calibration missing key: {key}"}), 400

    try:
        png_bytes, iw, ih = prepare_image(raw, MAX_IMAGE_EDGE)
    except Exception as e:
        return jsonify({"error": f"Could not read image: {e}"}), 400

    try:
        raw_text = run_detection(OLLAMA_HOST, model, png_bytes, OLLAMA_TIMEOUT)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502

    try:
        detections = parse_detections(raw_text)
    except (json.JSONDecodeError, ValueError) as e:
        return (
            jsonify(
                {
                    "error": f"Failed to parse model output: {e}",
                    "hint": "Try another vision model or retry.",
                    "raw_excerpt": raw_text[:800],
                }
            ),
            422,
        )

    try:
        rows = enrich_detections(detections, iw, ih, calibration)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    payload = {
        "image_width": iw,
        "image_height": ih,
        "model_used": model,
        "detections": rows,
        "preview_png_base64": base64.b64encode(png_bytes).decode("ascii"),
    }
    return jsonify(payload)


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "File too large."}), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=True)
