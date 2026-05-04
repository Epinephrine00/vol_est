"""VLM call for moving-inventory from one or more room photos."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from ollama import Client, ResponseError

MOVE_INVENTORY_PROMPT = """You are helping create a household moving inventory for South Korea.

The user attached one or more photos of rooms (living room, bedroom, etc.). List bulky furniture and appliances you can identify.

Output ONLY a JSON object (no markdown fences, no extra text), with this exact shape:
{
  "from_photos": [
    {
      "name": "<short Korean or English name>",
      "estimated_volume_m3": <positive number, your rough guess in cubic meters>,
      "qty": <positive integer, default 1>,
      "confidence": "high" | "medium" | "low",
      "room_hint": "<which room if inferable, else unknown>"
    }
  ],
  "summary_ko": "<one sentence Korean summary of what you saw>"
}

Rules:
- If no photo is usable or nothing is visible, return "from_photos": [] and a short summary_ko explaining that.
- estimated_volume_m3 must be realistic orders of magnitude (e.g. small chair ~0.05, wardrobe ~1.5, fridge ~0.6).
- Do not duplicate the same object unless clearly multiple instances.
- After this block you receive USER_CONTEXT (JSON) and optionally USER_PROMPT (free text). Follow explicit user instructions when listing items or volumes if they are reasonable and still valid JSON output.
"""

QUOTE_FILTER_PROMPT = """You are validating a moving quote against room photos.

The user attached one or more photos and a quote CSV has already produced candidate line items.
Your job is NOT to create a new quote. Your job is to filter the candidate quote items:
keep only items that are actually visible in the photos or are clearly the same visible object.

Output ONLY a JSON object (no markdown fences, no extra text), with this exact shape:
{
  "visible_ids": ["<candidate id>", "..."],
  "excluded": [
    { "id": "<candidate id>", "reason_ko": "<short Korean reason>" }
  ],
  "summary_ko": "<one sentence Korean summary of the filtering result>"
}

Rules:
- Candidate ids come from USER_CONTEXT.quote_candidates[].id. Return ids exactly as strings.
- Do not add new items. Only decide whether each quote candidate is visible.
- If a candidate is ambiguous or not clearly visible, exclude it.
- If several identical small items are visible but quantity is uncertain, keep the candidate if at least one matching object is visible.
- Use the candidate label/name and the photos together; do not trust the CSV blindly.
"""


def extract_json_object(text: str) -> str:
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if m:
            text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model output")
    return text[start : end + 1]


def parse_inventory(raw: str) -> dict[str, Any]:
    snippet = extract_json_object(raw)
    data = json.loads(snippet)
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object")
    photos = data.get("from_photos")
    if photos is None:
        photos = []
    if not isinstance(photos, list):
        photos = []
    cleaned = []
    for i, item in enumerate(photos):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name or not isinstance(name, str):
            continue
        name = name.strip() or f"item_{i}"
        try:
            vol = float(item.get("estimated_volume_m3", 0) or 0)
        except (TypeError, ValueError):
            vol = 0.05
        vol = max(0.001, vol)
        try:
            qty = int(item.get("qty", 1) or 1)
        except (TypeError, ValueError):
            qty = 1
        qty = max(1, qty)
        conf = str(item.get("confidence", "medium")).lower()
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        room = str(item.get("room_hint", "unknown"))
        cleaned.append(
            {
                "name": name,
                "estimated_volume_m3": round(vol, 4),
                "qty": qty,
                "confidence": conf,
                "room_hint": room,
                "source": "photo_vlm",
            }
        )
    summary = data.get("summary_ko")
    if not isinstance(summary, str):
        summary = ""
    return {"from_photos": cleaned, "summary_ko": summary.strip()}


def parse_quote_filter(raw: str, candidate_ids: set[str]) -> dict[str, Any]:
    snippet = extract_json_object(raw)
    data = json.loads(snippet)
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object")

    visible_raw = data.get("visible_ids") or []
    if not isinstance(visible_raw, list):
        visible_raw = []
    visible_ids = []
    seen = set()
    for raw_id in visible_raw:
        item_id = str(raw_id).strip()
        if item_id in candidate_ids and item_id not in seen:
            seen.add(item_id)
            visible_ids.append(item_id)

    excluded = []
    excluded_raw = data.get("excluded") or []
    if isinstance(excluded_raw, list):
        for item in excluded_raw:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", "")).strip()
            if item_id not in candidate_ids:
                continue
            excluded.append(
                {
                    "id": item_id,
                    "reason_ko": str(item.get("reason_ko") or "사진에서 확인되지 않음").strip(),
                }
            )

    summary = data.get("summary_ko")
    if not isinstance(summary, str):
        summary = ""
    return {
        "visible_ids": visible_ids,
        "excluded": excluded,
        "summary_ko": summary.strip(),
    }


def run_move_inventory(
    host: str,
    model: str,
    images_png: list[bytes],
    user_context: str,
    timeout: float,
) -> str:
    try:
        cap = int(os.environ.get("MOVE_VLM_CONTEXT_CHARS", "16000"))
    except ValueError:
        cap = 16000
    cap = max(2000, min(cap, 100000))
    ctx = (user_context or "").strip()[:cap]
    content = (
        MOVE_INVENTORY_PROMPT
        + "\n\nUSER_CONTEXT (JSON + optional USER_PROMPT section below):\n"
        + ctx
    )
    client = Client(host=host, timeout=timeout)
    try:
        response = client.chat(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": content,
                    "images": images_png if images_png else [],
                }
            ],
            options={
                "temperature": 0.25,
                "num_ctx": int(os.environ.get("MOVE_NUM_CTX", "16384")),
            },
            stream=False,
        )
    except ResponseError as e:
        raise RuntimeError(getattr(e, "error", None) or str(e)) from e
    except ConnectionError as e:
        raise RuntimeError(
            "Cannot connect to Ollama. Is the daemon running? "
            f"Expected host: {host!r}"
        ) from e
    except OSError as e:
        raise RuntimeError(f"Ollama request failed: {e}") from e

    if isinstance(response, dict):
        msg = response.get("message") or {}
        return (msg.get("content") or "").strip()
    msg = getattr(response, "message", None)
    if msg is None:
        return ""
    return (getattr(msg, "content", None) or "").strip()


def run_quote_filter(
    host: str,
    model: str,
    images_png: list[bytes],
    user_context: str,
    timeout: float,
) -> str:
    try:
        cap = int(os.environ.get("MOVE_VLM_CONTEXT_CHARS", "16000"))
    except ValueError:
        cap = 16000
    cap = max(2000, min(cap, 100000))
    ctx = (user_context or "").strip()[:cap]
    content = (
        QUOTE_FILTER_PROMPT
        + "\n\nUSER_CONTEXT (JSON + optional USER_PROMPT section below):\n"
        + ctx
    )
    client = Client(host=host, timeout=timeout)
    try:
        response = client.chat(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": content,
                    "images": images_png if images_png else [],
                }
            ],
            options={
                "temperature": 0.1,
                "num_ctx": int(os.environ.get("MOVE_NUM_CTX", "16384")),
            },
            stream=False,
        )
    except ResponseError as e:
        raise RuntimeError(getattr(e, "error", None) or str(e)) from e
    except ConnectionError as e:
        raise RuntimeError(
            "Cannot connect to Ollama. Is the daemon running? "
            f"Expected host: {host!r}"
        ) from e
    except OSError as e:
        raise RuntimeError(f"Ollama request failed: {e}") from e

    if isinstance(response, dict):
        msg = response.get("message") or {}
        return (msg.get("content") or "").strip()
    msg = getattr(response, "message", None)
    if msg is None:
        return ""
    return (getattr(msg, "content", None) or "").strip()
