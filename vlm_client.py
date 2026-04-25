"""Call Ollama vision chat and parse 3D-oriented detections (extent + 2D projection)."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from ollama import Client, ResponseError

DETECTOR_PROMPT = """You are a 3D-aware object detector for a single RGB photograph.

For each distinct physical object, estimate:
1) A tight 2D axis-aligned bounding box on the image (projection).
2) A 3D axis-aligned extent in **relative unitless** coordinates: how wide, tall, and deep the object is compared to itself (consistent scale within this image only).

Output ONLY a JSON array. No markdown fences, no commentary before or after.

Each array element must be exactly:
{"label": "<short name>", "bbox_xyxy": [x1, y1, x2, y2], "extent_xyz": [ex, ey, ez]}

Rules for bbox_xyxy (normalized 0 to 1):
- x is horizontal (0 = left, 1 = right), y is vertical (0 = top, 1 = bottom).
- (x1,y1) top-left, (x2,y2) bottom-right, 0 <= x1 < x2 <= 1, 0 <= y1 < y2 <= 1.

Rules for extent_xyz [ex, ey, ez] (all strictly positive floats):
- ex: extent along camera-right (object "width" in the scene).
- ey: extent along camera-down (object "height" in the scene).
- ez: extent along the depth axis (camera viewing direction, into the scene).
- Use the same arbitrary unit for all objects in this image; only ratios matter before user calibration.
- If depth is very uncertain, still give your best positive guess for ez (do not use zero).

If there are no objects, output: []
"""


def extract_json_array(text: str) -> str:
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if m:
            text = m.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON array found in model output")
    return text[start : end + 1]


def _fallback_extent(x1: float, y1: float, x2: float, y2: float) -> list[float]:
    ex = max(x2 - x1, 1e-6)
    ey = max(y2 - y1, 1e-6)
    ez = math.sqrt(ex * ey)
    return [ex, ey, ez]


def parse_detections(raw: str) -> list[dict[str, Any]]:
    snippet = extract_json_array(raw)
    data = json.loads(snippet)
    if not isinstance(data, list):
        raise ValueError("Top-level JSON must be an array")
    cleaned: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        box = item.get("bbox_xyxy")
        if label is None or not isinstance(label, str):
            continue
        label = label.strip() or f"object_{i}"
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        except (TypeError, ValueError):
            continue
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        if any(v < 0 or v > 1 for v in (x1, y1, x2, y2)):
            continue
        if x2 - x1 < 1e-6 or y2 - y1 < 1e-6:
            continue

        ext = item.get("extent_xyz")
        extent_xyz: list[float]
        if isinstance(ext, (list, tuple)) and len(ext) == 3:
            try:
                ex, ey, ez = float(ext[0]), float(ext[1]), float(ext[2])
            except (TypeError, ValueError):
                extent_xyz = _fallback_extent(x1, y1, x2, y2)
            else:
                if ex <= 0 or ey <= 0 or ez <= 0:
                    extent_xyz = _fallback_extent(x1, y1, x2, y2)
                else:
                    extent_xyz = [ex, ey, ez]
        else:
            extent_xyz = _fallback_extent(x1, y1, x2, y2)

        cleaned.append(
            {
                "label": label,
                "bbox_xyxy": [x1, y1, x2, y2],
                "extent_xyz": extent_xyz,
            }
        )
    return cleaned


def chat_message_content(response: Any) -> str:
    if isinstance(response, dict):
        msg = response.get("message") or {}
        return (msg.get("content") or "").strip()
    msg = getattr(response, "message", None)
    if msg is None:
        return ""
    return (getattr(msg, "content", None) or "").strip()


def run_detection(
    host: str,
    model: str,
    image_png: bytes,
    timeout: float,
) -> str:
    client = Client(host=host, timeout=timeout)
    try:
        response = client.chat(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": DETECTOR_PROMPT,
                    "images": [image_png],
                }
            ],
            options={"temperature": 0.2, "num_ctx": 4096},
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

    return chat_message_content(response)
