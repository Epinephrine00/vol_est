"""VLM call for moving-inventory from one or more room photos."""

from __future__ import annotations

import json
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


def run_move_inventory(
    host: str,
    model: str,
    images_png: list[bytes],
    user_context: str,
    timeout: float,
) -> str:
    ctx = (user_context or "").strip()[:8000]
    content = MOVE_INVENTORY_PROMPT + "\n\nUSER_CONTEXT (structured notes from user, may be empty):\n" + ctx
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
            options={"temperature": 0.25, "num_ctx": 8192},
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
