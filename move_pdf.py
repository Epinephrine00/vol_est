"""견적서 PDF (한글 폰트: 시스템 경로 또는 MOVE_PDF_FONT_TTF)."""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_KR_FONT_NAME: str | None = None


def _font_candidates() -> list[tuple[str, int]]:
    """(path, subfontIndex for .ttc)."""
    env = os.environ.get("MOVE_PDF_FONT_TTF", "").strip()
    out: list[tuple[str, int]] = []
    if env and Path(env).is_file():
        idx = int(os.environ.get("MOVE_PDF_TTC_INDEX", "0"))
        out.append((env, idx))
    try:
        idx = int(os.environ.get("MOVE_PDF_TTC_INDEX", "1"))
    except ValueError:
        idx = 1
    for p in (
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        if Path(p).is_file():
            sub = idx if p.endswith(".ttc") else 0
            out.append((p, sub))
    return out


def _ensure_korean_font() -> str:
    """Returns ReportLab font name (TT or built-in Helvetica fallback)."""
    global _KR_FONT_NAME
    if _KR_FONT_NAME is not None:
        return _KR_FONT_NAME
    reg = "MoveKrFont"
    for path, sub in _font_candidates():
        try:
            pdfmetrics.registerFont(TTFont(reg, path, subfontIndex=sub))
            _KR_FONT_NAME = reg
            return reg
        except Exception:
            continue
    _KR_FONT_NAME = "Helvetica"
    return "Helvetica"


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    esc = (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return Paragraph(esc, style)


def build_estimate_pdf(doc: dict[str, Any]) -> bytes:
    font = _ensure_korean_font()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "T",
        parent=styles["Heading1"],
        fontName=font,
        fontSize=18,
        leading=22,
        spaceAfter=12,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName=font,
        fontSize=12,
        leading=15,
        spaceBefore=10,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "B",
        parent=styles["Normal"],
        fontName=font,
        fontSize=9,
        leading=12,
    )
    small = ParagraphStyle(
        "S",
        parent=styles["Normal"],
        fontName=font,
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#555555"),
    )

    buf = io.BytesIO()
    story: list[Any] = []
    doc_pdf = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
        title="이사견적",
    )

    story.append(_p(doc.get("title") or "이사 견적(초안)", title_style))
    story.append(Spacer(1, 0.4 * cm))

    g = doc.get("generated_for") or {}
    meta_rows = [
        ["성함", str(g.get("customer_name") or "—")],
        ["이사일", str(g.get("move_date") or "—")],
        ["출발", str(g.get("origin_address") or "—")],
        ["도착", str(g.get("dest_address") or "—")],
        ["비고", str(g.get("special_notes") or "—")],
    ]
    t_meta = Table(meta_rows, colWidths=[3 * cm, 12 * cm])
    t_meta.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), font, 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
            ]
        )
    )
    story.append(t_meta)
    story.append(Spacer(1, 0.3 * cm))

    vlm = doc.get("vlm") or {}
    story.append(_p("VLM 요약", h2))
    story.append(_p(str(vlm.get("summary_ko") or "—"), body))
    up = doc.get("user_prompt_sent")
    if isinstance(up, str) and up.strip():
        story.append(_p("사용자 프롬프트", h2))
        story.append(_p(up.strip(), body))

    lines = doc.get("lines") or []
    story.append(_p("품목", h2))
    table_data: list[list[str]] = [
        ["출처", "이름", "부피(㎥)", "수량", "방/신뢰도"],
    ]
    for ln in lines:
        table_data.append(
            [
                str(ln.get("source", "")),
                str(ln.get("name", "")),
                str(ln.get("estimated_volume_m3", "")),
                str(ln.get("qty", "")),
                f'{ln.get("room_hint", "")} / {ln.get("confidence", "")}',
            ]
        )
    tw = [2 * cm, 5 * cm, 2.2 * cm, 1.5 * cm, 4.3 * cm]
    t_lines = Table(table_data, colWidths=tw, repeatRows=1)
    t_lines.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), font, 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(t_lines)

    q = doc.get("quote") or {}

    def _won(v: Any) -> str:
        if isinstance(v, int):
            return f"{v:,} 원"
        if isinstance(v, float) and v == int(v):
            return f"{int(v):,} 원"
        return "—" if v is None else str(v)

    story.append(_p("요금 산출", h2))
    fee_rows = [
        ["기본료", _won(q.get("base_fee"))],
        ["부피 합계(㎥)", str(q.get("volume_m3", "—"))],
        ["부피요금", _won(q.get("volume_fee"))],
        ["거리(km)", str(q.get("distance_km", "—"))],
        ["거리요금", _won(q.get("distance_fee"))],
        ["층/엘리베이터 추가", _won(q.get("floor_surcharge"))],
        ["총 견적", _won(q.get("total_ex_tax"))],
    ]
    t_fee = Table(fee_rows, colWidths=[5 * cm, 10 * cm])
    t_fee.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), font, 9),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#fff8e6")),
            ]
        )
    )
    story.append(t_fee)
    story.append(Spacer(1, 0.35 * cm))
    story.append(_p(f"모델: {doc.get('model_used', '')}", small))

    max_thumbs = int(os.environ.get("MOVE_PDF_MAX_THUMBS", "12"))
    thumbs_b64 = doc.get("previews_base64") or []
    if thumbs_b64 and max_thumbs > 0:
        story.append(_p("첨부 사진(일부)", h2))
        row: list[Any] = []
        w_img = 3.2 * cm
        for i, b64 in enumerate(thumbs_b64[:max_thumbs]):
            try:
                raw = base64.b64decode(b64)
                im_buf = io.BytesIO(raw)
                img = RLImage(im_buf, width=w_img, height=w_img * 0.75)
                row.append(img)
                if len(row) >= 3:
                    story.append(Table([row], colWidths=[w_img] * 3))
                    row = []
            except Exception:
                continue
        if row:
            while len(row) < 3:
                row.append(Spacer(w_img, 1))
            story.append(Table([row], colWidths=[w_img] * 3))

    doc_pdf.build(story)
    return buf.getvalue()


def pdf_attachment_headers(filename: str = "견적서.pdf") -> dict[str, str]:
    ascii_name = "estimate.pdf"
    star = "UTF-8''" + quote(filename)
    return {
        "Content-Type": "application/pdf",
        "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*={star}',
    }
