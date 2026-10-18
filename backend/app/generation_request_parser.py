"""Parse read-only Point and Polygon geometries from generation requests."""

from __future__ import annotations

from .direction_geometry import extract_point_position
from .models import PinLocation, PolygonLocation
from .polygon_scope import extract_polygon_rings
from .raw_placemark_parser import RawPlacemarkRecord, extract_raw_placemarks
from .task_family import combine_task_family, infer_task_family


def parse_generation_request_geometries(
    kml_content: str | bytes,
) -> tuple[list[PinLocation], list[PolygonLocation]]:
    """Return request Points and valid closed Polygons without storing them."""
    pins: list[PinLocation] = []
    polygons: list[PolygonLocation] = []
    for placemark in extract_raw_placemarks(kml_content):
        position = extract_point_position(placemark)
        if position is not None and not _is_generated_camera_point(placemark):
            pins.append(
                PinLocation(
                    lat=position[1],
                    lng=position[0],
                    name=placemark.placemark_name,
                    description=placemark.description,
                    icon_color=placemark.style_definition.get("icon_color", ""),
                    task_family=_request_task_family(placemark),
                )
            )
        if "Polygon" not in placemark.geometry_types:
            continue
        outer_ring, inner_rings = extract_polygon_rings(placemark)
        if (
            len(outer_ring) < 4
            or outer_ring[0][:2] != outer_ring[-1][:2]
            or len({position[:2] for position in outer_ring[:-1]}) < 3
        ):
            continue
        polygons.append(
            PolygonLocation(
                name=placemark.placemark_name,
                description=placemark.description,
                outer_ring=outer_ring,
                inner_rings=inner_rings,
            )
        )
    return pins, polygons


def _is_generated_camera_point(placemark: RawPlacemarkRecord) -> bool:
    equipment_type = placemark.extended_data.get("equipment_type", "").lower()
    folder_text = " ".join(placemark.folder_path).lower()
    name = placemark.placemark_name.lower()
    return (
        equipment_type in {"camera_dir", "camera_omni"}
        or "reference cameras" in folder_text
        or name.startswith(("camera_dir", "camera_omni"))
    )


def _request_task_family(placemark: RawPlacemarkRecord) -> str:
    group_family = infer_task_family(" ".join(placemark.folder_path))
    item_family = infer_task_family(
        " ".join(
            (
                placemark.placemark_name,
                placemark.description,
            )
        )
    )
    return combine_task_family(group_family, item_family)
