"""Context-aware, versioned business classification for raw KML Placemarks."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Any

from .raw_placemark_parser import RawPlacemarkRecord
from .task_family import TASK_FAMILIES, combine_task_family, infer_task_family


BUSINESS_ROLES = {
    "site",
    "camera_location",
    "camera_direction",
    "atc_location",
    "atc_tube_line",
    "route",
    "reference_point",
    "unknown",
    "ignored_historical_polygon",
}
CAMERA_COUNT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<count>\d{1,2})\s*(?:x\s*)?(?:cameras?|cams?)\b",
    re.IGNORECASE,
)
CAMERA_NAME_RE = re.compile(r"\b(?:cam|camera|cameras)\b", re.IGNORECASE)
CAMERA_FOLDER_RE = re.compile(
    r"\b(?:cams?|cameras?)\b(?:\s+(?:locations?|positions?))?",
    re.IGNORECASE,
)
SITE_NAME_RE = re.compile(r"^(?:site|ic|station|ql|ped)\s*[.:#-]*\s*\d*", re.IGNORECASE)
NUMBERED_SITE_RE = re.compile(r"^\d+(?:\.\d+)?\s*[.)-]?\s*\S")
ROUTE_RE = re.compile(r"^(?:route\b|directions?\s+from\b)", re.IGNORECASE)
DIRECTION_NAME_RE = re.compile(r"\bdirection\b|^line\s*\d+", re.IGNORECASE)


@dataclass(frozen=True)
class ClassificationDecision:
    """One reproducible rule-engine classification suggestion."""

    placemark_index: int
    business_role: str
    task_family: str
    classification_confidence: float
    classification_reasons: tuple[str, ...]
    required_camera_count: int | None = None
    camera_count_source: str = ""
    classification_method: str = "rule_engine"
    classification_status: str = "suggested"
    model_version: str = ""
    decision_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class _GroupContext:
    task_family: str
    camera_context: bool
    site_context: bool
    route_context: bool
    explicit_camera_points: int
    three_point_lines: int
    point_count: int


def extract_required_camera_count(description: str) -> int | None:
    """Return an explicit Site camera requirement such as ``2 cameras``."""
    match = CAMERA_COUNT_RE.search(_plain_text(description))
    if not match:
        return None
    count = int(match.group("count"))
    return count if 1 <= count <= 50 else None


def is_explicit_camera_name(name: str) -> bool:
    """Return whether the Placemark name itself identifies a Camera."""
    return CAMERA_NAME_RE.search(_normalise(name)) is not None


def classify_raw_placemarks(
    placemarks: list[RawPlacemarkRecord],
    source_filename: str,
) -> list[ClassificationDecision]:
    """Classify Placemarks using file, Folder, group, name, and geometry context."""
    contexts = _build_group_contexts(placemarks, source_filename)
    decisions: list[ClassificationDecision] = []

    for placemark in placemarks:
        context = contexts[placemark.folder_path]
        decisions.append(_classify_one(placemark, context, source_filename))

    return decisions


def _build_group_contexts(
    placemarks: list[RawPlacemarkRecord],
    source_filename: str,
) -> dict[tuple[str, ...], _GroupContext]:
    grouped: dict[tuple[str, ...], list[RawPlacemarkRecord]] = defaultdict(list)
    for placemark in placemarks:
        grouped[placemark.folder_path].append(placemark)

    contexts: dict[tuple[str, ...], _GroupContext] = {}
    for folder_path, records in grouped.items():
        folder_text = " ".join(folder_path)
        group_task = infer_task_family(folder_text)
        if group_task == "unknown":
            group_task = infer_task_family(source_filename)

        explicit_camera_points = sum(
            1
            for record in records
            if "Point" in record.geometry_types
            and is_explicit_camera_name(record.placemark_name)
        )
        three_point_lines = sum(
            1
            for record in records
            if "LineString" in record.geometry_types
            and _line_position_count(record) == 3
        )
        normalised_folder = _normalise(folder_text)
        contexts[folder_path] = _GroupContext(
            task_family=group_task,
            camera_context=CAMERA_FOLDER_RE.search(normalised_folder) is not None,
            site_context=re.search(r"\bsites?\b", normalised_folder) is not None,
            route_context=any(
                ROUTE_RE.search(_normalise(segment)) is not None
                for segment in folder_path
            ),
            explicit_camera_points=explicit_camera_points,
            three_point_lines=three_point_lines,
            point_count=sum("Point" in record.geometry_types for record in records),
        )
    return contexts


def _classify_one(
    placemark: RawPlacemarkRecord,
    context: _GroupContext,
    source_filename: str,
) -> ClassificationDecision:
    name = _normalise(placemark.placemark_name)
    item_task = infer_task_family(placemark.placemark_name)
    task_family = combine_task_family(context.task_family, item_task)
    required_count = extract_required_camera_count(placemark.description)
    explicit_camera = is_explicit_camera_name(placemark.placemark_name)
    explicit_site = SITE_NAME_RE.search(name) is not None
    numbered_site = NUMBERED_SITE_RE.search(name) is not None
    named_route = ROUTE_RE.search(name) is not None

    if task_family == "atc":
        if "Point" in placemark.geometry_types:
            return _decision(
                placemark,
                "atc_location",
                task_family,
                0.99,
                "Point appears in an ATC Layer and identifies an ATC logger location.",
            )
        if "LineString" in placemark.geometry_types:
            return _decision(
                placemark,
                "atc_tube_line",
                task_family,
                0.99,
                "LineString appears in an ATC Layer and preserves a pneumatic tube line.",
            )

    if "Polygon" in placemark.geometry_types:
        return _decision(
            placemark,
            "ignored_historical_polygon",
            task_family,
            0.99,
            "Historical Polygon is preserved but excluded from Camera generation.",
        )

    if context.route_context or named_route:
        return _decision(
            placemark,
            "route",
            task_family,
            0.98,
            "Folder or Placemark name explicitly identifies route/directions data.",
        )

    if "LineString" in placemark.geometry_types:
        camera_line_context = (
            explicit_camera
            or context.camera_context
            or context.explicit_camera_points > 0
            or DIRECTION_NAME_RE.search(name) is not None
        )
        if camera_line_context and _line_position_count(placemark) == 3:
            return _decision(
                placemark,
                "camera_direction",
                task_family,
                0.94,
                "Three-point LineString appears in a Camera group; Point 2 is the Camera anchor.",
            )
        if context.site_context and _line_position_count(placemark) == 3:
            return _decision(
                placemark,
                "camera_direction",
                task_family,
                0.72,
                "Three-point LineString in a Site group is preserved as an advisory Camera direction; Point 2 is the Camera anchor and requires binding review.",
            )
        if camera_line_context:
            return _decision(
                placemark,
                "camera_direction",
                task_family,
                0.72,
                "Non-standard Camera direction LineString is preserved and requires anchor review.",
            )
        return _decision(
            placemark,
            "unknown",
            task_family,
            0.35,
            "LineString lacks sufficient Camera or Route context and requires review.",
        )

    if "Point" in placemark.geometry_types:
        if required_count is not None and not explicit_camera:
            return _decision(
                placemark,
                "site",
                task_family,
                0.99,
                "Description contains an explicit Camera quantity requirement for this Site.",
                required_count,
                "explicit_description",
            )
        if explicit_camera and (
            context.camera_context
            or context.explicit_camera_points > 0
            or context.three_point_lines > 0
        ):
            return _decision(
                placemark,
                "camera_location",
                task_family,
                0.98,
                "Camera name and group geometry identify a Camera location, even when the name includes a Site prefix.",
            )
        if explicit_site:
            return _decision(
                placemark,
                "site",
                task_family,
                0.98,
                "Placemark name explicitly identifies a Site, IC, Station, QL, or Ped location.",
            )
        if explicit_camera:
            return _decision(
                placemark,
                "camera_location",
                task_family,
                0.95,
                "Placemark name explicitly identifies a Camera location.",
            )
        if context.camera_context:
            return _decision(
                placemark,
                "camera_location",
                task_family,
                0.88,
                "Point appears in a Folder explicitly identified as Camera locations/positions.",
            )
        if context.site_context:
            return _decision(
                placemark,
                "site",
                task_family,
                0.92,
                "Point appears in a Folder explicitly identified as Sites.",
            )
        if numbered_site and context.task_family in {"ic", "od", "ipod"}:
            return _decision(
                placemark,
                "site",
                task_family,
                0.90,
                "Numbered Point appears in a Site task Folder without a Camera name.",
            )
        if (
            context.task_family in {"ic", "od", "ipod"}
            and context.explicit_camera_points == 0
            and context.three_point_lines == 0
        ):
            return _decision(
                placemark,
                "site",
                task_family,
                0.78,
                "Point appears in a Site task group containing no Camera points or direction lines.",
            )
        return _decision(
            placemark,
            "reference_point",
            task_family,
            0.45,
            "Point lacks an explicit Site or Camera identity and is retained for review.",
        )

    return _decision(
        placemark,
        "unknown",
        task_family,
        0.20,
        f"No supported business geometry was identified in {source_filename}.",
    )


def _decision(
    placemark: RawPlacemarkRecord,
    business_role: str,
    task_family: str,
    confidence: float,
    reason: str,
    required_camera_count: int | None = None,
    camera_count_source: str = "",
) -> ClassificationDecision:
    return ClassificationDecision(
        placemark_index=placemark.placemark_index,
        business_role=business_role,
        task_family=task_family,
        classification_confidence=confidence,
        classification_reasons=(reason,),
        required_camera_count=required_camera_count,
        camera_count_source=camera_count_source,
    )


def _line_position_count(placemark: RawPlacemarkRecord) -> int:
    for geometry in placemark.geometry:
        if geometry["type"] != "LineString":
            continue
        coordinate_sets = geometry.get("coordinate_sets", [])
        if coordinate_sets:
            return len(coordinate_sets[0].get("positions", []))
    return 0


def _plain_text(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def _normalise(value: str) -> str:
    return " ".join(value.lower().replace("\xa0", " ").replace("-", " ").split())
