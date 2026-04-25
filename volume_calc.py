"""3D extent volume index and optional metric volume from axis calibration."""

from __future__ import annotations

from typing import Any


def bbox_pixels(
    bbox_xyxy: list[float], image_width: int, image_height: int
) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox_xyxy
    w_px = max(0.0, (x2 - x1) * image_width)
    h_px = max(0.0, (y2 - y1) * image_height)
    return w_px, h_px


def volume_index_extent(extent_xyz: list[float]) -> float:
    """Unitless product ex * ey * ez (VLM relative 3D AABB volume)."""
    ex, ey, ez = extent_xyz
    return max(0.0, ex) * max(0.0, ey) * max(0.0, ez)


def parse_axis(axis: Any) -> int:
    if isinstance(axis, int):
        if axis in (0, 1, 2):
            return axis
    if isinstance(axis, str):
        a = axis.strip().lower()
        if a in ("0", "x", "ex"):
            return 0
        if a in ("1", "y", "ey"):
            return 1
        if a in ("2", "z", "ez"):
            return 2
    raise ValueError('axis must be 0,1,2 or "x","y","z"')


def cm_per_model_unit(
    extent_xyz: list[float],
    axis_idx: int,
    cm: float,
) -> float:
    if cm <= 0:
        raise ValueError("cm must be positive")
    ev = extent_xyz[axis_idx]
    if ev <= 0:
        raise ValueError("extent along calibration axis must be positive")
    return cm / ev


def volume_cm3_from_extent(extent_xyz: list[float], s_cm_per_unit: float) -> float:
    """Isotropic scale: one model unit = s_cm_per_unit cm along each axis."""
    ex, ey, ez = extent_xyz
    if s_cm_per_unit <= 0:
        return 0.0
    s = s_cm_per_unit
    return (ex * s) * (ey * s) * (ez * s)


def enrich_detections(
    detections: list[dict[str, Any]],
    image_width: int,
    image_height: int,
    calibration: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    s_cm: float | None = None
    if calibration:
        idx = int(calibration["box_index"])
        cm = float(calibration["cm"])
        axis_idx = parse_axis(calibration["axis"])
        if idx < 0 or idx >= len(detections):
            raise ValueError("box_index out of range")
        s_cm = cm_per_model_unit(detections[idx]["extent_xyz"], axis_idx, cm)

    for d in detections:
        w_px, h_px = bbox_pixels(d["bbox_xyxy"], image_width, image_height)
        ext = d["extent_xyz"]
        row = {
            "label": d["label"],
            "bbox_xyxy": d["bbox_xyxy"],
            "extent_xyz": ext,
            "width_px": round(w_px, 2),
            "height_px": round(h_px, 2),
            "volume_index": round(volume_index_extent(ext), 6),
            "volume_cm3": None,
        }
        if s_cm is not None:
            row["volume_cm3"] = round(volume_cm3_from_extent(ext, s_cm), 4)
        out.append(row)
    return out
