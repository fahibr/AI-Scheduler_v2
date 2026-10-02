"""Convert door-schedule PDF pages into images for vision extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import Iterator

import pymupdf
from PIL import Image


@dataclass(frozen=True)
class PageImage:
    page_number: int  # 1-based within its PDF
    image: Image.Image
    png_bytes: bytes
    source_name: str = ""


def parse_page_selection(spec: str) -> list[int] | None:
    """
    Parse a page selection string into sorted unique 1-based page numbers.

    Examples:
      "" or "all"  -> None (all pages)
      "1-6"        -> [1, 2, 3, 4, 5, 6]
      "1,8,10"     -> [1, 8, 10]
      "1-3,8,10"   -> [1, 2, 3, 8, 10]

    Raises ValueError on invalid tokens.
    """
    text = (spec or "").strip()
    if not text or text.lower() in {"all", "*", "0"}:
        return None

    pages: set[int] = set()
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            ends = [p.strip() for p in part.split("-", 1)]
            if len(ends) != 2 or not ends[0] or not ends[1]:
                raise ValueError(f"Invalid page range: '{part}'")
            if not ends[0].isdigit() or not ends[1].isdigit():
                raise ValueError(f"Invalid page range: '{part}'")
            start, end = int(ends[0]), int(ends[1])
            if start < 1 or end < 1:
                raise ValueError("Page numbers must be >= 1.")
            if end < start:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            if not re.fullmatch(r"\d+", part):
                raise ValueError(f"Invalid page number: '{part}'")
            number = int(part)
            if number < 1:
                raise ValueError("Page numbers must be >= 1.")
            pages.add(number)

    if not pages:
        return None
    return sorted(pages)


def pdf_page_count(pdf_bytes: bytes) -> int:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return len(doc)


def pdf_bytes_to_page_images(
    pdf_bytes: bytes,
    *,
    dpi: int = 200,
    max_pages: int | None = None,
    page_numbers: list[int] | None = None,
    source_name: str = "",
) -> list[PageImage]:
    """Rasterize selected PDF pages to PNGs suitable for vision models."""
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    pages: list[PageImage] = []

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        total = len(doc)
        if page_numbers is not None:
            selected = [n for n in page_numbers if 1 <= n <= total]
        else:
            limit = total if max_pages is None else min(total, max_pages)
            selected = list(range(1, limit + 1))

        for page_number in selected:
            page = doc[page_number - 1]
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            png_bytes = pixmap.tobytes("png")
            image = Image.open(BytesIO(png_bytes)).convert("RGB")
            pages.append(
                PageImage(
                    page_number=page_number,
                    image=image,
                    png_bytes=png_bytes,
                    source_name=source_name,
                )
            )
    return pages


def iter_page_previews(pages: list[PageImage]) -> Iterator[tuple[int, Image.Image]]:
    for page in pages:
        yield page.page_number, page.image


def pdf_page_texts(
    pdf_bytes: bytes,
    *,
    max_pages: int | None = None,
    page_numbers: list[int] | None = None,
) -> dict[int, str]:
    """Return 1-based page number → embedded text layer (helps exact mm values)."""
    texts: dict[int, str] = {}
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        total = len(doc)
        if page_numbers is not None:
            selected = [n for n in page_numbers if 1 <= n <= total]
        else:
            limit = total if max_pages is None else min(total, max_pages)
            selected = list(range(1, limit + 1))
        for page_number in selected:
            texts[page_number] = doc[page_number - 1].get_text() or ""
    return texts
