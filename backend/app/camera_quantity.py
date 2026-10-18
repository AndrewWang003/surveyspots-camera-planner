"""Infer how many cameras a single historical Camera marker represents."""

from __future__ import annotations

import re


CAMERA_REF_RE = re.compile(
    r"\bcams?(?:era)?s?\s*(?P<ids>\d+[a-z]?(?:\s*(?:&|,|and|\+)\s*\d+[a-z]?)+)",
    re.IGNORECASE,
)
REPEATED_CAMERA_REF_RE = re.compile(
    r"\bcams?(?:era)?s?\s*(?P<id>\d+[a-z]?)\b",
    re.IGNORECASE,
)
EQUIPMENT_COUNT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<count>\d{1,2})\s*(?:x\s*)?"
    r"(?P<equipment>hik|ipod|cctv|cameras?|cams?)\b",
    re.IGNORECASE,
)


def infer_camera_quantity(
    name: str,
    description: str = "",
    direction_count: int = 0,
) -> int:
    """Return how many cameras a single Camera marker should count as.

    This is a count signal, not an instruction to duplicate coordinates in the
    generated map.  Examples include ``Cam 4 & 5`` and ``1 Hik + 1 IPOD``.
    """
    text = " ".join(part for part in (name, description) if part)
    quantities = [1]

    equipment_total = sum(
        count
        for match in EQUIPMENT_COUNT_RE.finditer(text)
        if (count := int(match.group("count"))) <= 10
    )
    if equipment_total:
        quantities.append(equipment_total)

    for match in CAMERA_REF_RE.finditer(text):
        quantities.append(len(_camera_ids(match.group("ids"))))

    repeated_ids = {
        match.group("id").lower()
        for match in REPEATED_CAMERA_REF_RE.finditer(text)
    }
    if len(repeated_ids) > 1:
        quantities.append(len(repeated_ids))

    if direction_count > 1:
        quantities.append(direction_count)

    return max(quantities)


def _camera_ids(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item.lower()
            for item in re.findall(r"\d+[a-z]?", value, flags=re.IGNORECASE)
        )
    )
