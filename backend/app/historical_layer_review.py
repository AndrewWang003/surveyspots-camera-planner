"""Apply human-reviewed historical Layer scope without altering raw evidence."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace

from .business_classifier import ClassificationDecision
from .raw_placemark_parser import RawPlacemarkRecord


SELECTABLE_LAYER_FAMILIES = {"ic", "od", "ipod", "hik", "atc", "site", "unknown"}
TASK_FAMILY_OVERRIDES = {"ic", "od", "ipod", "hik", "atc", "unknown"}
SELECTABLE_LAYER_TAGS = {
    "site",
    "camera",
    "direction",
    "polygon",
    "route",
    "atc",
}


@dataclass(frozen=True)
class LayerReviewSelection:
    """Human-reviewed Layer scope and data-type hints."""

    selected_family: str
    review_tags: tuple[str, ...] = ()


LayerReviewInput = dict[str, str | LayerReviewSelection]


def layer_path_for_placemark(placemark: RawPlacemarkRecord) -> str:
    """Return the same stable Layer path shown by the upload preview."""
    return " / ".join(placemark.folder_path) or "(root)"


def filter_selected_placemarks(
    placemarks: list[RawPlacemarkRecord],
    selected_layers: LayerReviewInput | None,
) -> list[RawPlacemarkRecord]:
    """Return only Placemarks in reviewed Layers, or all when no review exists."""
    if selected_layers is None:
        return placemarks
    return [
        placemark
        for placemark in placemarks
        if layer_path_for_placemark(placemark) in selected_layers
    ]


def apply_layer_review_overrides(
    placemarks: list[RawPlacemarkRecord],
    decisions: list[ClassificationDecision],
    selected_layers: LayerReviewInput | None,
) -> list[ClassificationDecision]:
    """Apply reviewed task/role guidance while retaining rule provenance."""
    if selected_layers is None:
        return decisions

    placemark_by_index = {
        placemark.placemark_index: placemark for placemark in placemarks
    }
    reviewed: list[ClassificationDecision] = []
    for decision in decisions:
        placemark = placemark_by_index[decision.placemark_index]
        layer_path = layer_path_for_placemark(placemark)
        review_selection = normalise_layer_review_selection(selected_layers[layer_path])
        selected_family = review_selection.selected_family
        review_tags = review_selection.review_tags
        metadata = dict(decision.decision_metadata or {})
        metadata["human_layer_review"] = {
            "layer_path": layer_path,
            "selected_family": selected_family,
            "review_tags": list(review_tags),
        }
        role = decision.business_role
        confidence = decision.classification_confidence
        reasons = (
            *decision.classification_reasons,
            _override_reason(selected_family, review_tags),
        )
        task_family = decision.task_family

        if selected_family in TASK_FAMILY_OVERRIDES:
            task_family = selected_family
        elif (
            selected_family == "site"
            and "Point" in placemark.geometry_types
            and role not in {"camera_location"}
        ):
            role = "site"
            confidence = max(confidence, 0.95)

        if review_tags:
            role, confidence, tag_reason = _apply_review_tag_role_hint(
                placemark,
                role,
                confidence,
                review_tags,
            )
            if tag_reason:
                reasons = (*reasons, tag_reason)

        reviewed.append(
            replace(
                decision,
                business_role=role,
                task_family=task_family,
                classification_confidence=confidence,
                classification_reasons=reasons,
                classification_method="rule_engine_with_human_layer_review",
                decision_metadata=metadata,
            )
        )
    return reviewed


def validate_selected_layers(selected_layers: LayerReviewInput) -> None:
    """Reject unsupported review values before any database write."""
    normalised = {
        layer_path: normalise_layer_review_selection(selection)
        for layer_path, selection in selected_layers.items()
    }
    unsupported = {
        selection.selected_family
        for selection in normalised.values()
        if selection.selected_family not in SELECTABLE_LAYER_FAMILIES
    }
    if unsupported:
        raise ValueError(
            "Unsupported historical Layer classification: "
            + ", ".join(sorted(unsupported))
        )
    unsupported_tags = {
        tag
        for selection in normalised.values()
        for tag in selection.review_tags
        if tag not in SELECTABLE_LAYER_TAGS
    }
    if unsupported_tags:
        raise ValueError(
            "Unsupported historical Layer review tag: "
            + ", ".join(sorted(unsupported_tags))
        )


def normalise_layer_review_selection(
    selection: str | LayerReviewSelection,
) -> LayerReviewSelection:
    """Return a consistent review selection object for old and new callers."""
    if isinstance(selection, LayerReviewSelection):
        return selection
    return LayerReviewSelection(selected_family=selection, review_tags=())


def _override_reason(selected_family: str, review_tags: tuple[str, ...]) -> str:
    tag_text = ""
    if review_tags:
        tag_text = " Allowed data tags: " + ", ".join(review_tags) + "."
    if selected_family == "site":
        return "Human Layer review identified this Folder as Site evidence." + tag_text
    return (
        "Human Layer review set the task family to "
        f"{selected_family.upper()}." + tag_text
    )


def _apply_review_tag_role_hint(
    placemark: RawPlacemarkRecord,
    role: str,
    confidence: float,
    review_tags: tuple[str, ...],
) -> tuple[str, float, str]:
    """Use human data tags only when they clearly disambiguate a Placemark."""
    tag_set = set(review_tags)
    if "Point" in placemark.geometry_types:
        if "camera" in tag_set and "site" not in tag_set:
            return (
                "camera_location",
                max(confidence, 0.93),
                "Human Layer tags indicate Point Placemarks in this Layer are Camera locations.",
            )
        if "site" in tag_set and "camera" not in tag_set and role != "camera_location":
            return (
                "site",
                max(confidence, 0.93),
                "Human Layer tags indicate Point Placemarks in this Layer are Sites.",
            )
    if "LineString" in placemark.geometry_types:
        if "direction" in tag_set and "route" not in tag_set:
            return (
                "camera_direction",
                max(confidence, 0.90),
                "Human Layer tags indicate LineString Placemarks in this Layer are Camera directions.",
            )
        if "route" in tag_set and "direction" not in tag_set:
            return (
                "route",
                max(confidence, 0.90),
                "Human Layer tags indicate LineString Placemarks in this Layer are Routes.",
            )

    return role, confidence, ""
