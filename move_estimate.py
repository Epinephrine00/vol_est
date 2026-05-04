"""이사 견적: VLM 사진 분석 + 사용자 extra_data 병합."""

from __future__ import annotations

import base64
import csv
import io
import json
import os
from typing import Any

from flask import Blueprint, Response, jsonify, render_template, request

from image_prep import prepare_image
from move_vlm import parse_inventory, parse_quote_filter, run_move_inventory, run_quote_filter

move_bp = Blueprint("move", __name__, url_prefix="/move")
FIXED_MOVE_MODEL = "gemma4:e2b"


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
    base = _env_float("MOVE_BASE_WON", 120000)
    per_m3 = _env_float("MOVE_PER_M3_WON", 33000)
    per_km = _env_float("MOVE_PER_KM_WON", 0)
    floor_step = _env_float("MOVE_FLOOR_STEP_WON", 0)
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


def _parse_extra_form() -> tuple[dict[str, Any] | None, Any]:
    extra_raw = request.form.get("extra_data") or request.form.get("extra_json") or ""
    if not extra_raw.strip():
        return {}, None
    try:
        extra = json.loads(extra_raw)
    except json.JSONDecodeError:
        return None, (jsonify({"error": "extra_data is not valid JSON."}), 400)
    if not isinstance(extra, dict):
        return None, (jsonify({"error": "extra_data must be a JSON object."}), 400)
    return extra, None


def _prepare_uploaded_images() -> tuple[list[bytes], list[str], Any]:
    max_edge = int(os.environ.get("MAX_IMAGE_EDGE", "1280"))
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
            png, _iw, _ih = prepare_image(raw, max_edge)
        except Exception as e:
            return [], [], (
                jsonify({"error": f"Could not read image {f.filename!r}: {e}"}),
                400,
            )
        images_png.append(png)
        thumbs_b64.append(base64.b64encode(png).decode("ascii"))
    return images_png, thumbs_b64, None


def _run_optional_move_vlm(
    images_png: list[bytes],
    extra: dict[str, Any],
    user_prompt: str,
    model: str,
) -> tuple[dict[str, Any] | None, Any]:
    if user_prompt:
        vlm_ctx = (
            json.dumps(extra, ensure_ascii=False, indent=2)
            + "\n\n---\nUSER_PROMPT (natural language):\n"
            + user_prompt
        )
    else:
        vlm_ctx = json.dumps(extra, ensure_ascii=False, indent=2) if extra else "{}"

    if not images_png:
        return {
            "from_photos": [],
            "summary_ko": "사진이 없어 VLM 분석을 건너뛰었습니다. 아래 항목은 사용자 입력만 반영됩니다.",
        }, None

    ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "180"))
    vlm_raw = ""
    try:
        vlm_raw = run_move_inventory(ollama_host, model, images_png, vlm_ctx, timeout)
        return parse_inventory(vlm_raw), None
    except RuntimeError as e:
        return None, (jsonify({"error": str(e)}), 502)
    except (json.JSONDecodeError, ValueError) as e:
        return None, (
            jsonify(
                {
                    "error": f"VLM output parse failed: {e}",
                    "raw_excerpt": vlm_raw[:1200],
                }
            ),
            422,
        )


def _read_json_upload(field_name: str, required: bool = False) -> tuple[dict[str, Any] | None, Any]:
    upload = request.files.get(field_name)
    raw = ""
    if upload and upload.filename:
        raw = upload.read().decode("utf-8-sig")
    else:
        raw = request.form.get(field_name, "")
    if not raw.strip():
        if required:
            return None, (jsonify({"error": f"Missing `{field_name}`."}), 400)
        return None, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, (jsonify({"error": f"`{field_name}` is not valid JSON: {e}"}), 400)
    if not isinstance(data, dict):
        return None, (jsonify({"error": f"`{field_name}` must be a JSON object."}), 400)
    return data, None


def _confidence_from_score(score: Any) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "medium"
    if s >= 0.75:
        return "high"
    if s < 0.45:
        return "low"
    return "medium"


def _items_from_rmc_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for i, inst in enumerate(summary.get("instances") or []):
        if not isinstance(inst, dict):
            continue
        label = str(inst.get("label") or f"rmc_item_{i}").strip()
        obb = inst.get("obb") if isinstance(inst.get("obb"), dict) else {}
        try:
            volume = float(obb.get("volume", inst.get("volume_m3", 0)) or 0)
        except (TypeError, ValueError):
            volume = 0.0
        if volume <= 0:
            continue
        items.append(
            {
                "name": label,
                "estimated_volume_m3": round(max(0.001, volume), 4),
                "qty": 1,
                "confidence": _confidence_from_score(inst.get("score")),
                "room_hint": "rmc_scene",
                "source": "rmc_summary",
                "rmc_instance_id": inst.get("id", i),
                "obb": obb,
            }
        )
    return items


def _parse_rmc_quote_csv() -> dict[str, Any] | None:
    upload = request.files.get("quote_csv")
    raw = ""
    if upload and upload.filename:
        raw = upload.read().decode("utf-8-sig")
    else:
        raw = request.form.get("quote_csv", "")
    if not raw.strip():
        return None

    rows = list(csv.reader(io.StringIO(raw)))
    if not rows:
        return None

    line_items: list[dict[str, Any]] = []
    totals: dict[str, Any] = {}
    for row in rows[1:]:
        row = row + [""] * max(0, 9 - len(row))
        if not any(cell.strip() for cell in row):
            continue
        if row[0].strip():
            line_items.append(
                {
                    "instance_id": row[0],
                    "label": row[1],
                    "volume_m3": _coerce_float(row[2]),
                    "rate_per_m3": _coerce_float(row[3]),
                    "handling_multiplier": _coerce_float(row[4]),
                    "min_charge": _coerce_float(row[5]),
                    "line_subtotal": _coerce_float(row[6]),
                    "currency": row[7],
                    "note": row[8],
                }
            )
            continue
        key = row[1].strip()
        if key:
            totals[key] = {
                "amount": _coerce_float(row[6]),
                "currency": row[7],
                "note": row[8],
            }
    return {"line_items": line_items, "totals": totals}


def _items_from_quote_csv(quote_csv: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not quote_csv:
        return []
    items = []
    for i, row in enumerate(quote_csv.get("line_items") or []):
        label = str(row.get("label") or f"quote_item_{i}").strip()
        if not label:
            continue
        volume = row.get("volume_m3")
        subtotal = row.get("line_subtotal")
        if not volume or volume <= 0:
            continue
        item_id = str(row.get("instance_id") or i)
        items.append(
            {
                "id": item_id,
                "name": label,
                "estimated_volume_m3": round(max(0.001, float(volume)), 4),
                "qty": 1,
                "confidence": "quote_csv",
                "room_hint": "견적 보조 데이터",
                "source": "quote_csv",
                "quote_subtotal": subtotal,
                "quote_currency": row.get("currency"),
                "quote_note": row.get("note"),
            }
        )
    return items


def _filter_quote_items_with_vlm(
    quote_items: list[dict[str, Any]],
    images_png: list[bytes],
    extra: dict[str, Any],
    user_prompt: str,
    model: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
    if not quote_items:
        return [], {
            "visible_ids": [],
            "excluded": [],
            "summary_ko": "quote.csv에 필터링할 품목이 없습니다.",
            "filter_applied": False,
        }, None
    if not images_png:
        return quote_items, {
            "visible_ids": [str(item["id"]) for item in quote_items],
            "excluded": [],
            "summary_ko": "사진이 없어 quote.csv 품목을 그대로 사용했습니다.",
            "filter_applied": False,
        }, None

    context = {
        "quote_candidates": [
            {
                "id": str(item["id"]),
                "name": item["name"],
                "estimated_volume_m3": item["estimated_volume_m3"],
                "quote_subtotal": item.get("quote_subtotal"),
                "note": item.get("quote_note"),
            }
            for item in quote_items
        ],
        "move_context": extra,
    }
    if user_prompt:
        context["user_prompt"] = user_prompt

    ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "180"))
    raw = ""
    try:
        raw = run_quote_filter(
            ollama_host,
            model,
            images_png,
            json.dumps(context, ensure_ascii=False, indent=2),
            timeout,
        )
        candidate_ids = {str(item["id"]) for item in quote_items}
        filt = parse_quote_filter(raw, candidate_ids)
    except RuntimeError as e:
        return [], {}, (jsonify({"error": str(e)}), 502)
    except (json.JSONDecodeError, ValueError) as e:
        return [], {}, (
            jsonify(
                {
                    "error": f"VLM quote filter output parse failed: {e}",
                    "raw_excerpt": raw[:1200],
                }
            ),
            422,
        )

    visible = set(filt["visible_ids"])
    kept = [dict(item, source="quote_csv_visible") for item in quote_items if str(item["id"]) in visible]
    excluded_ids = {str(item["id"]) for item in quote_items if str(item["id"]) not in visible}
    known_excluded = {str(item.get("id")) for item in filt.get("excluded", [])}
    for item_id in sorted(excluded_ids - known_excluded):
        filt.setdefault("excluded", []).append(
            {"id": item_id, "reason_ko": "사진에서 명확히 확인되지 않음"}
        )
    filt["filter_applied"] = True
    return kept, filt, None


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _summarize_rmc_viz(viz: dict[str, Any] | None) -> dict[str, Any] | None:
    if not viz:
        return None
    instances = []
    for inst in viz.get("instances") or []:
        if not isinstance(inst, dict):
            continue
        instances.append(
            {
                "id": inst.get("id"),
                "label": inst.get("label"),
                "volume_m3": inst.get("volume_m3"),
                "n_points_total": inst.get("n_points_total"),
            }
        )
    raw = viz.get("raw") if isinstance(viz.get("raw"), dict) else None
    return {
        "scene_bbox": viz.get("scene_bbox"),
        "n_instances": len(instances),
        "instances": instances,
        "raw_points_sampled": len(raw.get("points") or []) if raw else 0,
    }


def _build_doc(
    title: str,
    user_prompt: str,
    extra: dict[str, Any],
    vlm_block: dict[str, Any],
    lines: list[dict[str, Any]],
    quote: dict[str, Any],
    thumbs_b64: list[str],
    model: str,
) -> dict[str, Any]:
    return {
        "title": title,
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
            "from_photos": vlm_block.get("from_photos", []),
        },
        "lines": lines,
        "quote": quote,
        "previews_base64": thumbs_b64,
        "model_used": model,
    }


def _pdf_response(doc: dict[str, Any]):
    try:
        from move_pdf import build_estimate_pdf, pdf_attachment_headers
    except ModuleNotFoundError as e:
        if e.name == "reportlab":
            return (
                jsonify(
                    {
                        "error": "PDF generation requires `reportlab`. Run `pip install -r requirements.txt` in the vol_est environment.",
                    }
                ),
                500,
            )
        raise
    pdf_bytes = build_estimate_pdf(doc)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers=pdf_attachment_headers(),
    )


@move_bp.get("/")
def move_index():
    max_edge = int(os.environ.get("MAX_IMAGE_EDGE", "1280"))
    return render_template(
        "move.html",
        default_model=FIXED_MOVE_MODEL,
        max_image_edge=max_edge,
    )


@move_bp.post("/api/estimate")
def move_estimate_api():
    model = FIXED_MOVE_MODEL

    extra, err = _parse_extra_form()
    if err:
        return err
    assert extra is not None

    images_png, thumbs_b64, err = _prepare_uploaded_images()
    if err:
        return err

    manual_items = _normalize_manual_items(extra.get("items"))
    user_prompt = (request.form.get("user_prompt") or "").strip()
    vlm_block, err = _run_optional_move_vlm(images_png, extra, user_prompt, model)
    if err:
        return err
    assert vlm_block is not None

    photo_items = vlm_block["from_photos"]
    merged = _merge_lines(photo_items, manual_items)
    quote = _compute_quote(merged, extra)

    doc = _build_doc("이사 견적(초안)", user_prompt, extra, vlm_block,
                     merged, quote, thumbs_b64, model)
    out_fmt = (
        request.form.get("output") or request.args.get("output") or "json"
    ).strip().lower()
    if out_fmt == "pdf":
        return _pdf_response(doc)
    return jsonify(doc)


@move_bp.post("/api/estimate_from_rmc")
def move_estimate_from_rmc_api():
    model = FIXED_MOVE_MODEL
    mode = (request.form.get("mode") or "compare").strip().lower()
    if mode not in {"compare", "merge"}:
        return jsonify({"error": "mode must be `compare` or `merge`."}), 400

    extra, err = _parse_extra_form()
    if err:
        return err
    assert extra is not None

    summary, err = _read_json_upload("summary_json", required=False)
    if err:
        return err
    summary = summary or {}
    viz, err = _read_json_upload("viz_json", required=False)
    if err:
        return err

    manual_items = _normalize_manual_items(extra.get("items"))
    quote_csv = _parse_rmc_quote_csv()
    if not quote_csv:
        return jsonify({"error": "Missing required `quote_csv`."}), 400
    quote_items = _items_from_quote_csv(quote_csv)
    if not quote_items:
        return jsonify({"error": "`quote_csv` does not contain any billable line items."}), 400
    summary_items = _items_from_rmc_summary(summary)
    viz_summary = _summarize_rmc_viz(viz)

    images_png, thumbs_b64, err = _prepare_uploaded_images()
    if err:
        return err

    user_prompt = (request.form.get("user_prompt") or "").strip()
    context_extra = dict(extra)
    context_extra["estimate_assist_data"] = {
        "quote_candidates": [
            {
                "id": str(item["id"]),
                "name": item["name"],
                "estimated_volume_m3": item["estimated_volume_m3"],
                "quote_subtotal": item.get("quote_subtotal"),
                "note": item.get("quote_note"),
            }
            for item in quote_items
        ],
        "summary": {
            "input": summary.get("input"),
            "n_points": summary.get("n_points"),
            "n_instances": summary.get("n_instances"),
            "quote": summary.get("quote"),
        },
    }
    vlm_block, err = _run_optional_move_vlm(
        images_png,
        context_extra,
        user_prompt,
        model,
    )
    if err:
        return err
    assert vlm_block is not None
    visible_quote_items, quote_filter, err = _filter_quote_items_with_vlm(
        quote_items,
        images_png,
        extra,
        user_prompt,
        model,
    )
    if err:
        return err

    photo_items = vlm_block["from_photos"]
    base_items = visible_quote_items if quote_items else summary_items
    lines = _merge_lines(photo_items, base_items + manual_items)
    quote = _compute_quote(lines, extra)
    if quote_filter.get("summary_ko"):
        vlm_summary = vlm_block.get("summary_ko", "")
        vlm_block["summary_ko"] = (
            f"{vlm_summary} / 보조 데이터 필터링: {quote_filter['summary_ko']}"
            if vlm_summary else quote_filter["summary_ko"]
        )
    doc = _build_doc("견적 보조 데이터 기반 이사 견적", user_prompt, extra, vlm_block,
                     lines, quote, thumbs_b64, model)
    doc["integration"] = {
        "source": "estimate_assist_data",
        "mode": mode,
        "pricing_policy": (
            "quote.csv is the primary candidate list; VLM keeps only candidates "
            "that are visible in the uploaded photos before vol_est recomputes the total."
        ),
    }
    doc["assist_data"] = {
        "input": summary.get("input"),
        "n_points": summary.get("n_points"),
        "n_instances": summary.get("n_instances"),
        "summary_quote": summary.get("quote"),
        "quote_csv": quote_csv,
        "quote_filter": quote_filter,
        "visualization": viz_summary,
    }

    out_fmt = (
        request.form.get("output") or request.args.get("output") or "json"
    ).strip().lower()
    if out_fmt == "pdf":
        return _pdf_response(doc)
    return jsonify(doc)


@move_bp.post("/api/pdf_from_doc")
def move_pdf_from_doc_api():
    doc = request.get_json(silent=True)
    if not isinstance(doc, dict):
        return jsonify({"error": "Request body must be a JSON estimate document."}), 400
    return _pdf_response(doc)
