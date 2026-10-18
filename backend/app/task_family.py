"""Shared task-family inference and compatibility rules."""

from __future__ import annotations

import re


TASK_FAMILIES = {"ic", "od", "ipod", "hik", "atc", "unknown", "mixed"}


def infer_task_family(text: str) -> str:
    """Infer a task family from user-visible KML text."""
    normalised = _normalise(text)
    tokens = set(normalised.split())
    if "atc" in tokens or re.search(r"\bautomatic traffic counter\b", normalised):
        return "atc"
    if re.search(r"\bip\s*od\b|\bipod\b", normalised):
        return "ipod"
    if tokens.intersection({"hik", "hikvision", "cctv"}):
        return "hik"
    if tokens.intersection({"od", "origin", "destination"}):
        return "od"
    if tokens.intersection(
        {"ic", "intersection", "queue", "ql", "bicycle", "bike", "pedestrian", "ped"}
    ):
        return "ic"
    return "unknown"


def combine_task_family(group_family: str, item_family: str) -> str:
    """Combine group and item task-family hints."""
    if item_family == "unknown":
        return group_family
    if group_family == "unknown" or group_family == item_family:
        return item_family
    if {group_family, item_family} == {"od", "ipod"}:
        return "mixed"
    return "mixed"


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def task_families_compatible(first: str, second: str) -> bool:
    """Return whether two task families may share deterministic evidence."""
    if first == second:
        return True
    flexible = {"unknown", "mixed"}
    if first in flexible or second in flexible:
        return True
    if {first, second}.issubset({"od", "ipod"}):
        return True
    return False
