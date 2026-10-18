"""Classify KML Polygons as Site scopes through Point containment evidence."""

from __future__ import annotations

from dataclasses import dataclass

from .business_classifier import ClassificationDecision
from .direction_geometry import Position, extract_point_position
from .raw_placemark_parser import RawPlacemarkRecord
from .task_family import task_families_compatible


@dataclass(frozen=True)
class SiteScopePolygonCandidate:
    placemark_index: int
    name: str
    task_family: str
    outer_ring: tuple[Position, ...]
    inner_rings: tuple[tuple[Position, ...], ...]
    distinct_outer_vertex_count: int
    is_closed: bool
    scope_status: str
    requires_review: bool
    style_evidence: dict
    classification_reasons: tuple[str, ...]


@dataclass(frozen=True)
class PolygonSiteMembershipCandidate:
    polygon_placemark_index: int
    site_placemark_index: int
    membership_method: str
    membership_confidence: float
    membership_reasons: tuple[str, ...]


@dataclass(frozen=True)
class PolygonScopeBundle:
    polygons: tuple[SiteScopePolygonCandidate, ...]
    memberships: tuple[PolygonSiteMembershipCandidate, ...]


def build_polygon_scope_bundle(
    placemarks: list[RawPlacemarkRecord],
    decisions: list[ClassificationDecision],
) -> PolygonScopeBundle:
    """Accept valid Polygons as Site scopes only when they contain Sites."""
    decision_by_index = {decision.placemark_index: decision for decision in decisions}
    sites = []
    for placemark in placemarks:
        decision = decision_by_index.get(placemark.placemark_index)
        position = extract_point_position(placemark)
        if decision and decision.business_role == "site" and position:
            sites.append((placemark, decision, position))

    polygons: list[SiteScopePolygonCandidate] = []
    memberships: list[PolygonSiteMembershipCandidate] = []
    for placemark in placemarks:
        if "Polygon" not in placemark.geometry_types:
            continue
        decision = decision_by_index.get(placemark.placemark_index)
        task_family = decision.task_family if decision else "unknown"
        outer_ring, inner_rings = extract_polygon_rings(placemark)
        is_closed = len(outer_ring) >= 4 and outer_ring[0][:2] == outer_ring[-1][:2]
        distinct_vertices = len({position[:2] for position in outer_ring[:-1]}) if is_closed else 0
        valid = is_closed and distinct_vertices >= 3
        contained_sites = []
        if valid:
            contained_sites = [
                site
                for site in sites
                if task_families_compatible(task_family, site[1].task_family)
                and point_is_in_polygon(site[2], outer_ring, inner_rings)
            ]

        if not valid:
            status = "invalid_geometry_review"
            reasons = ("Polygon outer ring is not a valid closed area.",)
        elif not contained_sites:
            status = "unbound_review"
            reasons = (
                "Polygon geometry is valid but contains no task-compatible Site Point.",
            )
        else:
            status = "accepted_site_scope"
            reasons = (
                f"Polygon contains {len(contained_sites)} task-compatible Site Point(s).",
                "A Site scope may contain multiple independent Sites.",
            )

        polygons.append(
            SiteScopePolygonCandidate(
                placemark_index=placemark.placemark_index,
                name=placemark.placemark_name,
                task_family=task_family,
                outer_ring=outer_ring,
                inner_rings=inner_rings,
                distinct_outer_vertex_count=distinct_vertices,
                is_closed=is_closed,
                scope_status=status,
                requires_review=status != "accepted_site_scope",
                style_evidence={
                    key: placemark.style_definition.get(key, "")
                    for key in (
                        "resolved_style_id",
                        "line_color",
                        "line_width",
                        "poly_color",
                        "poly_fill",
                        "poly_outline",
                    )
                },
                classification_reasons=reasons,
            )
        )
        for site, _, _ in contained_sites:
            memberships.append(
                PolygonSiteMembershipCandidate(
                    polygon_placemark_index=placemark.placemark_index,
                    site_placemark_index=site.placemark_index,
                    membership_method="point_in_polygon",
                    membership_confidence=0.99,
                    membership_reasons=(
                        "Site Point is inside or on the boundary of the Polygon.",
                    ),
                )
            )

    return PolygonScopeBundle(tuple(polygons), tuple(memberships))


def extract_polygon_rings(
    placemark: RawPlacemarkRecord,
) -> tuple[tuple[Position, ...], tuple[tuple[Position, ...], ...]]:
    for geometry in placemark.geometry:
        if geometry["type"] != "Polygon":
            continue
        outer: tuple[Position, ...] = ()
        inner: list[tuple[Position, ...]] = []
        for coordinate_set in geometry.get("coordinate_sets", []):
            positions = tuple(
                tuple(float(value) for value in position)
                for position in coordinate_set.get("positions", [])
                if len(position) >= 2
                and all(isinstance(value, (int, float)) for value in position)
            )
            if coordinate_set.get("boundary_role") == "outer" and not outer:
                outer = positions
            elif coordinate_set.get("boundary_role") == "inner":
                inner.append(positions)
        return outer, tuple(inner)
    return (), ()


def point_is_in_polygon(
    point: Position,
    outer_ring: tuple[Position, ...],
    inner_rings: tuple[tuple[Position, ...], ...] = (),
) -> bool:
    """Return true for points inside or on the boundary, excluding holes."""
    if not _point_in_ring(point, outer_ring):
        return False
    return not any(_point_in_ring(point, ring) for ring in inner_rings)


def _point_in_ring(point: Position, ring: tuple[Position, ...]) -> bool:
    x, y = point[:2]
    inside = False
    for first, second in zip(ring, ring[1:]):
        x1, y1 = first[:2]
        x2, y2 = second[:2]
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) < 1e-12 and min(x1, x2) <= x <= max(x1, x2) and min(
            y1, y2
        ) <= y <= max(y1, y2):
            return True
        if (y1 > y) != (y2 > y):
            intersection_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < intersection_x:
                inside = not inside
    return inside
