"""이사 견적: VLM 사진 분석 + 사용자 extra_data 병합."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from flask import Blueprint, Response, jsonify, render_template, request

from image_prep import prepare_image
from move_pdf import build_estimate_pdf, pdf_attachment_headers
from move_vlm import parse_inventory, run_move_inventory

move_bp = Blueprint("move", __name__, url_prefix="/move")


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _normalize_manual_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        if not name or not isinstance(name, str):
            continue
        name = name.strip() or f"manual_{i}"
        try:
            vol = float(it.get("volume_m3", it.get("estimated_volume_m3", 0)) or 0)
        except (TypeError, ValueError):
            vol = 0.1
        vol = max(0.001, vol)
        try:
            qty = int(it.get("qty", 1) or 1)
        except (TypeError, ValueError):
            qty = 1
        qty = max(1, qty)
        out.append(
            {
                "name": name,
                "estimated_volume_m3": round(vol, 4),
                "qty": qty,
                "confidence": "user",
                "room_hint": str(it.get("room_hint", "manual")),
                "source": "user_json",
                "fragile": bool(it.get("fragile", False)),
            }
        )
    return out


def _merge_lines(
    photo_items: list[dict[str, Any]], manual_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """사용자 입력을 우선 나열 후, 사진 추정을 이어 붙임(간단 병합)."""
    merged: list[dict[str, Any]] = []
    seen = set()
    for it in manual_items:
        key = (it["name"].lower(), it["qty"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(it))
    for it in photo_items:
        key = (it["name"].lower(), it["qty"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(it))
    return merged


def _compute_quote(
    lines: list[dict[str, Any]],
    extra: dict[str, Any],
) -> dict[str, Any]:
    base = _env_float("MOVE_BASE_WON", 150000)
    per_m3 = _env_float("MOVE_PER_M3_WON", 120000)
    per_km = _env_float("MOVE_PER_KM_WON", 2500)
    floor_step = _env_float("MOVE_FLOOR_STEP_WON", 15000)

    total_vol = 0.0
    for ln in lines:
        total_vol += float(ln["estimated_volume_m3"]) * int(ln["qty"])

    dist = extra.get("distance_km")
    try:
        dist_km = max(0.0, float(dist)) if dist is not None else 0.0
    except (TypeError, ValueError):
        dist_km = 0.0

    of = extra.get("origin_floor", 0)
    df = extra.get("dest_floor", 0)
    try:
        of_i = max(0, int(of))
        df_i = max(0, int(df))
    except (TypeError, ValueError):
        of_i = df_i = 0
    ev_o = extra.get("elevator_origin", True)
    ev_d = extra.get("elevator_dest", True)
    floor_extra = 0.0
    if not bool(ev_o):
        floor_extra += of_i * floor_step
    if not bool(ev_d):
        floor_extra += df_i * floor_step

    volume_fee = total_vol * per_m3
    distance_fee = dist_km * per_km
    subtotal = base + volume_fee + distance_fee + floor_extra

    return {
        "currency": "KRW",
        "base_fee": int(round(base)),
        "volume_m3": round(total_vol, 4),
        "volume_fee": int(round(volume_fee)),
        "distance_km": dist_km,
        "distance_fee": int(round(distance_fee)),
        "floor_surcharge": int(round(floor_extra)),
        "total_ex_tax": int(round(subtotal)),
        "rate_notes": {
            "MOVE_BASE_WON": base,
            "MOVE_PER_M3_WON": per_m3,
            "MOVE_PER_KM_WON": per_km,
            "MOVE_FLOOR_STEP_WON": floor_step,
        },
    }


@move_bp.get("/")
def move_index():
    default_model = os.environ.get("OLLAMA_MODEL", "llava:7b").strip()
    max_edge = int(os.environ.get("MAX_IMAGE_EDGE", "1280"))
    return render_template(
        "move.html",
        default_model=default_model,
        max_image_edge=max_edge,
    )


@move_bp.post("/api/estimate")
def move_estimate_api():
    ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
    default_model = os.environ.get("OLLAMA_MODEL", "llava:7b").strip()
    max_edge = int(os.environ.get("MAX_IMAGE_EDGE", "1280"))
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "180"))
    model = (request.form.get("model") or default_model).strip() or default_model

    extra: dict[str, Any] = {}
    extra_raw = request.form.get("extra_data") or request.form.get("extra_json") or ""
    if extra_raw.strip():
        try:
            extra = json.loads(extra_raw)
        except json.JSONDecodeError:
            return jsonify({"error": "extra_data is not valid JSON."}), 400
        if not isinstance(extra, dict):
            return jsonify({"error": "extra_data must be a JSON object."}), 400

    files = request.files.getlist("images")
    images_png: list[bytes] = []
    thumbs_b64: list[str] = []
    max_images = int(os.environ.get("MOVE_MAX_IMAGES", "30"))

    allowed = frozenset(
        {"image/jpeg", "image/png", "image/webp", "image/jpg", "image/pjpeg"}
    )
    for f in files[:max_images]:
        if not f or not f.filename:
            continue
        ct = (f.mimetype or "").lower()
        if ct not in allowed:
            continue
        raw = f.read()
        if not raw:
            continue
        try:
            png, iw, ih = prepare_image(raw, max_edge)
        except Exception as e:
            return jsonify({"error": f"Could not read image {f.filename!r}: {e}"}), 400
        images_png.append(png)
        thumbs_b64.append(base64.b64encode(png).decode("ascii"))

    manual_items = _normalize_manual_items(extra.get("items"))

    user_prompt = (request.form.get("user_prompt") or "").strip()
    json_ctx = json.dumps(extra, ensure_ascii=False, indent=2) if extra else "{}"
    if user_prompt:
        vlm_ctx = (
            json_ctx
            + "\n\n---\nUSER_PROMPT (natural language):\n"
            + user_prompt
        )
    else:
        vlm_ctx = json_ctx

    vlm_block: dict[str, Any] | None = None
    vlm_raw = ""
    if images_png:
        try:
            vlm_raw = run_move_inventory(
                ollama_host, model, images_png, vlm_ctx, timeout
            )
            vlm_block = parse_inventory(vlm_raw)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 502
        except (json.JSONDecodeError, ValueError) as e:
            return (
                jsonify(
                    {
                        "error": f"VLM output parse failed: {e}",
                        "raw_excerpt": vlm_raw[:1200],
                    }
                ),
                422,
            )
    else:
        vlm_block = {
            "from_photos": [],
            "summary_ko": "사진이 없어 VLM 분석을 건너뛰었습니다. 아래 항목은 사용자 입력만 반영됩니다.",
        }

    photo_items = vlm_block["from_photos"]
    merged = _merge_lines(photo_items, manual_items)
    quote = _compute_quote(merged, extra)

    doc = {
        "title": "이사 견적(초안)",
        "user_prompt_sent": user_prompt,
        "generated_for": {
            "customer_name": extra.get("customer_name", ""),
            "move_date": extra.get("move_date", ""),
            "origin_address": extra.get("origin_address", ""),
            "dest_address": extra.get("dest_address", ""),
            "special_notes": extra.get("special_notes", ""),
        },
        "vlm": {
            "summary_ko": vlm_block.get("summary_ko", ""),
            "from_photos": photo_items,
        },
        "lines": merged,
        "quote": quote,
        "previews_base64": thumbs_b64,
        "model_used": model,
    }
    out_fmt = (
        request.form.get("output") or request.args.get("output") or "json"
    ).strip().lower()
    if out_fmt == "pdf":
        pdf_bytes = build_estimate_pdf(doc)
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers=pdf_attachment_headers(),
        )
    return jsonify(doc)
