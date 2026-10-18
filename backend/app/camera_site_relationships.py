"""Build explainable many-to-many relationships between Cameras and Sites."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .business_classifier import ClassificationDecision
from .direction_geometry import Position, extract_point_position, haversine_metres
from .observation_builder import CameraObservationCandidate
from .raw_placemark_parser import RawPlacemarkRecord
from .task_family import task_families_compatible


MAX_NEAREST_SITE_DISTANCE_M = 100.0
MAX_SEMANTIC_STATION_DISTANCE_M = 250.0
MIN_SEMANTIC_DISTANCE_MARGIN_M = 50.0
SITE_ID_RE = re.compile(
    r"^\s*(?P<kind>site|station|ic)\s*"
    r"(?P<identifier>\d+(?:\.\d+)?[a-z]?)\b",
    re.IGNORECASE,
)
BARE_SITE_ID_RE = re.compile(
    r"^\s*(?P<identifier>\d+(?:\.\d+)?[a-z]?)\b",
    re.IGNORECASE,
)
CAMERA_NAMED_ID_RE = re.compile(
    r"\b(?P<kind>station|ic)\s*"
    r"(?P<identifier>\d+(?:\.\d+)?[a-z]?)\s*[-:]?\s*"
    r"(?:[a-z]{1,3}\s+)?cams?\b",
    re.IGNORECASE,
)
GENERIC_CAMERA_FOLDER_RE = re.compile(
    r"\b(?:cams?|cameras?)\b(?:\s+(?:locations?|positions?))?",
    re.IGNORECASE,
)
SITE_PREFIX_RE = re.compile(
    r"^\s*site\s+(?P<references>.+?)(?:\s*[-–]\s*|\s+)cams?\b",
    re.IGNORECASE,
)
SITE_SUFFIX_RE = re.compile(
    r"\bcams?\b.*?[-–]\s*(?:for\s+)?sites?\s+"
    r"(?P<references>.+)$",
    re.IGNORECASE,
)
SITE_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?[a-z]?", re.IGNORECASE)


@dataclass(frozen=True)
class SiteObservationCandidate:
    placemark_index: int
    name: str
    task_family: str
    classification_confidence: float
    required_camera_count: int | None
    camera_count_source: str
    position: Position
    site_key: str | None


@dataclass(frozen=True)
class CameraSiteRelationshipCandidate:
    camera_placemark_index: int
    site_placemark_index: int
    relationship_role: str
    distance_m: float
    association_method: str
    is_named_association: bool
    relationship_confidence: float
    relationship_reasons: tuple[str, ...]


@dataclass(frozen=True)
class CameraSiteReviewCandidate:
    camera_placemark_index: int
    candidate_site_placemark_index: int | None
    review_code: str
    candidate_distance_m: float | None
    named_site_references: tuple[str, ...]
    review_reasons: tuple[str, ...]


@dataclass(frozen=True)
class CameraSiteRelationshipBundle:
    sites: tuple[SiteObservationCandidate, ...]
    relationships: tuple[CameraSiteRelationshipCandidate, ...]
    unmatched_named_references: tuple[tuple[int, tuple[str, ...]], ...]
    review_items: tuple[CameraSiteReviewCandidate, ...]


def site_key_from_name(name: str) -> str | None:
    """Return a namespaced Site identity such as ``site:4.5`` or ``ic:7``."""
    match = SITE_ID_RE.search(_normalise_space(name))
    if match:
        return _site_key(match.group("kind"), match.group("identifier"))
    match = BARE_SITE_ID_RE.search(_normalise_space(name))
    if match:
        return _site_key("site", match.group("identifier"))
    return None


def named_site_keys_from_camera(name: str) -> tuple[str, ...]:
    """Extract explicit Site, Station, or IC references from a Camera name."""
    normalised = _normalise_space(name)
    keys = [
        _site_key(match.group("kind"), match.group("identifier"))
        for match in CAMERA_NAMED_ID_RE.finditer(normalised)
    ]

    site_match = SITE_PREFIX_RE.search(normalised) or SITE_SUFFIX_RE.search(normalised)
    if site_match:
        keys.extend(
            _site_key("site", identifier)
            for identifier in SITE_NUMBER_RE.findall(site_match.group("references"))
        )
    return tuple(dict.fromkeys(keys))


def build_camera_site_relationship_bundle(
    placemarks: list[RawPlacemarkRecord],
    decisions: list[ClassificationDecision],
    camera_observations: tuple[CameraObservationCandidate, ...] | None = None,
) -> CameraSiteRelationshipBundle:
    """Build one primary Site and optional named fallback Sites per Camera."""
    decision_by_index = {decision.placemark_index: decision for decision in decisions}
    sites: list[SiteObservationCandidate] = []
    source_cameras: list[CameraObservationCandidate] = []

    for placemark in placemarks:
        decision = decision_by_index.get(placemark.placemark_index)
        if decision is None:
            continue
        position = extract_point_position(placemark)
        if position is None:
            continue
        if decision.business_role == "site":
            sites.append(
                SiteObservationCandidate(
                    placemark_index=placemark.placemark_index,
                    name=placemark.placemark_name,
                    task_family=decision.task_family,
                    classification_confidence=decision.classification_confidence,
                    required_camera_count=decision.required_camera_count,
                    camera_count_source=decision.camera_count_source,
                    position=position,
                    site_key=site_key_from_name(placemark.placemark_name),
                )
            )
        elif decision.business_role == "camera_location":
            source_cameras.append(
                CameraObservationCandidate(
                    placemark_index=placemark.placemark_index,
                    name=placemark.placemark_name,
                    task_family=decision.task_family,
                    classification_confidence=decision.classification_confidence,
                    position=position,
                    location_method="source_point",
                )
            )

    cameras = list(camera_observations) if camera_observations is not None else source_cameras

    sites_by_key: dict[str, list[SiteObservationCandidate]] = {}
    for site in sites:
        if site.site_key:
            sites_by_key.setdefault(site.site_key, []).append(site)

    relationships: list[CameraSiteRelationshipCandidate] = []
    unmatched: list[tuple[int, tuple[str, ...]]] = []
    review_items: list[CameraSiteReviewCandidate] = []
    for camera in cameras:
        camera_position = camera.position
        named_keys = named_site_keys_from_camera(camera.name)
        named_sites: list[SiteObservationCandidate] = []
        unmatched_keys: list[str] = []
        for key in named_keys:
            matching_sites = sites_by_key.get(key, [])
            if not matching_sites:
                unmatched_keys.append(key)
                continue
            named_sites.append(
                min(
                    matching_sites,
                    key=lambda site: (
                        haversine_metres(camera_position, site.position),
                        site.placemark_index,
                    ),
                )
            )
        named_sites = list(
            {site.placemark_index: site for site in named_sites}.values()
        )
        if unmatched_keys:
            unmatched.append((camera.placemark_index, tuple(unmatched_keys)))
            review_items.append(
                CameraSiteReviewCandidate(
                    camera_placemark_index=camera.placemark_index,
                    candidate_site_placemark_index=None,
                    review_code="unmatched_explicit_site_reference",
                    candidate_distance_m=None,
                    named_site_references=tuple(unmatched_keys),
                    review_reasons=(
                        "One or more explicit Site references in the Camera name could not be matched in the same source file.",
                    ),
                )
            )

        if named_sites:
            ranked = sorted(
                [
                    (
                        haversine_metres(camera_position, site.position),
                        site,
                    )
                    for site in named_sites
                ],
                key=lambda item: (item[0], item[1].placemark_index),
            )
            for index, (distance, site) in enumerate(ranked):
                role = "primary" if index == 0 else "fallback"
                relationships.append(
                    CameraSiteRelationshipCandidate(
                        camera_placemark_index=camera.placemark_index,
                        site_placemark_index=site.placemark_index,
                        relationship_role=role,
                        distance_m=distance,
                        association_method="explicit_name_nearest",
                        is_named_association=True,
                        relationship_confidence=0.99 if role == "primary" else 0.95,
                        relationship_reasons=(
                            "Camera name explicitly identifies this Site.",
                            (
                                "This is the nearest explicitly named Site."
                                if role == "primary"
                                else "A nearer explicitly named Site is the primary association."
                            ),
                        ),
                    )
                )
            continue

        compatible_sites = [
            site
            for site in sites
            if task_families_compatible(camera.task_family, site.task_family)
        ]
        if not sites:
            review_items.append(
                CameraSiteReviewCandidate(
                    camera_placemark_index=camera.placemark_index,
                    candidate_site_placemark_index=None,
                    review_code="no_site_candidates",
                    candidate_distance_m=None,
                    named_site_references=named_keys,
                    review_reasons=(
                        "The source file contains no classified Site Point for this Camera.",
                    ),
                )
            )
            continue
        if not compatible_sites:
            review_items.append(
                CameraSiteReviewCandidate(
                    camera_placemark_index=camera.placemark_index,
                    candidate_site_placemark_index=None,
                    review_code="no_compatible_site",
                    candidate_distance_m=None,
                    named_site_references=named_keys,
                    review_reasons=(
                        "The source file contains Sites, but none have a task family compatible with this Camera.",
                        "Cross-task automatic association is not allowed.",
                    ),
                )
            )
            continue
        ranked_compatible_sites = sorted(
            [
                (
                    haversine_metres(camera_position, site.position),
                    site,
                )
                for site in compatible_sites
            ],
            key=lambda item: (item[0], item[1].placemark_index),
        )
        distance, nearest_site = ranked_compatible_sites[0]
        camera_placemark = next(
            (
                placemark
                for placemark in placemarks
                if placemark.placemark_index == camera.placemark_index
            ),
            None,
        )
        placemarks_by_index = {
            placemark.placemark_index: placemark for placemark in placemarks
        }
        semantic_extended = _is_semantic_station_association(
            camera,
            camera_placemark,
            nearest_site,
            ranked_compatible_sites,
            placemarks_by_index,
        )
        if distance > MAX_NEAREST_SITE_DISTANCE_M and not semantic_extended:
            review_items.append(
                CameraSiteReviewCandidate(
                    camera_placemark_index=camera.placemark_index,
                    candidate_site_placemark_index=nearest_site.placemark_index,
                    review_code="nearest_site_outside_radius",
                    candidate_distance_m=distance,
                    named_site_references=named_keys,
                    review_reasons=(
                        "The nearest task-compatible Site is more than 100 metres from this Camera.",
                        "No stronger same-Station semantic evidence justified extended spatial association.",
                    ),
                )
            )
            continue
        camera_decision = decision_by_index.get(camera.placemark_index)
        if (
            camera_placemark is not None
            and camera_decision is not None
            and _should_keep_spatial_camera_unbound(
                camera_placemark,
                camera_decision,
            )
        ):
            review_items.append(
                CameraSiteReviewCandidate(
                    camera_placemark_index=camera.placemark_index,
                    candidate_site_placemark_index=nearest_site.placemark_index,
                    review_code="layer_without_site_context",
                    candidate_distance_m=distance,
                    named_site_references=named_keys,
                    review_reasons=(
                        "The Camera Layer was reviewed as Camera or Direction evidence without Site evidence.",
                        "The Camera remains available as nearby historical evidence but is not promoted to a primary Site relationship.",
                    ),
                )
            )
            continue
        relationships.append(
            CameraSiteRelationshipCandidate(
                camera_placemark_index=camera.placemark_index,
                site_placemark_index=nearest_site.placemark_index,
                relationship_role="primary",
                distance_m=distance,
                association_method="spatial_nearest",
                is_named_association=False,
                relationship_confidence=(
                    max(0.65, 0.94 - distance / 1000.0)
                    if not semantic_extended
                    else max(0.55, 0.82 - distance / 1000.0)
                ),
                relationship_reasons=(
                    "No explicit Site association was found in the Camera name.",
                    (
                        "This is the nearest task-compatible Site within 100 metres."
                        if not semantic_extended
                        else "The Camera is outside 100 metres but within the extended same-Station evidence radius."
                    ),
                    *(
                        (
                            "Extended association is supported by matching task family, matching marker colour, and a clear nearest-Site margin.",
                        )
                        if semantic_extended
                        else ()
                    ),
                ),
            )
        )

    return CameraSiteRelationshipBundle(
        sites=tuple(sites),
        relationships=tuple(relationships),
        unmatched_named_references=tuple(unmatched),
        review_items=tuple(review_items),
    )


def _site_key(kind: str, identifier: str) -> str:
    return f"{kind.lower()}:{identifier.lower()}"


def _normalise_space(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _should_keep_spatial_camera_unbound(
    placemark: RawPlacemarkRecord,
    decision: ClassificationDecision,
) -> bool:
    """Return whether reviewed Layer tags should prevent a spatial primary link."""
    review = (decision.decision_metadata or {}).get("human_layer_review") or {}
    review_tags = set(review.get("review_tags") or [])
    if not review_tags:
        return False
    if "site" in review_tags:
        return False
    if not review_tags.intersection({"camera", "direction"}):
        return False
    folder_text = " ".join(placemark.folder_path)
    return GENERIC_CAMERA_FOLDER_RE.search(_normalise_space(folder_text)) is None


def _is_semantic_station_association(
    camera: CameraObservationCandidate,
    camera_placemark: RawPlacemarkRecord | None,
    nearest_site: SiteObservationCandidate,
    ranked_sites: list[tuple[float, SiteObservationCandidate]],
    placemarks_by_index: dict[int, RawPlacemarkRecord],
) -> bool:
    if camera_placemark is None:
        return False
    nearest_distance = ranked_sites[0][0]
    if nearest_distance <= MAX_NEAREST_SITE_DISTANCE_M:
        return False
    if nearest_distance > MAX_SEMANTIC_STATION_DISTANCE_M:
        return False
    if not {camera.task_family, nearest_site.task_family}.issubset({"od", "ipod"}):
        return False
    if not _is_station_name(nearest_site.name):
        return False

    site_placemark = placemarks_by_index.get(nearest_site.placemark_index)
    if site_placemark is None:
        return False
    if _style_colour(camera_placemark.style_url) != _style_colour(
        site_placemark.style_url,
    ):
        return False

    if len(ranked_sites) > 1:
        next_distance = ranked_sites[1][0]
        if next_distance - nearest_distance < MIN_SEMANTIC_DISTANCE_MARGIN_M:
            return False
    return True


def _is_station_name(value: str) -> bool:
    return bool(re.search(r"\bstation\s+\w+", value, re.IGNORECASE))


def _style_colour(style_url: str) -> str:
    match = re.search(r"-([0-9A-Fa-f]{6})(?:-|$)", style_url)
    return match.group(1).upper() if match else ""
