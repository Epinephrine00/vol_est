"""Run /move-style estimates from a JSONL manifest.

Example:
    python batch_move_estimate.py --input cases.jsonl --output results.jsonl

Each input line is one case:
    {"case_id":"room_001","images":["a.jpg","b.png"],"extra":{"items":[]},"user_prompt":"optional"}

Relative paths are resolved from the manifest file's directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from move_estimate import (
    FIXED_MOVE_MODEL,
    MoveEstimateError,
    estimate_move_from_files,
    estimate_move_from_rmc_files,
)

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


def _load_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _resolve_path(raw_path: str, base_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def _load_case(line: str, line_no: int) -> dict[str, Any]:
    try:
        case = json.loads(line)
    except json.JSONDecodeError as e:
        raise ValueError(f"line {line_no}: invalid JSON: {e}") from e
    if not isinstance(case, dict):
        raise ValueError(f"line {line_no}: each JSONL row must be an object")
    return case


def _case_extra(case: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    raw_extra_path = case.get("extra_json_path")
    if raw_extra_path:
        if not isinstance(raw_extra_path, str):
            raise ValueError("extra_json_path must be a string")
        extra.update(_load_json_object(_resolve_path(raw_extra_path, base_dir)))

    raw_extra = case.get("extra")
    if raw_extra is not None:
        if not isinstance(raw_extra, dict):
            raise ValueError("extra must be a JSON object")
        extra.update(raw_extra)
    return extra


def _case_images(case: dict[str, Any], base_dir: Path) -> list[Path]:
    raw_images = case.get("images", [])
    if not isinstance(raw_images, list):
        raise ValueError("images must be an array")

    paths: list[Path] = []
    for raw_path in raw_images:
        if not isinstance(raw_path, str):
            raise ValueError("every images entry must be a string path")
        paths.append(_resolve_path(raw_path, base_dir))
    return paths


def _case_optional_json(
    case: dict[str, Any],
    base_dir: Path,
    field_name: str,
) -> dict[str, Any] | None:
    raw_path = case.get(field_name)
    if not raw_path:
        return None
    if not isinstance(raw_path, str):
        raise ValueError(f"{field_name} must be a string")
    return _load_json_object(_resolve_path(raw_path, base_dir))


def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    quote = result.get("quote") if isinstance(result.get("quote"), dict) else {}
    vlm = result.get("vlm") if isinstance(result.get("vlm"), dict) else {}
    assist_data = (
        result.get("assist_data") if isinstance(result.get("assist_data"), dict) else None
    )
    summary: dict[str, Any] = {
        "title": result.get("title", ""),
        "model_used": result.get("model_used", ""),
        "volume_m3": quote.get("volume_m3"),
        "total_ex_tax": quote.get("total_ex_tax"),
        "currency": quote.get("currency"),
        "quote": quote,
        "lines": result.get("lines", []),
        "vlm_summary_ko": vlm.get("summary_ko", ""),
        "photo_items": vlm.get("from_photos", []),
    }
    if isinstance(result.get("integration"), dict):
        summary["integration"] = result["integration"]
    if assist_data:
        summary["assist_data"] = {
            "summary_quote": assist_data.get("summary_quote"),
            "quote_filter": assist_data.get("quote_filter"),
            "visualization": assist_data.get("visualization"),
        }
    return summary


def _run_case(case: dict[str, Any], line_no: int, base_dir: Path, model: str) -> dict[str, Any]:
    case_id = str(case.get("case_id") or f"line_{line_no}")
    try:
        image_paths = _case_images(case, base_dir)
        extra = _case_extra(case, base_dir)
        user_prompt = str(case.get("user_prompt") or "").strip()
        quote_csv_path = case.get("quote_csv_path")
        if quote_csv_path:
            if not isinstance(quote_csv_path, str):
                raise ValueError("quote_csv_path must be a string")
            result = estimate_move_from_rmc_files(
                image_paths,
                _resolve_path(quote_csv_path, base_dir),
                extra=extra,
                summary=_case_optional_json(case, base_dir, "summary_json_path"),
                viz=_case_optional_json(case, base_dir, "viz_json_path"),
                user_prompt=user_prompt,
                model=model,
                mode=str(case.get("assist_mode") or "compare"),
            )
        else:
            result = estimate_move_from_files(
                image_paths,
                extra=extra,
                user_prompt=user_prompt,
                model=model,
            )
        return {
            "case_id": case_id,
            "ok": True,
            "result": _summarize_result(result),
        }
    except MoveEstimateError as e:
        return {
            "case_id": case_id,
            "ok": False,
            "status_code": e.status_code,
            "error": e.payload,
        }
    except Exception as e:
        return {
            "case_id": case_id,
            "ok": False,
            "status_code": 500,
            "error": {"error": str(e)},
        }


def run_batch(input_path: Path, output_path: Path, model: str) -> dict[str, int]:
    base_dir = input_path.resolve().parent
    counts = {"total": 0, "ok": 0, "failed": 0}
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8-sig") as src, output_path.open(
        "w",
        encoding="utf-8",
    ) as dst:
        for line_no, raw_line in enumerate(src, start=1):
            line = raw_line.strip()
            if not line:
                continue
            counts["total"] += 1
            try:
                case = _load_case(line, line_no)
                row = _run_case(case, line_no, base_dir, model)
            except Exception as e:
                row = {
                    "case_id": f"line_{line_no}",
                    "ok": False,
                    "status_code": 400,
                    "error": {"error": str(e)},
                }

            if row["ok"]:
                counts["ok"] += 1
            else:
                counts["failed"] += 1
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
            dst.flush()
    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run /move/api/estimate logic for each case in a JSONL manifest.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Input JSONL manifest")
    parser.add_argument("--output", required=True, type=Path, help="Output JSONL path")
    parser.add_argument(
        "--model",
        default=FIXED_MOVE_MODEL,
        help=f"Ollama model name. Default: {FIXED_MOVE_MODEL}",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    counts = run_batch(args.input, args.output, args.model.strip() or FIXED_MOVE_MODEL)
    print(
        f"Wrote {counts['total']} rows to {args.output} "
        f"({counts['ok']} ok, {counts['failed']} failed)."
    )
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
