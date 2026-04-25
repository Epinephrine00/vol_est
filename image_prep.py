"""Decode uploads, apply EXIF orientation, optional downscale; return PNG bytes + dimensions."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps


def prepare_image(file_bytes: bytes, max_edge: int) -> tuple[bytes, int, int]:
    im = Image.open(BytesIO(file_bytes))
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "P"):
        im = im.convert("RGB")
    elif im.mode != "RGB":
        im = im.convert("RGB")

    w, h = im.size
    longest = max(w, h)
    if longest > max_edge and max_edge > 0:
        scale = max_edge / float(longest)
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        im = im.resize((nw, nh), Image.Resampling.LANCZOS)
        w, h = im.size

    out = BytesIO()
    im.save(out, format="PNG", optimize=True)
    return out.getvalue(), w, h
