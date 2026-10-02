"""
Text-layer door discovery for varied architectural schedule templates.

Handles common MY/SG/region patterns without relying only on vision:
  - FD1 / D1 / TD13 / AD1 / RS1 style marks
  - '1100mm (W) x 2400mm (H) x 50mm thk'
  - '900 x 2100' / '900 X 2100MM'
  - 'D1 (900x2100)'
  - Malay '1800MM(L) X 2400MM(T)' (Lebar x Tinggi)
"""

from __future__ import annotations

import re
from typing import Any

# Known door / shutter / glazed-door prefixes (exclude bare W — often axis labels W1/H1)
_MARK_RE = re.compile(
    r"\b(?P<mark>"
    r"(?:FRS|FD|SD|RS|TD|PD|AD|MD|GD|WD|DT|DR|LD|D)"
    r"[-_]?\d{1,3}[A-Z]?"
    r")\b(?![-/])",  # reject drawing refs like D6-3A
    re.IGNORECASE,
)

_SKIP_MARKS = {
    "DW",
    "WP",
    "WP03",
    "ISO",
    "NOS",
    "W1",
    "W2",
    "H1",
    "H2",
    "FFL",
    "TNB",
    "LAM",
}

# Description-style overall size
_DIM_DESC_RE = re.compile(
    r"(?P<w>\d+)\s*mm\s*\(\s*W\s*\)\s*x\s*(?P<h>\d+)\s*mm\s*\(\s*H\s*\)"
    r"(?:\s*x\s*(?P<t>\d+)\s*mm\s*thk)?",
    re.IGNORECASE,
)
# Table / note: 900 x 2100 or 900 X 2100MM (optional thickness)
_DIM_XH_RE = re.compile(
    r"(?P<w>\d{3,4})\s*[x×X]\s*(?P<h>\d{3,4})\s*(?:mm)?"
    r"(?:\s*[x×X]\s*(?P<t>\d{2,3})\s*(?:mm)?)?",
    re.IGNORECASE,
)
# Mark with size: D1 (900x2100) / FD2 (1000x2400)
_MARK_SIZE_RE = re.compile(
    r"\b(?P<mark>(?:FRS|FD|SD|RS|TD|PD|AD|MD|GD|WD|DT|DR|LD|D)"
    r"[-_]?\d{1,3}[A-Z]?)\s*\(\s*(?P<w>\d{3,4})\s*[x×X]\s*(?P<h>\d{3,4})\s*\)",
    re.IGNORECASE,
)
# Full "New … door/shutter …" specification block with embedded size
_NEW_BLOCK_RE = re.compile(
    r"(?P<block>New\s+"
    r"(?P<w>\d+)\s*mm\s*\(\s*W\s*\)\s*x\s*(?P<h>\d+)\s*mm\s*\(\s*H\s*\)"
    r"(?:\s*x\s*(?P<t>\d+)\s*mm\s*thk)?\.?"
    r".{20,1200}?"
    r"(?:door|shutter|pintu|louvres?|louvers?)"
    r".{0,900}?"
    r"(?:equilvalent|equivalent|approval|manuf(?:acturer)?'?s\s+detail|"
    r"to\s+detail|arch(?:itect)?'?s\s+(?:selection|approval)|JBPM|Bomba)"
    r"[^\n.]{0,80}\.?)",
    re.IGNORECASE | re.DOTALL,
)
# Malay: 1800MM(L) X 2400MM(T)
_DIM_MALAY_RE = re.compile(
    r"(?P<w>\d{3,4})\s*MM\s*\(\s*L\s*\)\s*X\s*(?P<h>\d{3,4})\s*MM\s*\(\s*T\s*\)",
    re.IGNORECASE,
)
# Desaru-style structure / frame sizes often listed as "800 x 2100" near TD1
_TOTAL_RE = re.compile(
    r"(?:TOTAL|BILANGAN|QTY|NOS)\s*[:：]?\s*(?P<qty>\d+)",
    re.IGNORECASE,
)
# "Level 1 (24), Level 2 (5)" / "Services Floor Plan (1)"
_LEVEL_QTY_RE = re.compile(
    r"(?P<label>"
    r"Level\s*\d+"
    r"|Aras\s*\d+"
    r"|L\d+"
    r"|GF|G/?F|UG|Basement|Roof|Services?\s+Floor(?:\s+Plan)?"
    r"|Ground(?:\s+Floor)?"
    r"|First(?:\s+Floor)?"
    r"|Second(?:\s+Floor)?"
    r"|Third(?:\s+Floor)?"
    r")"
    r"\s*\(\s*(?P<qty>\d+)\s*\)",
    re.IGNORECASE,
)
_LEAF_RE = re.compile(
    r"\b("
    r"single\s+leaf|double\s+leaf|sliding|bi-?fold(?:ing)?|bi-?passing|"
    r"swing|flush|roller\s+shutter|rolling\s+shutter|"
    r"alumn?\.?\s+louvres?|aluminium\s+louvres?|louvres?\s+door|"
    r"pintu\s+rata|2\s+bukaan|satu\s+daun|dua\s+daun"
    r")\b",
    re.IGNORECASE,
)
_SHUTTER_RE = re.compile(
    r"\b(?:fire\s+rated\s+)?(?:manual\s+)?(?:m\.?\s*s\.?\s+|mild\s+steel\s+)?"
    r"(?:roller|rolling)\s+shutter\b",
    re.IGNORECASE,
)
# Finish-only phrases — never use alone as door_material
_FINISH_ONLY_RE = re.compile(
    r"^(?:veneer\s+lipping|paint\s+finish|laminate(?:\s+[\w-]+)*\s+finish|"
    r"hpl\s+finish|pvdf(?:\s+powder)?\s+coat(?:ed)?(?:\s+finish)?)"
    r"(?:\s+with\s+.+)?$",
    re.IGNORECASE,
)
_FRAME_MATERIAL_RE = re.compile(
    r"\b(?P<frame>"
    r"metal|aluminium|aluminum|steel|timber|wood|hollow\s+metal|zincalume|"
    r"alumn?\.?|alum\.?"
    r")\s+frame\b|"
    r"\bbingkai\s+besi\b|"
    r"\b(?:1\.?\d\s*mm\s+thk\.?\s+)?metal\s*\(zincalume",
    re.IGNORECASE,
)
_FIRE_RATING_RE = re.compile(
    r"\b(?:"
    r"(?P<hours>\d+(?:\.\d+)?)\s*-?\s*(?:hours?|hr|hrs)\s*(?:fire\s*rated)?|"
    r"(?P<mins>\d+)\s*-?\s*min(?:ute)?s?\s*(?:fire\s*rated)?|"
    r"(?P<hr_short>\d+)\s*HR\.?\s*(?:FIRE[- ]?RATED)?|"
    r"(?P<fr_code>\d+)\s*FR\b|"
    r"(?P<smoke>smoke\s*seal(?:ed)?|smoke\s*rated)"
    r")",
    re.IGNORECASE,
)
_GLUE_FALSE_POSITIVE = re.compile(r"glue\s*type\s*[:\-]?\s*D\d+", re.I)

# Full schedule description / specification paragraphs
_SPEC_NEW_RE = re.compile(
    r"(?:New|Provide|Supply|Install)\s+"
    r".{20,1200}?"
    r"(?:door|shutter|pintu)"
    r".{0,800}?"
    r"(?:equilvalent|equivalent|approval|manuf(?:acturer)?'?s\s+detail|to\s+detail|"
    r"arch(?:itect)?'?s\s+(?:selection|approval)|JBPM|Bomba)[^\n.]{0,80}\.?",
    re.IGNORECASE | re.DOTALL,
)
_SPEC_LEAF_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*MM\s*THK\.?\s+)?"
    r"(?:HONEYCOMB\s+CORE\s+FLUSH\s+DOOR|SOLID\s+TIMBER\s+DOOR|TIMBER\s+FLUSH|"
    r"FIRE[- ]?RATED\s+[A-Z0-9 /,-]{5,80}DOOR|METAL\s+DOOR|ALUM(?:INIUM|\.)?\s+"
    r"(?:FRAME(?:D)?\s+)?(?:SOLID\s+)?PANEL\s+DOOR|PINTU\s+[A-Z ]{3,40}|"
    r"POWDER\s+COATED\s+ALUM[^\n]{5,100}|METAL\s*\(ZINCALUME[^\n]{5,100}|"
    r"FIXED\s+POWDER\s+COATED\s+ALUM\.?\s+LOUVERS?[^\n]{0,100})"
    r"[^\n]{0,400}",
    re.IGNORECASE,
)
_SPEC_SIZE_LINE_RE = re.compile(
    r"\d{3,4}\s*[x×X]\s*\d{3,4}\s*MM?\s+[A-Z][^\n]{10,200}",
    re.IGNORECASE,
)
_SPEC_MALAY_RE = re.compile(
    r"(?:PINTU|SPESIFIKASI)[^\n]{10,500}",
    re.IGNORECASE,
)
_TITLE_NOISE_RE = re.compile(
    r"\b(?:"
    r"GENERAL\s*-\s*DOOR\s*SCHEDULE|DOOR\s*&\s*WINDOW\s*SCHEDULE|"
    r"NOS\s*:|REMARKS\s*:|LOCATION\s*:|DESCRIPTION\s*:|DOOR\s*TYPE\s*:|"
    r"FLOOR\s*FINISH\s*LEVEL|AS\s*SHOWN|REFER\s*TO\s*KEY\s*PLAN|"
    r"TOTAL\s*:|Level\s*\d+\s*\(\d+\)|TENDER|DRAWING|SCHEDULE\s*\d*"
    r")\b",
    re.IGNORECASE,
)
_REMARKS_FIELD_RE = re.compile(
    r"(?:REMARKS?|CATATAN|REMARK)\s*[:：]\s*(?P<val>[^\n]{2,200})",
    re.IGNORECASE,
)
_REMARKS_NOTE_RE = re.compile(
    r"(?:"
    r"REFER\s+TO\s+[A-Z0-9][A-Z0-9 /&'\-]{2,60}"
    r"|BY\s+OTHERS"
    r"|SEE\s+(?:DETAIL|DWG|DRAWING|NOTE)[^\n]{0,40}"
    r"|AS\s+PER\s+[A-Z0-9][A-Z0-9 /.\-]{2,40}"
    r"|\bN\s*/\s*A\b"
    r"|\bN\.?\s*A\.?\b"
    r"|\bNIL\b"
    r")",
    re.IGNORECASE,
)
# Skip drawing-wide / title-block notes when used as door remarks
_REMARKS_SKIP_RE = re.compile(
    r"CONTRACTORS?\s+SHALL|CHECK\s+ALL\s+DIMENSIONS|COPYRIGHT|"
    r"DOOR\s*TYPE\s*:|LOCATION\s*:|DESCRIPTION\s*:|FLOOR\s*FINISH",
    re.IGNORECASE,
)


def _norm_mark(raw: str) -> str:
    return re.sub(r"[-_\s]+", "", raw.strip().upper())


def _plausible_opening(w: int | None, h: int | None, t: int | None = None) -> bool:
    if w is None or h is None:
        return False
    # Vision panel / trim / decorative sizes
    if w <= 300 and h <= 1000:
        return False
    if h <= 400 and w >= 1000:  # shutter box / header
        return False
    if w < 500 or w > 6000 or h < 1500 or h > 5000:
        return False
    if t is not None and (t < 20 or t > 120):
        # thickness optional; reject absurd values when present
        return w >= 500 and h >= 1500  # keep W/H, caller may drop t
    return True


def parse_level_quantities(window: str) -> tuple[str | None, int | None]:
    """
    Parse per-level counts and overall total from a door-type text window.

    Returns (quantity_by_level display string, overall quantity).
    Example: ("Level 1: 24; Level 2: 5", 29)

    Only the first Level…TOTAL block is used so a following orphan block
    for another type does not leak into this door.
    """
    if not window:
        return None, None

    total_match = _TOTAL_RE.search(window)
    segment = window[: total_match.end()] if total_match else window

    parts: list[str] = []
    level_sum = 0
    for match in _LEVEL_QTY_RE.finditer(segment):
        label = re.sub(r"\s+", " ", match.group("label").strip())
        # Normalise common abbreviations
        label = re.sub(r"^L(\d+)$", r"Level \1", label, flags=re.I)
        label = re.sub(r"^Aras\s*", "Level ", label, flags=re.I)
        if re.fullmatch(r"GF|G/?F|Ground(?:\s+Floor)?", label, re.I):
            label = "Ground Floor"
        qty = int(match.group("qty"))
        parts.append(f"{label}: {qty}")
        level_sum += qty

    by_level = "; ".join(parts) if parts else None

    overall = int(total_match.group("qty")) if total_match else None
    if overall is None and level_sum > 0:
        overall = level_sum
    return by_level, overall


def _clean_specification(text: str) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip(" \t\n\r-–—:;")
    cleaned = _TITLE_NOISE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" \t\n\r-–—:;")
    if len(cleaned) < 25:
        return None
    # Drop leftovers that are mostly qty / mark noise
    if not re.search(
        r"door|shutter|pintu|timber|plywood|metal|alum|frame|leaf|flush|"
        r"honeycomb|fire|mm\b|thk|panel|kayu",
        cleaned,
        re.I,
    ):
        return None
    return cleaned[:900]


def extract_remarks(before: str, after: str) -> str | None:
    """
    Extract remarks / notes for a door type from its local text window.

    Captures labeled REMARKS:/CATATAN: values and common schedule notes
    such as 'REFER TO KEY PLAN', 'BY OTHERS', etc.
    """
    before = before or ""
    after = after or ""
    # Prefer notes immediately after the mark (typical CAD dump order)
    windows = [(after, True), (before[-350:] if before else "", False)]
    found: list[str] = []

    for window, prefer in windows:
        if not window.strip():
            continue
        for match in _REMARKS_FIELD_RE.finditer(window):
            val = re.sub(r"\s+", " ", match.group("val")).strip(" -:;")
            if not val or _REMARKS_SKIP_RE.search(val):
                continue
            # Stop at next field label fragments
            val = re.split(
                r"\b(?:LOCATION|DESCRIPTION|DOOR\s*TYPE|NOS|TOTAL|LEVEL)\b",
                val,
                maxsplit=1,
                flags=re.I,
            )[0].strip(" -:;")
            if len(val) >= 2:
                found.append(val)
                if prefer:
                    return val[:300]
        for match in _REMARKS_NOTE_RE.finditer(window):
            val = re.sub(r"\s+", " ", match.group(0)).strip()
            if not val or _REMARKS_SKIP_RE.search(val):
                continue
            found.append(val)
            if prefer:
                return val[:300]

    if not found:
        return None
    # Deduplicate while preserving order
    unique: list[str] = []
    for item in found:
        if item.lower() not in {u.lower() for u in unique}:
            unique.append(item)
    return "; ".join(unique)[:300]


def extract_specification(before: str, after: str) -> str | None:
    """
    Pull the door description / specification note for one mark.

    Prefers full 'New … door …' paragraphs (often before the mark in CAD text),
    then leaf/frame spec lines, Malay spesifikasi, then size+product lines.
    """
    before = before or ""
    after = after or ""
    candidates: list[tuple[int, str]] = []  # (score, text)

    def _consider(match: re.Match[str], *, near_mark: bool, from_after: bool) -> None:
        cleaned = _clean_specification(match.group(0))
        if not cleaned:
            return
        score = len(cleaned)
        if near_mark:
            score += 200
        if from_after:
            score += 80
        # Prefer full "New … door" prose and leaf specs over bare frame notes
        if _SPEC_NEW_RE.match(match.group(0)):
            score += 150
        if re.search(
            r"flush\s+door|honeycomb|fire-?rated|solid\s+timber\s+door|pintu\s+",
            cleaned,
            re.I,
        ):
            score += 120
        if re.search(r"\bframe\b", cleaned, re.I) and not re.search(
            r"\bdoor\b|\bpintu\b|\bshutter\b", cleaned, re.I
        ):
            score -= 50
        candidates.append((score, cleaned))

    if before.strip():
        for pattern in (_SPEC_NEW_RE, _SPEC_LEAF_RE, _SPEC_MALAY_RE, _SPEC_SIZE_LINE_RE):
            matches = list(pattern.finditer(before))
            if not matches:
                continue
            # Only keep specs immediately before this mark
            near = [m for m in matches if m.start() >= max(0, len(before) - 220)]
            for hit in near:
                _consider(hit, near_mark=True, from_after=False)

    if after.strip():
        for pattern in (_SPEC_NEW_RE, _SPEC_LEAF_RE, _SPEC_MALAY_RE, _SPEC_SIZE_LINE_RE):
            matches = list(pattern.finditer(after))
            if not matches:
                continue
            # Prefer the first spec after the mark
            _consider(matches[0], near_mark=True, from_after=True)

    if not candidates:
        for window, from_after in (
            (before[-500:] if before else "", False),
            (after[:500] if after else "", True),
        ):
            cleaned = _clean_specification(window)
            if cleaned and len(cleaned) > 60:
                score = len(cleaned) + (40 if from_after else 0)
                candidates.append((score, cleaned))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _parse_level_quantities(window: str) -> tuple[str | None, int | None]:
    return parse_level_quantities(window)


def _title_material(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip())
    lowered = cleaned.lower().replace(".", "")
    if re.fullmatch(r"m\s*s", lowered) or lowered in {"ms", "m s"}:
        return "Mild steel"
    if "mild steel" in lowered:
        return "Mild steel"
    if "zincalume" in lowered:
        return "Metal (Zincalume)"
    if "kayu keras" in lowered:
        return "Hardwood"
    if "papan lapis" in lowered:
        return "Plywood"
    if "honeycomb" in lowered:
        return "Honeycomb core flush"
    if re.fullmatch(r"alumn?", lowered) or lowered in {"alum", "aluminum"}:
        return "Aluminium"
    return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned


def _is_finish_only_material(value: str | None) -> bool:
    if not value:
        return False
    text = re.sub(r"\s+", " ", str(value).strip())
    if _FINISH_ONLY_RE.match(text):
        return True
    lowered = text.lower()
    # Veneer / paint / laminate recorded as if they were the leaf core
    if lowered.startswith("veneer lipping") and "plywood" not in lowered and "timber" not in lowered:
        return True
    return False


def _sanitize_frame_material(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    # Drop LLM commentary e.g. "(specified despite frameless door designation)"
    text = re.sub(r"\s*\([^)]*(?:frameless|despite|although|note)[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,;-")
    return text or None


def sanitize_materials(
    door_material: str | None,
    frame_material: str | None,
    *,
    description: str | None = None,
) -> tuple[str | None, str | None]:
    """
    Reject finish-only / frame-contaminated door materials and strip frame commentary.
    Optionally re-derive from the New… description when the stored value is bad.
    """
    door = (door_material or "").strip() or None
    frame = _sanitize_frame_material(frame_material)
    desc = (description or "").strip() or None

    bad_door = False
    if door:
        lowered = door.lower()
        if _is_finish_only_material(door):
            bad_door = True
        elif lowered in {"metal", "steel", "timber", "timber flush", "flush", "glass", "plywood"}:
            # Too vague / often stolen from "metal frame" or "glass door"
            bad_door = True
        elif (
            lowered == "aluminium"
            and desc
            and re.search(r"louvres?|louvers?", desc, re.I) is None
            and re.search(r"glass|tempered|laminated", desc, re.I)
        ):
            bad_door = True

    derived_door = derived_frame = None
    if desc and (bad_door or door is None or frame is None):
        derived_door, derived_frame = _materials(desc)

    if bad_door or door is None:
        door = derived_door if desc else (None if bad_door else door)
    if frame is None and derived_frame:
        frame = derived_frame

    if door and _is_finish_only_material(door):
        door = None
    return door, frame


def fire_rating_from_description(description: str | None) -> str | None:
    """Fire rating from a single New… / spec paragraph only (no neighbour bleed)."""
    if not description:
        return None
    text = re.sub(r"\s+", " ", str(description))
    # Prefer leaf / product phrasing over vision-panel glass alone
    preferred = re.search(
        r"(?:single\s+leaf|double\s+leaf|fire\s+rated\s+(?:door|camouflage)|"
        r"roller\s+shutter|rolling\s+shutter)"
        r"[^\n.]{0,80}?"
        r"((?:\d+(?:\.\d+)?)\s*-?\s*hours?\s*fire\s*rated|"
        r"(?:\d+)\s*-?\s*min(?:ute)?s?\s*fire\s*rated)",
        text,
        re.IGNORECASE,
    )
    if preferred:
        return _normalize_fire(preferred.group(1))
    # "2 hour fire rated door/camouflage/shutter" with rating before product
    before_product = re.search(
        r"((?:\d+(?:\.\d+)?)\s*-?\s*hours?\s*fire\s*rated|"
        r"(?:\d+)\s*-?\s*min(?:ute)?s?\s*fire\s*rated)"
        r"[^\n.]{0,40}?(?:door|camouflage|roller|rolling\s+shutter)",
        text,
        re.IGNORECASE,
    )
    if before_product:
        return _normalize_fire(before_product.group(1))
    return None


def sanitize_fire_rating(
    fire_rating: str | None,
    *,
    description: str | None = None,
) -> str | None:
    """
    Keep fire rating only when the door's own description supports it.
    Drops neighbour-bleed / LLM inventions when New… text has no leaf fire rating.
    """
    fire = (fire_rating or "").strip() or None
    desc = (description or "").strip() or None
    if not desc:
        return fire
    derived = fire_rating_from_description(desc)
    # New… prose is authoritative: if it has no leaf fire rating, clear any value
    if re.match(r"New\b", desc, re.I) and derived is None:
        return None
    if fire is None:
        return derived
    return fire


def _normalize_fire(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"\s+", " ", str(raw).strip())
    match = _FIRE_RATING_RE.search(text)
    if not match:
        return text
    if match.groupdict().get("hours"):
        hours = match.group("hours")
        return f"{hours} hour"
    if match.groupdict().get("hr_short"):
        return f"{match.group('hr_short')} hour"
    if match.groupdict().get("mins"):
        return f"{match.group('mins')} min"
    if match.groupdict().get("fr_code"):
        return match.group("fr_code")
    if match.groupdict().get("smoke"):
        return "Smoke"
    return text


def discover_door_marks(page_text: str) -> list[str]:
    """Return ordered unique door marks found in schedule text."""
    if not page_text:
        return []
    # Strip glue-type false positives (e.g. GLUE TYPE: D4)
    cleaned = _GLUE_FALSE_POSITIVE.sub(" ", page_text)

    ordered: list[str] = []
    seen: set[str] = set()

    # Prefer marks that appear with an inline size — high confidence
    for match in _MARK_SIZE_RE.finditer(cleaned):
        mark = _norm_mark(match.group("mark"))
        if mark in _SKIP_MARKS or mark in seen:
            continue
        seen.add(mark)
        ordered.append(mark)

    for match in _MARK_RE.finditer(cleaned):
        mark = _norm_mark(match.group("mark"))
        if mark in _SKIP_MARKS or mark in seen:
            continue
        # Bare D# marks are noisy in title blocks — require door-ish context nearby
        if re.fullmatch(r"D\d{1,3}[A-Z]?", mark):
            start = max(0, match.start() - 80)
            end = min(len(cleaned), match.end() + 200)
            window = cleaned[start:end]
            if not re.search(
                r"door|pintu|leaf|flush|frame|hinge|timber|metal|alum|fire|"
                r"schedule|location|bedroom|bath|entrance|\d{3,4}\s*[x×X]\s*\d{3,4}",
                window,
                re.I,
            ):
                continue
        seen.add(mark)
        ordered.append(mark)

    return ordered


def _first_dim_in(text: str) -> tuple[int | None, int | None, int | None]:
    for pattern in (_DIM_DESC_RE, _DIM_MALAY_RE, _DIM_XH_RE):
        for match in pattern.finditer(text):
            w = int(match.group("w"))
            h = int(match.group("h"))
            t_raw = match.groupdict().get("t")
            t = int(t_raw) if t_raw else None
            if not _plausible_opening(w, h):
                continue
            if t is not None and (t < 20 or t > 120):
                t = None
            return w, h, t
    return None, None, None


def _materials(window: str) -> tuple[str | None, str | None]:
    """
    Extract leaf/core and frame materials from a New… block (or short window).

    Prefer explicit core phrases. Never treat veneer lipping / paint / laminate
    finishes as the door material, and never treat "metal frame" as the leaf.
    """
    if not window:
        return None, None
    text = re.sub(r"\s+", " ", window)
    lower = text.lower()

    door_mat: str | None = None

    if re.search(r"alumn?\.?\s+louvres?|aluminium\s+louvres?|aluminum\s+louvres?", lower):
        door_mat = "Aluminium louvres"
    elif re.search(r"laminated\s+clear\s+tempered\s+glass", lower):
        door_mat = "Laminated clear tempered glass"
    elif re.search(r"\btempered\s+glass\b", lower) and re.search(
        r"\b(?:glass\s+door|sliding\s+glass|frameless\s+glass)\b", lower
    ):
        door_mat = "Tempered glass"
    elif re.search(r"solid\s+core\s+timber", lower):
        if re.search(r"wbp\s+plywood", lower):
            door_mat = "Solid core timber with WBP plywood surface"
        elif re.search(r"plywood\s+surface", lower) or re.search(
            r"\d+(?:\.\d+)?\s*mm\s+plywood", lower
        ):
            door_mat = "Solid core timber with plywood surface"
        else:
            door_mat = "Solid core timber"
        if "veneer lipping" in lower:
            door_mat = f"{door_mat} and veneer lipping"
    elif re.search(r"ordinary\s+plywood", lower):
        door_mat = "Ordinary plywood"
        if "veneer lipping" in lower:
            door_mat = "Ordinary plywood with veneer lipping"
    elif re.search(r"honeycomb\s+core", lower):
        door_mat = "Honeycomb core flush"
    elif re.search(r"hollow\s+metal", lower):
        door_mat = "Hollow metal"
    elif _SHUTTER_RE.search(text) and re.search(
        r"\bm\.?\s*s\.?\b|\bmild\s+steel\b|\bsteel\b", text, re.I
    ):
        door_mat = "Mild steel"
    elif re.search(r"kayu\s+keras", lower):
        door_mat = "Hardwood"
    elif re.search(r"papan\s+lapis", lower):
        door_mat = "Plywood"
    # Intentionally no bare "metal" / "timber" / "glass" / "veneer lipping" match —
    # those are finishes or frame words on these schedules.

    frame_mat: str | None = None
    frame_match = _FRAME_MATERIAL_RE.search(text)
    if frame_match:
        raw = frame_match.group(0)
        raw_lower = raw.lower()
        if "besi" in raw_lower:
            frame_mat = "Metal"
        elif "zincalume" in raw_lower:
            frame_mat = "Metal (Zincalume)"
        elif re.search(r"alumn?\.?|alum\.?|aluminium|aluminum", raw_lower):
            frame_mat = "Aluminium"
        elif "metal" in raw_lower or "steel" in raw_lower:
            frame_mat = "Metal"
        elif "timber" in raw_lower or "wood" in raw_lower:
            frame_mat = "Timber"
        else:
            frame_mat = _title_material(
                raw.replace(" frame", "").replace(" Frame", "")
            )

    return door_mat, frame_mat


def _configuration(window: str) -> str | None:
    if _SHUTTER_RE.search(window):
        return "roller shutter"
    leaf = _LEAF_RE.search(window)
    if not leaf:
        return None
    value = re.sub(r"\s+", " ", leaf.group(1).lower())
    if "shutter" in value:
        return "roller shutter"
    if "2 bukaan" in value or "dua daun" in value:
        return "double leaf"
    if "satu daun" in value:
        return "single leaf"
    if "bifold" in value or "bi-fold" in value:
        return "bi-fold"
    if "louvre" in value or "louver" in value:
        return "louvres"
    return value


def _empty_door(mark: str, page_number: int) -> dict[str, Any]:
    return {
        "door_type": mark,
        "item_number": None,
        "width_mm": None,
        "height_mm": None,
        "thickness_mm": None,
        "fire_rating": None,
        "door_material": None,
        "frame_material": None,
        "configuration": None,
        "location": None,
        "quantity_by_level": None,
        "quantity": None,
        "description": None,
        "remarks": None,
        "page": page_number,
        "source_pdf": None,
    }


def _apply_new_block(record: dict[str, Any], match: re.Match[str]) -> None:
    """Apply size + description + materials from one New… specification block."""
    w, h = int(match.group("w")), int(match.group("h"))
    t_raw = match.groupdict().get("t")
    t = int(t_raw) if t_raw else None
    if not _plausible_opening(w, h):
        return
    record["width_mm"] = w
    record["height_mm"] = h
    if t is not None and 20 <= t <= 120:
        record["thickness_mm"] = t

    block = re.sub(r"\s+", " ", match.group("block")).strip()
    record["description"] = block[:900]

    config = _configuration(block)
    if config:
        record["configuration"] = config
    door_mat, frame_mat = sanitize_materials(
        *_materials(block),
        description=block,
    )
    if door_mat:
        record["door_material"] = door_mat
    if frame_mat:
        record["frame_material"] = frame_mat
    if record.get("fire_rating") is None:
        fire = fire_rating_from_description(block)
        if fire:
            record["fire_rating"] = fire


def _find_new_block_for_mark(before: str, after: str) -> re.Match[str] | None:
    """
    Pair a door mark with its New… specification.

    CAD text dumps vary:
      - mark → TOTAL → New…  (common on later sheets)
      - New… → mark         (common on earlier sheets)
    Prefer the first New… after the mark; else the last New… before the mark.
    """
    after_hits = [
        m
        for m in _NEW_BLOCK_RE.finditer(after or "")
        if _plausible_opening(int(m.group("w")), int(m.group("h")))
    ]
    if after_hits:
        return after_hits[0]
    before_hits = [
        m
        for m in _NEW_BLOCK_RE.finditer(before or "")
        if _plausible_opening(int(m.group("w")), int(m.group("h")))
    ]
    if before_hits:
        return before_hits[-1]
    return None


def extract_doors_from_page_text(
    page_text: str,
    page_number: int,
) -> list[dict[str, Any]]:
    """
    Build door records purely from the PDF text layer.

    Works for description schedules, tabular WxH schedules, and Malay jadual pintu
    (synthetic TYPE-n when no marks exist).
    """
    if not page_text or not page_text.strip():
        return []

    doors: dict[str, dict[str, Any]] = {}

    # Inline mark+(WxH) first
    for match in _MARK_SIZE_RE.finditer(page_text):
        mark = _norm_mark(match.group("mark"))
        if mark in _SKIP_MARKS:
            continue
        w, h = int(match.group("w")), int(match.group("h"))
        if not _plausible_opening(w, h):
            continue
        record = doors.setdefault(mark, _empty_door(mark, page_number))
        record["width_mm"] = w
        record["height_mm"] = h

    marks = discover_door_marks(page_text)
    # Positions of first occurrence for windowing
    first_pos: dict[str, int] = {}
    for match in _MARK_RE.finditer(page_text):
        mark = _norm_mark(match.group("mark"))
        if mark not in marks:
            continue
        # Ignore "GLUE TYPE: D3" style false positions
        prefix = page_text[max(0, match.start() - 16) : match.start()]
        if re.search(r"glue\s*type\s*[:\-]?\s*$", prefix, re.I):
            continue
        if mark not in first_pos:
            first_pos[mark] = match.start()

    ordered = sorted(first_pos.items(), key=lambda item: item[1])
    for index, (mark, start) in enumerate(ordered):
        prev_end = ordered[index - 1][1] if index > 0 else 0
        end = (
            ordered[index + 1][1]
            if index + 1 < len(ordered)
            else min(len(page_text), start + 1200)
        )
        lookbehind_start = max(prev_end, start - 1000)
        before = page_text[lookbehind_start:start]
        after = page_text[start:end]
        primary = after if after.strip() else before

        record = doors.setdefault(mark, _empty_door(mark, page_number))

        # One New… block drives size + description together (avoids mismatched pairs)
        new_block = _find_new_block_for_mark(before, after)
        if new_block:
            _apply_new_block(record, new_block)
        elif record.get("width_mm") is None or record.get("height_mm") is None:
            # Fallback for tabular schedules without "New …" prose
            w = h = t = None
            w, h, t = _first_dim_in(after)
            if w is None:
                w, h, t = _first_dim_in(before)
            if w is not None and _plausible_opening(w, h):
                record["width_mm"] = w
                record["height_mm"] = h
                if t is not None and 20 <= t <= 120:
                    record["thickness_mm"] = t
            spec = extract_specification(before, after)
            if spec:
                record["description"] = spec

        if not record.get("configuration"):
            config = _configuration(before + " " + primary)
            if config:
                record["configuration"] = config

        # When a New… block exists, materials come only from that block (already
        # applied). Do not scan the mark-to-mark window — it bleeds neighbour text.
        if new_block is None:
            door_mat, frame_mat = _materials(before + " " + primary)
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

        # Fire rating: when a New… block exists, only trust that block (already
        # applied in _apply_new_block). Window scan bleeds neighbour FD ratings
        # onto non-fire D*/LD* marks (e.g. D8/D10/D11/LD1 ← FD "2 hour").
        if record.get("fire_rating") is None and new_block is None:
            fire = _FIRE_RATING_RE.search(before + " " + primary)
            if fire:
                record["fire_rating"] = _normalize_fire(fire.group(0))

        record["fire_rating"] = sanitize_fire_rating(
            record.get("fire_rating"),
            description=record.get("description"),
        )

        # Prefer qty lines immediately after the mark, before the next mark
        qty_window = page_text[start:end]
        by_level, overall = parse_level_quantities(qty_window)
        if record.get("quantity_by_level") is None and by_level:
            record["quantity_by_level"] = by_level
        if record.get("quantity") is None and overall is not None:
            record["quantity"] = overall

        remarks = extract_remarks(before, after)
        if remarks and not record.get("remarks"):
            record["remarks"] = remarks

        # Keep location free of remark-like noise
        loc = record.get("location")
        if loc and re.search(r"refer\s+to|by\s+others", str(loc), re.I):
            if not record.get("remarks"):
                record["remarks"] = str(loc).strip()
            record["location"] = None

    # Malay jadual without marks: synthesize TYPE-n from L x T blocks
    if not doors:
        blocks = list(_DIM_MALAY_RE.finditer(page_text))
        for index, match in enumerate(blocks, start=1):
            w, h = int(match.group("w")), int(match.group("h"))
            if not _plausible_opening(w, h):
                continue
            mark = f"TYPE{index}"
            start = match.start()
            end = blocks[index].start() if index < len(blocks) else min(len(page_text), start + 500)
            window = page_text[start:end]
            record = _empty_door(mark, page_number)
            record["width_mm"] = w
            record["height_mm"] = h
            door_mat, frame_mat = _materials(window)
            record["door_material"] = door_mat
            record["frame_material"] = frame_mat
            record["configuration"] = _configuration(window)
            by_level, overall = _parse_level_quantities(window)
            record["quantity_by_level"] = by_level
            record["quantity"] = overall
            record["description"] = extract_specification("", window) or _clean_specification(
                window[:500]
            )
            record["remarks"] = extract_remarks("", window)
            doors[mark] = record

    return list(doors.values())


def merge_door_records(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Union by door_type. Non-null fields in primary win; secondary fills gaps
    and contributes marks missing from primary.
    Longer description / specification text is preferred.
    """
    merged: dict[str, dict[str, Any]] = {}
    for source in (secondary, primary):
        for door in source:
            mark = str(door.get("door_type") or "").strip().upper()
            if not mark:
                continue
            if mark not in merged:
                merged[mark] = dict(door)
                continue
            current = merged[mark]
            for key, value in door.items():
                if key == "door_type":
                    continue
                if key == "description":
                    existing = current.get("description")
                    if value and (
                        existing is None
                        or (
                            len(str(value)) > len(str(existing))
                            and not (
                                str(existing).lower().lstrip().startswith("new ")
                                and not str(value).lower().lstrip().startswith("new ")
                            )
                        )
                    ):
                        current[key] = value
                    continue
                if key in {"width_mm", "height_mm", "thickness_mm", "quantity"}:
                    # Keep primary numeric values; only fill blanks from secondary
                    if current.get(key) is None and value is not None:
                        current[key] = value
                    continue
                if key == "remarks":
                    existing = current.get("remarks")
                    if value and (
                        existing is None or len(str(value)) > len(str(existing or ""))
                    ):
                        current[key] = value
                    continue
                if key in {"door_material", "frame_material"}:
                    # Never fill gaps with finish-only / commentary values
                    if current.get(key) is None and value is not None:
                        door_v, frame_v = sanitize_materials(
                            value if key == "door_material" else current.get("door_material"),
                            value if key == "frame_material" else current.get("frame_material"),
                            description=current.get("description") or door.get("description"),
                        )
                        filled = door_v if key == "door_material" else frame_v
                        if filled is not None:
                            current[key] = filled
                    continue
                if current.get(key) is None and value is not None:
                    current[key] = value
    for mark, current in merged.items():
        door_v, frame_v = sanitize_materials(
            current.get("door_material"),
            current.get("frame_material"),
            description=current.get("description"),
        )
        current["door_material"] = door_v
        current["frame_material"] = frame_v
        current["fire_rating"] = sanitize_fire_rating(
            current.get("fire_rating"),
            description=current.get("description"),
        )
    return list(merged.values())
