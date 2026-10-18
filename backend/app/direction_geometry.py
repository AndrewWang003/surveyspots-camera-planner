"""Geometry helpers for preserving and analysing historical Camera lines."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TypeAlias

from .raw_placemark_parser import RawPlacemarkRecord


Position: TypeAlias = tuple[float, ...]


@dataclass(frozen=True)
class ThreePointDirectionAnalysis:
    """Derived comparison metadata for an unchanged three-point LineString."""

    boundary_a: Position
    camera_anchor: Position
    boundary_b: Position
    boundary_a_bearing: float
    boundary_b_bearing: float
    suggested_centre_bearing: float
    suggested_coverage_angle: float


def extract_point_position(placemark: RawPlacemarkRecord) -> Position | None:
    """Return the first valid KML Point position in ``(lng, lat, ...)`` order."""
    for geometry in placemark.geometry:
        if geometry["type"] != "Point":
            continue
        positions = _first_positions(geometry)
        if positions:
            return _valid_position(positions[0])
    return None


def extract_linestring_positions(
    placemark: RawPlacemarkRecord,
) -> tuple[Position, ...]:
    """Return the first LineString while preserving source coordinate order."""
    for geometry in placemark.geometry:
        if geometry["type"] != "LineString":
            continue
        positions = _first_positions(geometry)
        if not positions:
            return ()
        parsed = tuple(
            position
            for raw_position in positions
            if (position := _valid_position(raw_position)) is not None
        )
        return parsed if len(parsed) == len(positions) else ()
    return ()


def analyse_three_point_direction(
    positions: tuple[Position, ...],
) -> ThreePointDirectionAnalysis | None:
    """Analyse ``boundary_a -> camera_anchor -> boundary_b`` geometry.

    Source order does not imply left versus right. Circular vector arithmetic
    keeps north/south-facing lines and the 359/1-degree boundary continuous.
    """
    if len(positions) != 3:
        return None

    boundary_a, camera_anchor, boundary_b = positions
    bearing_a = bearing_degrees(camera_anchor, boundary_a)
    bearing_b = bearing_degrees(camera_anchor, boundary_b)
    coverage_angle = circular_distance_degrees(bearing_a, bearing_b)
    centre_bearing = circular_midpoint_degrees(bearing_a, bearing_b)
    if centre_bearing is None:
        return None

    return ThreePointDirectionAnalysis(
        boundary_a=boundary_a,
        camera_anchor=camera_anchor,
        boundary_b=boundary_b,
        boundary_a_bearing=bearing_a,
        boundary_b_bearing=bearing_b,
        suggested_centre_bearing=centre_bearing,
        suggested_coverage_angle=coverage_angle,
    )


def bearing_degrees(start: Position, end: Position) -> float:
    """Return clockwise bearing from north for KML ``(lng, lat)`` positions."""
    lng1, lat1 = start[:2]
    lng2, lat2 = end[:2]
    lat1_rad, lat2_rad = map(math.radians, (lat1, lat2))
    delta_lng = math.radians(lng2 - lng1)
    y = math.sin(delta_lng) * math.cos(lat2_rad)
    x = (
        math.cos(lat1_rad) * math.sin(lat2_rad)
        - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lng)
    )
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def circular_midpoint_degrees(first: float, second: float) -> float | None:
    """Return the circular angle bisector, or ``None`` for opposed vectors."""
    east = math.sin(math.radians(first)) + math.sin(math.radians(second))
    north = math.cos(math.radians(first)) + math.cos(math.radians(second))
    if math.hypot(east, north) < 1e-9:
        return None
    return (math.degrees(math.atan2(east, north)) + 360.0) % 360.0


def circular_distance_degrees(first: float, second: float) -> float:
    """Return the smallest separation between two bearings in degrees."""
    return abs((first - second + 180.0) % 360.0 - 180.0)


def haversine_metres(first: Position, second: Position) -> float:
    """Return ground distance between KML ``(lng, lat)`` positions."""
    lng1, lat1 = first[:2]
    lng2, lat2 = second[:2]
    radius_m = 6_371_000.0
    lat1_rad, lat2_rad = map(math.radians, (lat1, lat2))
    delta_lat = lat2_rad - lat1_rad
    delta_lng = math.radians(lng2 - lng1)
    haversine = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lng / 2.0) ** 2
    )
    return 2.0 * radius_m * math.asin(math.sqrt(haversine))


def _first_positions(geometry: dict) -> list[list[float | str]]:
    coordinate_sets = geometry.get("coordinate_sets", [])
    if not coordinate_sets:
        return []
    return coordinate_sets[0].get("positions", [])


def _valid_position(values: list[float | str]) -> Position | None:
    if len(values) < 2 or not all(isinstance(value, (int, float)) for value in values):
        return None
    position = tuple(float(value) for value in values)
    lng, lat = position[:2]
    if not (-180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0):
        return None
    return position
