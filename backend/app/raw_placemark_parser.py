"""Lossless raw Placemark extraction for KML ingestion.

This module deliberately does not assign business roles. It preserves source
context and geometry so classification rules can be rerun without re-uploading
or reconstructing information from the derived ``survey_history`` table.
"""

from __future__ import annotations

from dataclasses import dataclass
import xml.etree.ElementTree as ET
from typing import Any


GEOMETRY_TAGS = {
    "Point",
    "LineString",
    "LinearRing",
    "Polygon",
    "MultiGeometry",
    "Model",
}


@dataclass(frozen=True)
class RawPlacemarkRecord:
    """One Placemark with its unclassified source context and geometry."""

    placemark_index: int
    folder_path: tuple[str, ...]
    placemark_name: str
    description: str
    style_url: str
    style_id: str
    style_definition: dict[str, Any]
    extended_data: dict[str, str]
    geometry_types: tuple[str, ...]
    geometry: tuple[dict[str, Any], ...]
    raw_coordinates: tuple[str, ...]
    raw_kml_fragment: str


def extract_raw_placemarks(kml_content: str | bytes) -> list[RawPlacemarkRecord]:
    """Extract every Placemark without applying business classification."""
    source = kml_content.encode("utf-8") if isinstance(kml_content, str) else kml_content
    root = ET.fromstring(source)
    style_definitions = _extract_style_definitions(root)
    placemarks: list[RawPlacemarkRecord] = []

    for index, (placemark, folder_path) in enumerate(_walk_placemarks(root, ())):
        style_url = _child_text(placemark, "styleUrl")
        style_id = style_url.removeprefix("#") if style_url.startswith("#") else style_url
        geometry = tuple(_extract_geometry(placemark))
        geometry_types = tuple(dict.fromkeys(item["type"] for item in geometry))
        raw_coordinates = tuple(
            _normalise_coordinate_text(element.text)
            for element in placemark.iter()
            if _local_name(element.tag) == "coordinates" and element.text
        )

        placemarks.append(
            RawPlacemarkRecord(
                placemark_index=index,
                folder_path=folder_path,
                placemark_name=_child_text(placemark, "name"),
                description=_child_text(placemark, "description"),
                style_url=style_url,
                style_id=style_id,
                style_definition=style_definitions.get(style_id, {}),
                extended_data=_extract_extended_data(placemark),
                geometry_types=geometry_types,
                geometry=geometry,
                raw_coordinates=raw_coordinates,
                raw_kml_fragment=ET.tostring(placemark, encoding="unicode"),
            )
        )

    return placemarks


def _walk_placemarks(
    element: ET.Element,
    folder_path: tuple[str, ...],
):
    if _local_name(element.tag) == "Folder":
        folder_name = _child_text(element, "name")
        if folder_name:
            folder_path = (*folder_path, folder_name)

    if _local_name(element.tag) == "Placemark":
        yield element, folder_path

    for child in element:
        yield from _walk_placemarks(child, folder_path)


def _extract_geometry(placemark: ET.Element) -> list[dict[str, Any]]:
    geometry: list[dict[str, Any]] = []
    for element in placemark.iter():
        geometry_type = _local_name(element.tag)
        if geometry_type not in GEOMETRY_TAGS:
            continue

        coordinate_sets = []
        for coordinates in element.iter():
            if _local_name(coordinates.tag) != "coordinates" or not coordinates.text:
                continue
            raw_text = _normalise_coordinate_text(coordinates.text)
            coordinate_sets.append(
                {
                    "raw": raw_text,
                    "positions": _parse_coordinate_positions(raw_text),
                    "boundary_role": (
                        _polygon_boundary_role(element, coordinates)
                        if geometry_type == "Polygon"
                        else ""
                    ),
                }
            )

        geometry.append(
            {
                "type": geometry_type,
                "attributes": dict(element.attrib),
                "coordinate_sets": coordinate_sets,
                "raw_xml": ET.tostring(element, encoding="unicode"),
            }
        )
    return geometry


def _parse_coordinate_positions(raw_text: str) -> list[list[float | str]]:
    positions: list[list[float | str]] = []
    for raw_position in raw_text.split():
        values: list[float | str] = []
        for raw_value in raw_position.split(","):
            try:
                values.append(float(raw_value))
            except ValueError:
                values.append(raw_value)
        positions.append(values)
    return positions


def _extract_extended_data(placemark: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for element in placemark.iter():
        element_type = _local_name(element.tag)
        name = element.get("name", "")
        if element_type == "Data" and name:
            value = next(
                (
                    child.text.strip()
                    for child in element
                    if _local_name(child.tag) == "value" and child.text
                ),
                "",
            )
            values[name] = value
        elif element_type == "SimpleData" and name and element.text:
            values[name] = element.text.strip()
    return values


def _extract_style_definitions(root: ET.Element) -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    for element in root.iter():
        element_type = _local_name(element.tag)
        style_id = element.get("id", "")
        if element_type not in {"Style", "StyleMap"} or not style_id:
            continue

        definitions[style_id] = {
            "type": element_type,
            "icon_href": _descendant_text(element, "href"),
            "icon_color": _descendant_text(element, "IconStyle", "color"),
            "line_color": _descendant_text(element, "LineStyle", "color"),
            "line_width": _descendant_text(element, "LineStyle", "width"),
            "poly_color": _descendant_text(element, "PolyStyle", "color"),
            "poly_fill": _descendant_text(element, "PolyStyle", "fill"),
            "poly_outline": _descendant_text(element, "PolyStyle", "outline"),
            "resolved_style_id": "",
            "normal_style_id": _style_map_normal_reference(element),
            "raw_xml": ET.tostring(element, encoding="unicode"),
        }
    for definition in definitions.values():
        normal_style_id = definition.get("normal_style_id", "")
        resolved = definitions.get(normal_style_id)
        if not resolved:
            continue
        definition["resolved_style_id"] = normal_style_id
        for key in (
            "icon_href",
            "icon_color",
            "line_color",
            "line_width",
            "poly_color",
            "poly_fill",
            "poly_outline",
        ):
            definition[key] = resolved.get(key, "")
    return definitions


def _style_map_normal_reference(element: ET.Element) -> str:
    if _local_name(element.tag) != "StyleMap":
        return ""
    for pair in element:
        if _local_name(pair.tag) != "Pair":
            continue
        if _child_text(pair, "key") != "normal":
            continue
        return _child_text(pair, "styleUrl").removeprefix("#")
    return ""


def _polygon_boundary_role(
    polygon: ET.Element,
    coordinates: ET.Element,
) -> str:
    for boundary in polygon.iter():
        boundary_type = _local_name(boundary.tag)
        if boundary_type not in {"outerBoundaryIs", "innerBoundaryIs"}:
            continue
        if any(descendant is coordinates for descendant in boundary.iter()):
            return "outer" if boundary_type == "outerBoundaryIs" else "inner"
    return ""


def _descendant_text(
    element: ET.Element,
    parent_name: str,
    child_name: str | None = None,
) -> str:
    for descendant in element.iter():
        if _local_name(descendant.tag) != parent_name:
            continue
        if child_name is None:
            return descendant.text.strip() if descendant.text else ""
        for child in descendant:
            if _local_name(child.tag) == child_name and child.text:
                return child.text.strip()
    return ""


def _child_text(element: ET.Element, child_name: str) -> str:
    for child in element:
        if _local_name(child.tag) == child_name and child.text:
            return child.text.strip()
    return ""


def _normalise_coordinate_text(value: str) -> str:
    return " ".join(value.split())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
