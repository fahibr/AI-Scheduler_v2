"""Vision agent that extracts door type and dimensions from schedule images."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from dotenv import load_dotenv
from openai import AzureOpenAI

from .pdf_images import (
    PageImage,
    parse_page_selection,
    pdf_bytes_to_page_images,
    pdf_page_count,
    pdf_page_texts,
)
from .prompts import load_prompts, resolve_prompt_version_for_format
from .schedule_formats import (
    SCHEDULE_FORMAT_STRUCTURED_TABLE,
    normalize_schedule_format,
    schedule_format_label,
)
from .text_extract import (
    extract_doors_from_page_text,
    extract_remarks,
    extract_specification,
    fire_rating_from_description,
    merge_door_records,
    parse_level_quantities,
    sanitize_fire_rating,
    sanitize_materials,
)

load_dotenv()


ProgressCallback = Callable[["ProgressEvent"], None]


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )

    def __iadd__(self, other: "TokenUsage") -> "TokenUsage":
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        return self


@dataclass
class ProgressEvent:
    """Structured progress update for UI / CLI consumers."""

    message: str
    stage: str = "info"  # start|convert|extract|refine|page_done|file|done
    current: int | None = None  # 1-based completed/current page index across job
    total: int | None = None  # total pages in job
    page_number: int | None = None  # page number within the PDF
    source_name: str | None = None
    doors_found: int | None = None
    file_index: int | None = None
    file_count: int | None = None
    prompt_tokens: int = 0  # tokens for this update/page
    completion_tokens: int = 0
    prompt_tokens_total: int = 0  # cumulative across the job
    completion_tokens_total: int = 0


def _usage_from_response(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )


def _emit(on_progress: ProgressCallback | None, event: ProgressEvent) -> None:
    if on_progress:
        on_progress(event)


def _selected_pages_for_pdf(
    pdf_bytes: bytes,
    page_numbers: list[int] | None,
) -> list[int]:
    total = pdf_page_count(pdf_bytes)
    if page_numbers is None:
        return list(range(1, total + 1))
    return [n for n in page_numbers if 1 <= n <= total]


def count_extract_pages(
    pdf_files: list[tuple[str, bytes]],
    page_numbers: list[int] | None,
) -> int:
    return sum(len(_selected_pages_for_pdf(data, page_numbers)) for _, data in pdf_files)



def _client() -> AzureOpenAI:
    endpoint = os.getenv("ENDPOINT_URL", "").rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    if not endpoint or not api_key:
        raise RuntimeError(
            "Missing Azure OpenAI settings. Set ENDPOINT_URL and AZURE_OPENAI_API_KEY in .env."
        )
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )


def _deployment() -> str:
    name = os.getenv("DEPLOYMENT_NAME", "").strip()
    if not name:
        raise RuntimeError("Missing DEPLOYMENT_NAME in .env.")
    return name


def _min_interval() -> float:
    try:
        return float(os.getenv("DOOR_API_MIN_INTERVAL", "2.0"))
    except ValueError:
        return 2.0


def _encode_png(png_bytes: bytes) -> str:
    return base64.b64encode(png_bytes).decode("ascii")


def _parse_json_payload(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _to_mm(value: Any) -> int | None:
    """Parse integers from values like 1100, '1100', '1100mm', '1,100 mm'."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(round(float(value)))
    match = re.search(r"(\d+(?:\.\d+)?)", str(value).replace(",", ""))
    if not match:
        return None
    return int(round(float(match.group(1))))


_DOOR_TYPE_RE = re.compile(
    r"^(?:"
    r"[A-Z]{1,4}\d{1,4}[A-Z]{0,3}"  # FD1, D10A, D10GA, D10LA, TD13, RS1
    r"|(?:FD|SD|GD|MD|AD|TD|LD|PD|DT|DR|WD|RS|FRS)[A-Z]{1,3}"  # FDGA (letter-only type codes)
    r"|TYPE[-_]?\d{1,3}"
    r")$"
)


def _format_quantity_by_level(raw: Any) -> str | None:
    """Normalize quantity_by_level from dict/list/str into 'Level: n; …' text."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        parts = [
            f"{k}: {v}"
            for k, v in raw.items()
            if v is not None and str(v).strip() != ""
        ]
        return "; ".join(parts) if parts else None
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                label = item.get("level") or item.get("label") or item.get("name")
                count = item.get("quantity") or item.get("qty") or item.get("count")
                if label is not None and count is not None:
                    parts.append(f"{label}: {count}")
            elif item is not None and str(item).strip():
                parts.append(str(item).strip())
        return "; ".join(parts) if parts else None
    text = str(raw).strip()
    if not text:
        return None
    # Recover stringified Python/JSON dicts from models or prior bad normalizes
    if text.startswith("{") and text.endswith("}"):
        parsed: Any = None
        try:
            import ast

            parsed = ast.literal_eval(text)
        except Exception:
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
        if isinstance(parsed, dict):
            return _format_quantity_by_level(parsed)
    return text


def _normalize_door(
    raw: dict[str, Any],
    page_number: int,
    *,
    source_pdf: str | None = None,
) -> dict[str, Any] | None:
    door_type = str(raw.get("door_type") or "").strip().upper()
    door_type = re.sub(r"[\s_]+", "", door_type)
    door_type = door_type.replace("TYPE-", "TYPE").replace("TYPE_", "TYPE")
    if not door_type or not _DOOR_TYPE_RE.match(door_type):
        return None

    width = _to_mm(raw.get("width_mm"))
    height = _to_mm(raw.get("height_mm"))
    thickness = _to_mm(raw.get("thickness_mm"))

    # Reject vision-panel sized "doors"
    if width is not None and height is not None and width <= 300 and height <= 1000:
        return None
    # Reject shutter-box / header height mistaken as opening
    if width is not None and height is not None and height <= 400 and width >= 1000:
        return None

    qty = _to_mm(raw.get("quantity"))

    def _text(key: str) -> str | None:
        value = raw.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    source = source_pdf or _text("source_pdf")
    # Format dict/list BEFORE _text(); otherwise str(dict) becomes "{'LEVEL 2': 5}" and sticks.
    qty_by_level = _format_quantity_by_level(raw.get("quantity_by_level"))
    item_number = _extract_item_number(raw)

    description = _text("description")
    door_material, frame_material = sanitize_materials(
        _text("door_material"),
        _text("frame_material"),
        description=description,
    )
    fire_rating = sanitize_fire_rating(
        _normalize_fire_rating(_text("fire_rating")),
        description=description,
    )

    return {
        "door_type": door_type,
        "item_number": item_number,
        "width_mm": width,
        "height_mm": height,
        "thickness_mm": thickness,
        "fire_rating": fire_rating,
        "door_material": door_material,
        "frame_material": frame_material,
        "configuration": _text("configuration"),
        "location": _text("location"),
        "quantity_by_level": qty_by_level,
        "quantity": qty,
        "description": description,
        "remarks": _text("remarks"),
        "page": page_number,
        "source_pdf": source,
    }


# Prefer W x H x thk; also accept W x H (common for roller shutters).
_DIM_RE = re.compile(
    r"(?P<w>\d+)\s*mm\s*\(\s*W\s*\)\s*x\s*(?P<h>\d+)\s*mm\s*\(\s*H\s*\)"
    r"(?:\s*x\s*(?P<t>\d+)\s*mm\s*thk)?",
    re.IGNORECASE,
)
_DIM_XH_RE = re.compile(
    r"(?P<w>\d{3,4})\s*[x×X]\s*(?P<h>\d{3,4})\s*(?:mm)?"
    r"(?:\s*[x×X]\s*(?P<t>\d{2,3})\s*(?:mm)?)?",
    re.IGNORECASE,
)
_DIM_MALAY_RE = re.compile(
    r"(?P<w>\d{3,4})\s*MM\s*\(\s*L\s*\)\s*X\s*(?P<h>\d{3,4})\s*MM\s*\(\s*T\s*\)",
    re.IGNORECASE,
)
_MARK_RE = re.compile(
    r"\b(?P<mark>(?:FRS|FD|SD|RS|TD|PD|AD|MD|GD|WD|DT|DR|LD|D|TYPE)"
    r"[-_]?\d{1,3}[A-Z]?)\b(?![-/])",
    re.IGNORECASE,
)
_TOTAL_RE = re.compile(
    r"(?:TOTAL|BILANGAN|QTY|NOS)\s*[:：]?\s*(?P<qty>\d+)",
    re.IGNORECASE,
)
_LEAF_RE = re.compile(
    r"\b("
    r"single leaf|double leaf|sliding|bi-?folding|bi-?passing|"
    r"roller\s+shutter|fire\s+rated\s+roller\s+shutter|rolling\s+shutter"
    r")\b",
    re.I,
)
_SHUTTER_RE = re.compile(
    r"\b(?:fire\s+rated\s+)?(?:manual\s+)?(?:m\.?\s*s\.?\s+|mild\s+steel\s+)?"
    r"(?:roller|rolling)\s+shutter\b",
    re.IGNORECASE,
)
_DOOR_MATERIAL_RE = re.compile(
    r"\b("
    r"ordinary plywood|"
    r"honeycomb\s+core|"
    r"plywood|"
    r"solid timber|"
    r"timber|"
    r"hollow metal|"
    r"mild\s+steel|"
    r"m\.?\s*s\.?|"
    r"steel|"
    r"aluminium|"
    r"aluminum|"
    r"zincalume|"
    r"FRP|"
    r"HDF|"
    r"glass|"
    r"kayu\s+keras|"
    r"papan\s+lapis"
    r")\b",
    re.IGNORECASE,
)
_FRAME_MATERIAL_RE = re.compile(
    r"\b(metal|aluminium|aluminum|steel|timber|wood|hollow metal|zincalume)\s+frame\b|"
    r"\bbingkai\s+besi\b",
    re.IGNORECASE,
)
_FIRE_RATING_RE = re.compile(
    r"\b(?:"
    r"(?P<hours>\d+(?:\.\d+)?)\s*-?\s*hours?\s*fire\s*rated|"
    r"(?P<hr_short>\d+)\s*HR\.?\s*(?:FIRE[- ]?RATED)?|"
    r"(?P<mins>\d+)\s*-?\s*min(?:ute)?s?\s*fire\s*rated|"
    r"fire\s*rating\s*[:\-]?\s*(?P<label>\d+\s*(?:hour|hr|min|minutes?|mins?)|smoke|\d+)|"
    r"(?P<fr_code>\d+)\s*FR\b|"
    r"(?P<smoke>smoke\s*seal(?:ed)?|smoke\s*rated)"
    r")",
    re.IGNORECASE,
)


def _title_material(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip())
    lowered = cleaned.lower().replace(".", "")
    # Normalise mild-steel abbreviations from schedule notes ("m.s", "ms")
    if re.fullmatch(r"m\s*s", lowered) or lowered in {"ms", "m s"}:
        return "Mild steel"
    if "mild steel" in lowered:
        return "Mild steel"
    return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned


def _normalize_fire_rating(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"\s+", " ", str(raw).strip())
    if not text:
        return None
    match = _FIRE_RATING_RE.search(text)
    if match:
        if match.group("hours"):
            hours = match.group("hours")
            return f"{hours} hour"
        if match.groupdict().get("hr_short"):
            return f"{match.group('hr_short')} hour"
        if match.group("mins"):
            return f"{match.group('mins')} min"
        if match.group("label"):
            return re.sub(r"\s+", " ", match.group("label").strip())
        if match.group("fr_code"):
            return match.group("fr_code")
        if match.group("smoke"):
            return "Smoke"
    # Fall back to cleaned model output
    return text


def _configuration_from_text(window: str) -> str | None:
    if _SHUTTER_RE.search(window):
        return "roller shutter"
    leaf = _LEAF_RE.search(window)
    if leaf:
        value = leaf.group(1).lower()
        if "shutter" in value:
            return "roller shutter"
        return value
    return None


def _materials_from_text(window: str) -> tuple[str | None, str | None]:
    # Reuse the shared New-block material parser (avoids frame-metal / veneer bugs)
    from .text_extract import _materials

    return _materials(window)


def _fire_rating_from_text(window: str) -> str | None:
    # Prefer door leaf / shutter fire phrases over vision-panel glass alone
    preferred = re.search(
        r"(?:single leaf|double leaf|door|roller\s+shutter|rolling\s+shutter|"
        r"fire\s+rated\s+roller\s+shutter)[^\n.]{0,60}?"
        r"((?:\d+(?:\.\d+)?)\s*-?\s*hours?\s*fire\s*rated|"
        r"(?:\d+)\s*-?\s*min(?:ute)?s?\s*fire\s*rated)",
        window,
        re.IGNORECASE,
    )
    if preferred:
        return _normalize_fire_rating(preferred.group(1))
    # "2 hour fire rated roller shutter" — rating before the product name
    before_shutter = re.search(
        r"((?:\d+(?:\.\d+)?)\s*-?\s*hours?\s*fire\s*rated|"
        r"(?:\d+)\s*-?\s*min(?:ute)?s?\s*fire\s*rated)"
        r"[^\n.]{0,40}?(?:roller|rolling)\s+shutter",
        window,
        re.IGNORECASE,
    )
    if before_shutter:
        return _normalize_fire_rating(before_shutter.group(1))
    match = _FIRE_RATING_RE.search(window)
    if match:
        return _normalize_fire_rating(match.group(0))
    return None


def _apply_dim_match(record: dict[str, Any], match: re.Match[str], nearby: str) -> None:
    w, h = int(match.group("w")), int(match.group("h"))
    t_raw = match.groupdict().get("t")
    t = int(t_raw) if t_raw else None
    # Reject vision-panel sized hits; also reject shutter-box-like tiny heights alone
    if w <= 300 and h <= 1000:
        return
    if h <= 400 and w >= 1000:
        # Likely shutter-box / header dimension, not opening height
        return
    if w < 500 or h < 1500:
        return
    record["width_mm"] = w
    record["height_mm"] = h
    if t is not None and 20 <= t <= 120:
        record["thickness_mm"] = t
    if not record.get("configuration"):
        config = _configuration_from_text(match.group(0) + nearby)
        if config:
            record["configuration"] = config


def _find_dim_match(text: str) -> re.Match[str] | None:
    for pattern in (_DIM_RE, _DIM_MALAY_RE, _DIM_XH_RE):
        candidates = [
            m
            for m in pattern.finditer(text)
            if int(m.group("w")) >= 500 and int(m.group("h")) >= 1500
        ]
        if candidates:
            return candidates[0]
    return None


def enrich_doors_from_page_text(
    doors: list[dict[str, Any]],
    page_text: str,
    page_number: int,
) -> list[dict[str, Any]]:
    """
    Fill missing W/H/thickness and materials using the PDF text layer.

    For each known door mark, search the text segment from that mark to the next
    mark (and a short look-behind) for the overall door size phrase.
    """
    del page_number  # kept for API compatibility
    if not page_text:
        return doors

    by_type: dict[str, dict[str, Any]] = {d["door_type"]: dict(d) for d in doors}
    skip_marks = {"WP03", "ISO", "NOS", "DW", "W1", "W2", "H1", "H2"}

    # First occurrence of each door mark, sorted by position
    first_pos: dict[str, int] = {}
    for match in _MARK_RE.finditer(page_text):
        mark = re.sub(r"[-_\s]+", "", match.group("mark").upper())
        if mark in skip_marks:
            continue
        if mark in by_type and mark not in first_pos:
            first_pos[mark] = match.start()

    ordered = sorted(first_pos.items(), key=lambda item: item[1])
    for index, (mark, start) in enumerate(ordered):
        prev_start = ordered[index - 1][1] if index > 0 else 0
        end = ordered[index + 1][1] if index + 1 < len(ordered) else min(
            len(page_text), start + 1200
        )
        lookbehind_start = max(prev_start, start - 1000)
        before = page_text[lookbehind_start:start]
        after = page_text[start:end]
        primary = after if after.strip() else before

        record = by_type[mark]

        if record.get("width_mm") is None or record.get("height_mm") is None:
            match = None
            match_base = after
            before_hits = [
                m
                for m in _DIM_RE.finditer(before)
                if int(m.group("w")) >= 500 and int(m.group("h")) >= 1500
            ]
            if before_hits:
                match = before_hits[-1]
                match_base = before
            if not match:
                match = _find_dim_match(after)
                match_base = after
            if not match:
                match = _find_dim_match(before + after)
                match_base = before + after
            if match:
                nearby = match_base[match.end() : match.end() + 120]
                _apply_dim_match(record, match, nearby)

        if not record.get("configuration"):
            config = _configuration_from_text(before + " " + primary)
            if config:
                record["configuration"] = config

        if record.get("door_material") is None or record.get("frame_material") is None:
            door_mat, frame_mat = _materials_from_text(before + " " + primary)
            # Prefer materials derived from the door's own description when present
            desc = record.get("description")
            if desc:
                from_desc_door, from_desc_frame = _materials_from_text(str(desc))
                door_mat = from_desc_door or door_mat
                frame_mat = from_desc_frame or frame_mat
            if record.get("door_material") is None and door_mat:
                record["door_material"] = door_mat
            if record.get("frame_material") is None and frame_mat:
                record["frame_material"] = frame_mat

        door_mat, frame_mat = sanitize_materials(
            record.get("door_material"),
            record.get("frame_material"),
            description=record.get("description"),
        )
        record["door_material"] = door_mat
        record["frame_material"] = frame_mat

        if record.get("fire_rating") is None:
            desc = record.get("description")
            if desc:
                fire = fire_rating_from_description(str(desc))
            else:
                fire = _fire_rating_from_text(before + " " + primary)
            if fire:
                record["fire_rating"] = fire

        record["fire_rating"] = sanitize_fire_rating(
            record.get("fire_rating"),
            description=record.get("description"),
        )

        if record.get("quantity") is None or record.get("quantity_by_level") is None:
            # TOTAL / Level lines usually sit immediately under the door type label
            by_level, overall = parse_level_quantities(page_text[start:end])
            if record.get("quantity_by_level") is None and by_level:
                record["quantity_by_level"] = by_level
            if record.get("quantity") is None and overall is not None:
                record["quantity"] = overall

        spec = extract_specification(before, after)
        if spec:
            existing = record.get("description")
            if existing is None:
                record["description"] = spec
            elif len(spec) > len(str(existing)) and not (
                str(existing).lower().lstrip().startswith("new ")
                and not spec.lower().lstrip().startswith("new ")
            ):
                # Do not replace a New… block with unrelated longer text
                if str(existing).lower().lstrip().startswith("new "):
                    pass
                else:
                    record["description"] = spec

        remarks = extract_remarks(before, after)
        if remarks and not record.get("remarks"):
            record["remarks"] = remarks

    return list(by_type.values())


def _is_roller_shutter(record: dict[str, Any]) -> bool:
    config = (record.get("configuration") or "").lower()
    desc = (record.get("description") or "").lower()
    return "shutter" in config or "roller shutter" in desc or "rolling shutter" in desc


def _mark_suggests_fire_shutter(mark: str) -> bool:
    upper = (mark or "").upper()
    return upper.startswith("FRS") or upper.startswith("FR") and "RS" in upper


def _door_needs_text_refine(d: dict[str, Any]) -> bool:
    """Whether a second-pass text LLM is still useful for this door."""
    is_shutter = _is_roller_shutter(d)
    mark = str(d.get("door_type") or "")
    if d.get("width_mm") is None or d.get("height_mm") is None:
        return True
    # Thickness is often absent for roller shutters / aluminium doors
    if d.get("thickness_mm") is None and not is_shutter:
        # Only refine thickness when mark suggests a solid/fire leaf
        if mark.upper().startswith(("FD", "FRS", "MD")):
            return True
    if d.get("door_material") is None and d.get("frame_material") is None:
        return True
    if d.get("fire_rating") is None:
        if is_shutter:
            return _mark_suggests_fire_shutter(mark)
        # Only push fire-rating refine for fire-door prefixes
        if mark.upper().startswith(("FD", "FRS", "FR")):
            return True
    return False


def refine_dimensions_with_text_llm(
    doors: list[dict[str, Any]],
    page_text: str,
    *,
    client: AzureOpenAI | None = None,
    deployment: str | None = None,
    prompt_version: str | None = None,
) -> tuple[list[dict[str, Any]], TokenUsage]:
    """Second-pass text LLM to fill any remaining missing dimensions/materials."""
    missing = [d for d in doors if _door_needs_text_refine(d)]
    if not missing or not page_text.strip():
        return doors, TokenUsage()

    oa = client or _client()
    model = deployment or _deployment()
    prompts = load_prompts(prompt_version)
    marks = [d["door_type"] for d in missing]
    clipped = page_text.strip()
    if len(clipped) > 14000:
        clipped = clipped[:14000] + "\n…[truncated]"

    response = oa.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": prompts.refine_system,
            },
            {
                "role": "user",
                "content": prompts.render_refine_user(
                    marks=marks,
                    page_text=clipped,
                ),
            },
        ],
    )
    usage = _usage_from_response(response)
    content = response.choices[0].message.content or "{}"
    try:
        payload = _parse_json_payload(content)
    except json.JSONDecodeError:
        return doors, usage

    updates = {
        d["door_type"]: d
        for item in payload.get("doors", [])
        if isinstance(item, dict)
        for d in [_normalize_door(item, doors[0].get("page", 1))]
        if d
    }
    merged: list[dict[str, Any]] = []
    for door in doors:
        update = updates.get(door["door_type"])
        if not update:
            merged.append(door)
            continue
        combined = dict(door)
        for key in (
            "width_mm",
            "height_mm",
            "thickness_mm",
            "fire_rating",
            "door_material",
            "frame_material",
            "configuration",
            "quantity_by_level",
            "quantity",
            "location",
            "description",
            "remarks",
        ):
            if combined.get(key) is None and update.get(key) is not None:
                combined[key] = update[key]
        merged.append(combined)
    return merged, usage


def _page_text_block(page_text: str | None, *, limit: int = 14000) -> str:
    if not page_text or not page_text.strip():
        return ""
    clipped = page_text.strip()
    if len(clipped) > limit:
        clipped = clipped[:limit] + "\n…[truncated]"
    return (
        "\n\nEmbedded PDF text from the same page (use for exact mm values):\n"
        f"```\n{clipped}\n```\n"
    )


_ITEM_FIELD_KEYS = frozenset(
    {
        "item_number",
        "item_no",
        "item_no.",
        "item",
        "item #",
        "item no",
        "item no.",
        "item number",
        "no.",
        "no",
        "#",
        "bil",
        "bil.",
        "bilangan",
    }
)


def _looks_like_item_key(key: str) -> bool:
    cleaned = re.sub(r"[\s_\-]+", " ", str(key or "").strip().lower())
    cleaned = cleaned.rstrip(".")
    if cleaned in _ITEM_FIELD_KEYS:
        return True
    # "ITEM", "Item No", "Item Number", "ITEM NO."
    return bool(re.fullmatch(r"(?:item(?:\s*(?:no|number|#)?)?|bil(?:angan)?)", cleaned))


def _coerce_item_number(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        if isinstance(value, int):
            return str(value)
        text = str(value).strip()
    else:
        text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "-"}:
        return None
    # Model often returns ITEM as "1.00" — normalise whole-number floats
    try:
        as_float = float(text.replace(",", ""))
        if as_float.is_integer() and abs(as_float) < 10**9:
            return str(int(as_float))
    except ValueError:
        pass
    # Avoid treating door marks as item numbers when mis-mapped
    if _DOOR_TYPE_RE.match(re.sub(r"[\s_]+", "", text.upper())):
        return None
    return text


def _extract_item_number(raw: dict[str, Any]) -> str | None:
    for key in ("item_number", "item_no", "item"):
        found = _coerce_item_number(raw.get(key))
        if found:
            return found
    extras = raw.get("extra_fields")
    if isinstance(extras, dict):
        for key, value in extras.items():
            if _looks_like_item_key(str(key)):
                found = _coerce_item_number(value)
                if found:
                    return found
    return None


def _enrich_description_from_extras(item: dict[str, Any]) -> dict[str, Any]:
    """Fold extra_fields / source_row_text into description so nothing is lost."""
    out = dict(item)
    # Promote ITEM / Item No. into a typed field before folding leftovers into description
    if not out.get("item_number"):
        promoted = _extract_item_number(out)
        if promoted:
            out["item_number"] = promoted
    extras = out.get("extra_fields")
    source_row = out.get("source_row_text")
    bits: list[str] = []
    existing = str(out.get("description") or "").strip()
    if existing:
        bits.append(existing)
    if isinstance(extras, dict) and extras:
        for key, value in extras.items():
            if value is None or str(value).strip() == "":
                continue
            # Skip item columns — already captured as item_number
            if _looks_like_item_key(str(key)):
                continue
            fragment = f"{key}: {value}"
            if fragment not in existing:
                bits.append(fragment)
    if source_row and str(source_row).strip():
        src = str(source_row).strip()
        if src not in existing and src not in " | ".join(bits):
            bits.append(src)
    if bits:
        out["description"] = " | ".join(bits)[:900]
    return out


def _doors_from_normalized_rows(
    rows: list[Any],
    page_number: int,
    *,
    source_pdf: str | None = None,
) -> list[dict[str, Any]]:
    doors: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        enriched = _enrich_description_from_extras(item)
        normalized = _normalize_door(
            enriched,
            page_number,
            source_pdf=source_pdf,
        )
        if normalized:
            doors.append(normalized)
    return doors


def analyze_structured_table_page(
    page: PageImage,
    *,
    page_text: str | None = None,
    client: AzureOpenAI | None = None,
    deployment: str | None = None,
    prompt_version: str | None = None,
) -> tuple[dict[str, Any], TokenUsage]:
    """
    First-pass vision call for structured-table schedules.

    Returns analysis JSON (needs_restructure, normalized_rows, …) and token usage.
    """
    oa = client or _client()
    model = deployment or _deployment()
    prompts = load_prompts(prompt_version)
    if not prompts.supports_table_analyze:
        return {}, TokenUsage()

    data_url = f"data:image/png;base64,{_encode_png(page.png_bytes)}"
    text_block = _page_text_block(page_text)

    response = oa.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompts.analyze_system or ""},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompts.render_analyze_user(
                            page_number=page.page_number,
                            text_block=text_block,
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    )
    usage = _usage_from_response(response)
    content = response.choices[0].message.content or "{}"
    try:
        payload = _parse_json_payload(content)
    except json.JSONDecodeError:
        return {}, usage
    result = payload if isinstance(payload, dict) else {}
    return result, usage


def extract_doors_from_page_image(
    page: PageImage,
    *,
    page_text: str | None = None,
    client: AzureOpenAI | None = None,
    deployment: str | None = None,
    prompt_version: str | None = None,
    normalized_table: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], TokenUsage]:
    """Call Azure OpenAI vision on one page image and return door records + token usage."""
    oa = client or _client()
    model = deployment or _deployment()
    prompts = load_prompts(prompt_version)
    data_url = f"data:image/png;base64,{_encode_png(page.png_bytes)}"
    text_block = _page_text_block(page_text)

    normalized_table_block = ""
    if normalized_table:
        clipped = json.dumps(normalized_table, ensure_ascii=False, indent=2)
        if len(clipped) > 16000:
            clipped = clipped[:16000] + "\n…[truncated]"
        normalized_table_block = (
            "\n\nNormalised table analysis from the prior pass "
            "(preserve all fields; do not drop extra_fields):\n"
            f"```json\n{clipped}\n```\n"
        )

    response = oa.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompts.vision_system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompts.render_vision_user(
                            page_number=page.page_number,
                            text_block=text_block,
                            normalized_table_block=normalized_table_block,
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    )
    usage = _usage_from_response(response)
    content = response.choices[0].message.content or "{}"
    payload = _parse_json_payload(content)
    doors_raw = payload.get("doors", [])
    if not isinstance(doors_raw, list):
        if normalized_table and isinstance(normalized_table.get("normalized_rows"), list):
            return (
                _doors_from_normalized_rows(
                    normalized_table["normalized_rows"],
                    page.page_number,
                    source_pdf=page.source_name or None,
                ),
                usage,
            )
        return [], usage

    doors: list[dict[str, Any]] = []
    for item in doors_raw:
        if isinstance(item, dict):
            enriched = _enrich_description_from_extras(item)
            normalized = _normalize_door(
                enriched,
                page.page_number,
                source_pdf=page.source_name or None,
            )
            if normalized:
                doors.append(normalized)

    if normalized_table and isinstance(normalized_table.get("normalized_rows"), list):
        analysis_doors = _doors_from_normalized_rows(
            normalized_table["normalized_rows"],
            page.page_number,
            source_pdf=page.source_name or None,
        )
        doors = merge_door_records(doors, analysis_doors)

    return doors, usage


def extract_doors_from_pdf(
    pdf_bytes: bytes,
    *,
    dpi: int = 200,
    max_pages: int | None = None,
    page_numbers: list[int] | None = None,
    source_name: str = "",
    on_progress: ProgressCallback | None = None,
    client: AzureOpenAI | None = None,
    deployment: str | None = None,
    global_page_offset: int = 0,
    global_page_total: int | None = None,
    file_index: int | None = None,
    file_count: int | None = None,
    usage_totals: TokenUsage | None = None,
    schedule_format: str | None = None,
    prompt_version: str | None = None,
) -> tuple[list[dict[str, Any]], list[PageImage], TokenUsage]:
    """
    Convert selected PDF pages to images, then extract door type, size, and materials.

    Returns (door records, page images used for extraction, token usage for this PDF).
    """
    label = source_name or "PDF"
    totals = usage_totals if usage_totals is not None else TokenUsage()
    file_usage = TokenUsage()
    fmt = normalize_schedule_format(schedule_format)
    version = prompt_version or resolve_prompt_version_for_format(fmt)
    prompts = load_prompts(version)

    _emit(
        on_progress,
        ProgressEvent(
            message=f"Converting pages to images ({label})…",
            stage="convert",
            current=global_page_offset,
            total=global_page_total,
            source_name=source_name or None,
            file_index=file_index,
            file_count=file_count,
            prompt_tokens_total=totals.prompt_tokens,
            completion_tokens_total=totals.completion_tokens,
        ),
    )

    pages = pdf_bytes_to_page_images(
        pdf_bytes,
        dpi=dpi,
        max_pages=max_pages,
        page_numbers=page_numbers,
        source_name=source_name,
    )
    if not pages:
        return [], [], file_usage

    job_total = global_page_total or len(pages)
    texts = pdf_page_texts(pdf_bytes, page_numbers=[p.page_number for p in pages])
    oa = client or _client()
    model = deployment or _deployment()
    interval = _min_interval()
    all_doors: list[dict[str, Any]] = []
    last_call = 0.0

    for index, page in enumerate(pages, start=1):
        global_current = global_page_offset + index
        prefix = f"[{source_name}] " if source_name else ""
        page_usage = TokenUsage()
        _emit(
            on_progress,
            ProgressEvent(
                message=(
                    f"{prefix}Extracting page {page.page_number} "
                    f"({global_current}/{job_total})…"
                ),
                stage="extract",
                current=global_current,
                total=job_total,
                page_number=page.page_number,
                source_name=source_name or None,
                file_index=file_index,
                file_count=file_count,
                prompt_tokens_total=totals.prompt_tokens,
                completion_tokens_total=totals.completion_tokens,
            ),
        )

        elapsed = time.monotonic() - last_call
        if last_call and elapsed < interval:
            time.sleep(interval - elapsed)

        page_text = texts.get(page.page_number, "")
        normalized_table: dict[str, Any] | None = None

        if fmt == SCHEDULE_FORMAT_STRUCTURED_TABLE and prompts.supports_table_analyze:
            _emit(
                on_progress,
                ProgressEvent(
                    message=(
                        f"{prefix}Analysing table structure on page "
                        f"{page.page_number}…"
                    ),
                    stage="analyze",
                    current=global_current,
                    total=job_total,
                    page_number=page.page_number,
                    source_name=source_name or None,
                    file_index=file_index,
                    file_count=file_count,
                    prompt_tokens_total=totals.prompt_tokens,
                    completion_tokens_total=totals.completion_tokens,
                ),
            )
            normalized_table, analyze_usage = analyze_structured_table_page(
                page,
                page_text=page_text,
                client=oa,
                deployment=model,
                prompt_version=version,
            )
            page_usage += analyze_usage
            file_usage += analyze_usage
            totals += analyze_usage
            last_call = time.monotonic()
            note = (normalized_table or {}).get("structure_notes") or ""
            restructure = (normalized_table or {}).get("needs_restructure")
            row_count = len((normalized_table or {}).get("normalized_rows") or [])
            _emit(
                on_progress,
                ProgressEvent(
                    message=(
                        f"{prefix}Table analysis: "
                        f"{'restructure' if restructure else 'no restructure'} · "
                        f"{row_count} row(s)"
                        + (f" — {note[:120]}" if note else "")
                    ),
                    stage="analyze",
                    current=global_current,
                    total=job_total,
                    page_number=page.page_number,
                    source_name=source_name or None,
                    file_index=file_index,
                    file_count=file_count,
                    prompt_tokens=analyze_usage.prompt_tokens,
                    completion_tokens=analyze_usage.completion_tokens,
                    prompt_tokens_total=totals.prompt_tokens,
                    completion_tokens_total=totals.completion_tokens,
                ),
            )
            elapsed = time.monotonic() - last_call
            if elapsed < interval:
                time.sleep(interval - elapsed)

        doors, vision_usage = extract_doors_from_page_image(
            page,
            page_text=page_text,
            client=oa,
            deployment=model,
            prompt_version=version,
            normalized_table=normalized_table,
        )
        page_usage += vision_usage
        file_usage += vision_usage
        totals += vision_usage
        last_call = time.monotonic()

        text_doors = extract_doors_from_page_text(page_text, page.page_number)
        if fmt == SCHEDULE_FORMAT_STRUCTURED_TABLE:
            doors = merge_door_records(doors, text_doors)
        else:
            doors = merge_door_records(text_doors, doors)
        doors = enrich_doors_from_page_text(doors, page_text, page.page_number)
        for door in doors:
            if not door.get("source_pdf") and source_name:
                door["source_pdf"] = source_name

        still_missing = any(_door_needs_text_refine(d) for d in doors)
        if still_missing and page_text.strip():
            _emit(
                on_progress,
                ProgressEvent(
                    message=f"{prefix}Refining page {page.page_number}…",
                    stage="refine",
                    current=global_current,
                    total=job_total,
                    page_number=page.page_number,
                    source_name=source_name or None,
                    file_index=file_index,
                    file_count=file_count,
                    prompt_tokens=vision_usage.prompt_tokens,
                    completion_tokens=vision_usage.completion_tokens,
                    prompt_tokens_total=totals.prompt_tokens,
                    completion_tokens_total=totals.completion_tokens,
                ),
            )
            elapsed = time.monotonic() - last_call
            if elapsed < interval:
                time.sleep(interval - elapsed)
            doors, refine_usage = refine_dimensions_with_text_llm(
                doors,
                page_text,
                client=oa,
                deployment=model,
                prompt_version=version,
            )
            page_usage += refine_usage
            file_usage += refine_usage
            totals += refine_usage
            last_call = time.monotonic()
            doors = enrich_doors_from_page_text(doors, page_text, page.page_number)
            for door in doors:
                if not door.get("source_pdf") and source_name:
                    door["source_pdf"] = source_name

        all_doors.extend(doors)

        _emit(
            on_progress,
            ProgressEvent(
                message=(
                    f"{prefix}Page {page.page_number} done — "
                    f"found {len(doors)} door type(s) "
                    f"({global_current}/{job_total}) | "
                    f"tokens in {page_usage.prompt_tokens} / "
                    f"out {page_usage.completion_tokens}."
                ),
                stage="page_done",
                current=global_current,
                total=job_total,
                page_number=page.page_number,
                source_name=source_name or None,
                doors_found=len(doors),
                file_index=file_index,
                file_count=file_count,
                prompt_tokens=page_usage.prompt_tokens,
                completion_tokens=page_usage.completion_tokens,
                prompt_tokens_total=totals.prompt_tokens,
                completion_tokens_total=totals.completion_tokens,
            ),
        )

    return all_doors, pages, file_usage


def extract_doors_from_pdfs(
    pdf_files: list[tuple[str, bytes]],
    *,
    dpi: int = 200,
    page_numbers: list[int] | None = None,
    page_selection: str | None = None,
    on_progress: ProgressCallback | None = None,
    schedule_format: str | None = None,
    prompt_version: str | None = None,
) -> tuple[list[dict[str, Any]], list[PageImage], TokenUsage]:
    """
    Extract doors from one or more PDFs.

    page_numbers takes precedence; otherwise page_selection (e.g. '1-6' or '1,8,10')
    is parsed. Applied independently to each PDF.

    Returns (doors, pages, total token usage).
    """
    if not pdf_files:
        return [], [], TokenUsage()

    selected = page_numbers
    if selected is None and page_selection is not None:
        selected = parse_page_selection(page_selection)

    fmt = normalize_schedule_format(schedule_format)
    version = prompt_version or resolve_prompt_version_for_format(fmt)
    total_pages = count_extract_pages(pdf_files, selected)
    usage_totals = TokenUsage()
    prompts = load_prompts(version)
    _emit(
        on_progress,
        ProgressEvent(
            message=(
                f"Starting extraction: {len(pdf_files)} PDF(s), "
                f"{total_pages} page(s) selected · "
                f"{schedule_format_label(fmt)} · prompt `{prompts.version}`."
            ),
            stage="start",
            current=0,
            total=total_pages,
            file_count=len(pdf_files),
        ),
    )

    client = _client()
    deployment = _deployment()
    all_doors: list[dict[str, Any]] = []
    all_pages: list[PageImage] = []
    page_offset = 0

    for index, (name, pdf_bytes) in enumerate(pdf_files, start=1):
        pages_in_file = _selected_pages_for_pdf(pdf_bytes, selected)
        _emit(
            on_progress,
            ProgressEvent(
                message=(
                    f"Processing file {index}/{len(pdf_files)}: {name} "
                    f"({len(pages_in_file)} page(s))"
                ),
                stage="file",
                current=page_offset,
                total=total_pages,
                source_name=name,
                file_index=index,
                file_count=len(pdf_files),
                prompt_tokens_total=usage_totals.prompt_tokens,
                completion_tokens_total=usage_totals.completion_tokens,
            ),
        )
        doors, pages, _file_usage = extract_doors_from_pdf(
            pdf_bytes,
            dpi=dpi,
            page_numbers=selected,
            source_name=name,
            on_progress=on_progress,
            client=client,
            deployment=deployment,
            global_page_offset=page_offset,
            global_page_total=total_pages,
            file_index=index,
            file_count=len(pdf_files),
            usage_totals=usage_totals,
            schedule_format=fmt,
            prompt_version=version,
        )
        all_doors.extend(doors)
        all_pages.extend(pages)
        page_offset += len(pages_in_file)

    _emit(
        on_progress,
        ProgressEvent(
            message=(
                f"Extraction finished: {len(all_doors)} door record(s) "
                f"from {len(all_pages)} page(s) | "
                f"tokens in {usage_totals.prompt_tokens} / "
                f"out {usage_totals.completion_tokens} "
                f"(total {usage_totals.total_tokens})."
            ),
            stage="done",
            current=total_pages,
            total=total_pages,
            file_count=len(pdf_files),
            prompt_tokens=usage_totals.prompt_tokens,
            completion_tokens=usage_totals.completion_tokens,
            prompt_tokens_total=usage_totals.prompt_tokens,
            completion_tokens_total=usage_totals.completion_tokens,
        ),
    )
    return all_doors, all_pages, usage_totals

