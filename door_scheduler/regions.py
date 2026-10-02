"""Region codes used for access control (aligned with AAOS country folders)."""

from __future__ import annotations

# Code → display name (Malaysia, China, Vietnam, Taiwan, Thailand,
# Hong Kong, Philippines, Singapore, Indonesia)
REGIONS: dict[str, str] = {
    "MY": "Malaysia",
    "CH": "China",
    "VN": "Vietnam",
    "TW": "Taiwan",
    "TH": "Thailand",
    "HK": "Hong Kong",
    "PH": "Philippines",
    "SG": "Singapore",
    "ID": "Indonesia",
}

REGION_CODES: tuple[str, ...] = tuple(REGIONS.keys())


def normalize_region_code(code: str | None) -> str | None:
    if not code:
        return None
    value = str(code).strip().upper()
    return value if value in REGIONS else None


def normalize_region_list(codes: list[str] | None) -> list[str]:
    if not codes:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in codes:
        code = normalize_region_code(raw)
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def region_label(code: str | None) -> str:
    normalized = normalize_region_code(code)
    if not normalized:
        return "—"
    return f"{normalized} · {REGIONS[normalized]}"


def region_options() -> list[tuple[str, str]]:
    """Return (code, label) pairs for UI select boxes."""
    return [(code, f"{code} · {name}") for code, name in REGIONS.items()]
