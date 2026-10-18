"""KML generator with dual-buffer opacity and standardised icons.

Builds KML entirely with :mod:`xml.etree.ElementTree` for full control
over KML-specific elements (``<heading>``, ABGR colour strings, etc.).
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import Final

from app.config import settings
from app.models import ClusteredPoint, EquipmentType, PinLocation
from app.normalized_generation import NormalizedGenerationOutput

# ---------------------------------------------------------------------------
# KML namespace
# ---------------------------------------------------------------------------
KML_NS: Final[str] = "http://www.opengis.net/kml/2.2"
GENERATED_CAMERA_ICON_HREF: Final[str] = (
    "https://www.gstatic.com/mapspro/images/stock/503-wht-blank_maps.png"
)
GENERATED_CAMERA_ICON_COLOR: Final[str] = "ff000000"
GENERATED_CAMERA_MYMAPS_STYLE_ID: Final[str] = "icon-1535-000000-nodesc"
GENERATED_CAMERA_COLOR_STYLE_PREFIX: Final[str] = "generated-camera"
GENERATED_DIRECTION_LINE_COLOR: Final[str] = "ff00ffff"
GENERATED_DIRECTION_LINE_WIDTH: Final[str] = "3.0"
DEFAULT_LOOKAT_RANGE_M: Final[float] = 500.0
MIN_LOOKAT_RANGE_M: Final[float] = 350.0
LOOKAT_RANGE_PADDING: Final[float] = 2.4

# ---------------------------------------------------------------------------
# Style definitions per equipment type and opacity zone
# ---------------------------------------------------------------------------
# Each entry:  (icon_href, core_color_abgr, fade_color_abgr, is_polygon)
_STYLE_DEFS: dict[EquipmentType, dict[str, str | bool]] = {
    EquipmentType.NODE: {
        "icon": "http://maps.google.com/mapfiles/kml/paddle/blu-circle.png",
        "core_color": "ffff6614",
        "fade_color": "66ff6614",
        "is_polygon": False,
    },
    EquipmentType.ZONE: {
        "icon": "http://maps.google.com/mapfiles/kml/paddle/red-square.png",
        "core_color": "ff00ff00",  # solid green
        "fade_color": "6600ff00",
        "is_polygon": False,
    },
    EquipmentType.CAMERA_DIR: {
        "icon": GENERATED_CAMERA_ICON_HREF,
        "core_color": GENERATED_CAMERA_ICON_COLOR,
        "fade_color": GENERATED_CAMERA_ICON_COLOR,
        "is_polygon": False,
        "hotspot_x": "0.5",
        "hotspot_xunits": "fraction",
        "hotspot_y": "0.5",
        "hotspot_yunits": "fraction",
    },
    EquipmentType.CAMERA_OMNI: {
        "icon": GENERATED_CAMERA_ICON_HREF,
        "core_color": GENERATED_CAMERA_ICON_COLOR,
        "fade_color": GENERATED_CAMERA_ICON_COLOR,
        "is_polygon": False,
        "hotspot_x": "0.5",
        "hotspot_xunits": "fraction",
        "hotspot_y": "0.5",
        "hotspot_yunits": "fraction",
    },
}


def _style_id(eq_type: EquipmentType, zone: str) -> str:
    """Return a deterministic style ID, e.g. ``node_core``."""
    if eq_type in (EquipmentType.CAMERA_DIR, EquipmentType.CAMERA_OMNI):
        return GENERATED_CAMERA_MYMAPS_STYLE_ID
    return f"{eq_type.value}_{zone}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    """Create a sub-element and optionally set its text."""
    elem = ET.SubElement(parent, tag)
    if text is not None:
        elem.text = text
    return elem


def _haversine_m(start: tuple[float, float], end: tuple[float, float]) -> float:
    """Return approximate ground distance between two lat/lng points."""
    radius_m = 6_371_000.0
    lat1, lng1 = map(math.radians, start)
    lat2, lng2 = map(math.radians, end)
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    hav = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    return 2 * radius_m * math.asin(math.sqrt(hav))


def _site_viewpoint(pins: list[PinLocation]) -> tuple[float, float, float] | None:
    """Return a KML LookAt centre/range from uploaded site pins."""
    if not pins:
        return None

    min_lat = min(pin.lat for pin in pins)
    max_lat = max(pin.lat for pin in pins)
    min_lng = min(pin.lng for pin in pins)
    max_lng = max(pin.lng for pin in pins)
    center_lat = (min_lat + max_lat) / 2.0
    center_lng = (min_lng + max_lng) / 2.0

    if len(pins) == 1:
        return center_lat, center_lng, DEFAULT_LOOKAT_RANGE_M

    farthest_m = max(
        _haversine_m((center_lat, center_lng), (pin.lat, pin.lng))
        for pin in pins
    )
    view_range_m = max(MIN_LOOKAT_RANGE_M, farthest_m * LOOKAT_RANGE_PADDING)
    return center_lat, center_lng, view_range_m


def _add_look_at(
    feature: ET.Element,
    lat: float,
    lng: float,
    range_m: float,
) -> None:
    """Attach a KML LookAt view hint to a Document or Folder."""
    look_at = _sub(feature, "LookAt")
    _sub(look_at, "longitude", f"{lng:.8f}")
    _sub(look_at, "latitude", f"{lat:.8f}")
    _sub(look_at, "altitude", "0")
    _sub(look_at, "heading", "0")
    _sub(look_at, "tilt", "0")
    _sub(look_at, "range", f"{range_m:.1f}")
    _sub(look_at, "altitudeMode", "clampToGround")


def _add_shared_styles(document: ET.Element) -> None:
    """Add shared ``<Style>`` blocks to the KML ``<Document>``."""
    added_style_maps: set[str] = set()
    for eq_type, props in _STYLE_DEFS.items():
        for zone, color_key in (("core", "core_color"), ("fade", "fade_color")):
            color = str(props[color_key])
            style_id = _style_id(eq_type, zone)

            if eq_type in (EquipmentType.CAMERA_DIR, EquipmentType.CAMERA_OMNI):
                if style_id in added_style_maps:
                    continue
                _add_mymaps_icon_style_map(document, style_id, props, color)
                added_style_maps.add(style_id)
                continue

            style = _sub(document, "Style")
            style.set("id", style_id)

            if props["is_polygon"]:
                # Zone — polygon fill, no border
                poly_style = _sub(style, "PolyStyle")
                _sub(poly_style, "color", color)
                _sub(poly_style, "fill", "1")
                _sub(poly_style, "outline", "0")
                line_style = _sub(style, "LineStyle")
                _sub(line_style, "width", "0")
            else:
                _add_icon_style(style, props, color)


def _add_normalized_generation_styles(document: ET.Element) -> None:
    """Add generated-output advisory Direction Line styles."""
    direction_style = _sub(document, "Style")
    direction_style.set("id", "camera_direction_advisory")
    line_style = _sub(direction_style, "LineStyle")
    _sub(line_style, "color", GENERATED_DIRECTION_LINE_COLOR)
    _sub(line_style, "width", GENERATED_DIRECTION_LINE_WIDTH)


def _add_generated_camera_color_styles(
    document: ET.Element,
    output: NormalizedGenerationOutput,
) -> None:
    colors = _request_site_icon_colors(output)
    if len(colors) <= 1:
        return
    for color in colors:
        _add_mymaps_icon_style_map(
            document,
            _generated_camera_color_style_id(color),
            {"icon": GENERATED_CAMERA_ICON_HREF},
            color,
        )


def _format_kml_position(position: tuple[float, ...]) -> str:
    """Format a preserved KML position without inventing altitude."""
    return ",".join(str(coordinate) for coordinate in position)


def _request_site_icon_colors(output: NormalizedGenerationOutput) -> set[str]:
    return {
        site.icon_color
        for site in output.sites
        if _is_kml_abgr_color(site.icon_color)
    }


def _is_kml_abgr_color(value: str) -> bool:
    return len(value) == 8 and all(char in "0123456789abcdefABCDEF" for char in value)


def _generated_camera_color_style_id(color: str) -> str:
    return f"{GENERATED_CAMERA_COLOR_STYLE_PREFIX}-{color.lower()}"


def _camera_style_id(camera: GeneratedCameraLocation, multi_color_output: bool) -> str:
    if not multi_color_output:
        return GENERATED_CAMERA_MYMAPS_STYLE_ID
    color = _camera_site_color(camera)
    if color:
        return _generated_camera_color_style_id(color)
    return GENERATED_CAMERA_MYMAPS_STYLE_ID


def _camera_site_color(camera: GeneratedCameraLocation) -> str:
    for coverage in camera.coverages:
        if _is_kml_abgr_color(coverage.site_icon_color):
            return coverage.site_icon_color
    return ""


def _camera_display_name(camera: GeneratedCameraLocation, index: int) -> str:
    if not camera.coverages:
        return camera.name or f"Camera {index}"
    primary_coverage = min(
        camera.coverages,
        key=lambda coverage: (
            coverage.selection_rank,
            coverage.site_name,
            coverage.canonical_site_id,
        ),
    )
    return f"{primary_coverage.site_name} Cam {primary_coverage.selection_rank}"


def _camera_description(camera: GeneratedCameraLocation) -> str:
    lines = [
        "Generated Camera Location.",
        "Review location on site and adjust if needed.",
    ]
    if camera.azimuth is not None:
        lines.append(f"Suggested bearing: {camera.azimuth:.1f} degrees.")
    return "\n".join(lines)


def _normalise_direction_positions(
    positions: tuple[tuple[float, ...], ...],
) -> tuple[tuple[float, ...], ...]:
    if len(positions) != 3:
        return positions

    left, anchor, right = positions
    if len(left) < 2 or len(anchor) < 2 or len(right) < 2:
        return positions

    left_m = _haversine_m((anchor[1], anchor[0]), (left[1], left[0]))
    right_m = _haversine_m((anchor[1], anchor[0]), (right[1], right[0]))
    target_m = min(left_m, right_m)
    if target_m <= 0:
        return positions

    return (
        _scale_direction_point(left, anchor, target_m, left_m),
        anchor,
        _scale_direction_point(right, anchor, target_m, right_m),
    )


def _scale_direction_point(
    point: tuple[float, ...],
    anchor: tuple[float, ...],
    target_m: float,
    current_m: float,
) -> tuple[float, ...]:
    if current_m <= 0 or abs(current_m - target_m) < 0.001:
        return point
    ratio = target_m / current_m
    scaled = (
        anchor[0] + (point[0] - anchor[0]) * ratio,
        anchor[1] + (point[1] - anchor[1]) * ratio,
    )
    if len(point) > 2:
        return (*scaled, *point[2:])
    return scaled


def _add_icon_style(
    style: ET.Element,
    props: dict[str, str | bool],
    color: str,
    heading: float | None = None,
) -> None:
    """Add a complete IconStyle, including href, so importers do not fall back."""
    icon_style = _sub(style, "IconStyle")
    _sub(icon_style, "color", color)
    _sub(icon_style, "scale", "1.0")
    if heading is not None:
        _sub(icon_style, "heading", f"{heading:.1f}")
    icon = _sub(icon_style, "Icon")
    _sub(icon, "href", str(props["icon"]))
    _sub(icon_style, "hotSpot").attrib.update({
        "x": str(props.get("hotspot_x", "32")),
        "xunits": str(props.get("hotspot_xunits", "pixels")),
        "y": str(props.get("hotspot_y", "64")),
        "yunits": str(props.get("hotspot_yunits", "insetPixels")),
    })


def _add_mymaps_icon_style_map(
    document: ET.Element,
    style_id: str,
    props: dict[str, str | bool],
    color: str,
) -> None:
    """Add a My Maps-compatible normal/highlight icon StyleMap."""
    for state, label_scale in (("normal", "0"), ("highlight", "1")):
        style = _sub(document, "Style")
        style.set("id", f"{style_id}-{state}")
        _add_icon_style(style, props, color)
        label_style = _sub(style, "LabelStyle")
        _sub(label_style, "scale", label_scale)
        balloon_style = _sub(style, "BalloonStyle")
        _sub(balloon_style, "text", "<h3>$[name]</h3>")

    style_map = _sub(document, "StyleMap")
    style_map.set("id", style_id)
    for state in ("normal", "highlight"):
        pair = _sub(style_map, "Pair")
        _sub(pair, "key", state)
        _sub(pair, "styleUrl", f"#{style_id}-{state}")


def _build_placemark(
    document: ET.Element,
    point: ClusteredPoint,
    zone: str,
) -> None:
    """Append a ``<Placemark>`` for a single :class:`ClusteredPoint`."""
    eq = point.equipment_type
    style_eq = (
        EquipmentType.CAMERA_OMNI
        if eq == EquipmentType.CAMERA_DIR and point.azimuth is None
        else eq
    )
    pm = _sub(document, "Placemark")

    # Name & description -------------------------------------------------------
    _sub(pm, "name", f"{eq.value} ({point.total_usage} uses)")
    desc_text = (
        f"Equipment: {eq.value}\n"
        f"Group: {point.layout_family.upper()}\n"
        f"Source group: {point.source_group or 'unknown'}\n"
        f"Total usage: {point.total_usage}\n"
        f"Time-decay score: {point.time_decay_score:.2f}\n"
        f"Records: {point.record_count}\n"
        f"Distance: {point.distance_m:.1f} m\n"
        f"Last seen: {point.latest_timestamp.isoformat()}"
    )
    if point.azimuth is not None:
        desc_text += f"\nAzimuth: {point.azimuth:.1f}°"

    desc_el = _sub(pm, "description")
    desc_el.text = f"<![CDATA[{desc_text}]]>"

    # MVP: location first. Do not emit heading/facing visualisation yet.
    _sub(pm, "styleUrl", f"#{_style_id(style_eq, zone)}")

    # Extended data ------------------------------------------------------------
    ext = _sub(pm, "ExtendedData")

    for key, value in (
        ("usage_count", str(point.total_usage)),
        ("time_decay_score", f"{point.time_decay_score:.6f}"),
        ("equipment_type", eq.value),
        ("layout_family", point.layout_family),
        ("source_group", point.source_group),
        ("azimuth", str(point.azimuth) if point.azimuth is not None else ""),
    ):
        data = _sub(ext, "Data")
        data.set("name", key)
        _sub(data, "value", value)

    # Point geometry (KML order: lng, lat, alt) --------------------------------
    pt = _sub(pm, "Point")
    _sub(pt, "coordinates", f"{point.lng},{point.lat},0")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_reference_kml(
    center_lat: float,
    center_lng: float,
    points: list[ClusteredPoint],
    fade_r: float | None = None,
    core_r: float | None = None,
) -> str:
    """Generate a KML document with dual-buffer opacity zones.

    Parameters
    ----------
    center_lat, center_lng:
        The survey centre used for distance-based opacity bucketing.
    points:
        Clustered reference points from the database.

    Returns
    -------
    str
        Complete KML document as an XML string with declaration.
    """
    kml = ET.Element("kml")
    kml.set("xmlns", KML_NS)
    doc = _sub(kml, "Document")
    _sub(doc, "name", "Survey Reference Points")
    _sub(
        doc,
        "description",
        f"Reference points near ({center_lat:.6f}, {center_lng:.6f})",
    )

    # Shared styles
    _add_shared_styles(doc)

    folder = _sub(doc, "Folder")
    _sub(folder, "name", "IC reference cameras")

    # Placemarks ---------------------------------------------------------------
    core_r_val = core_r if core_r is not None else settings.CORE_RADIUS_M
    fade_r_val = fade_r if fade_r is not None else settings.FADE_RADIUS_M

    for pt in points:
        if pt.distance_m <= core_r_val:
            zone = "core"
        elif pt.distance_m <= fade_r_val:
            zone = "fade"
        else:
            # Beyond the fade boundary → skip entirely
            continue
        _build_placemark(folder, pt, zone)

    # Serialise ----------------------------------------------------------------
    tree = ET.ElementTree(kml)
    ET.indent(tree, space="  ")

    # ET.tostring can add the declaration via xml_declaration / default_namespace
    xml_bytes: bytes = ET.tostring(
        kml,
        encoding="unicode",  # type: ignore[arg-type]
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_bytes}'


def generate_pinned_map_reference_kml(
    sites: list[tuple[PinLocation, list[ClusteredPoint]]],
    fade_r: float | None = None,
    core_r: float | None = None,
) -> str:
    """Generate camera reference KML grouped by uploaded user site pins."""
    kml = ET.Element("kml")
    kml.set("xmlns", KML_NS)
    doc = _sub(kml, "Document")
    _sub(doc, "name", "Survey Reference Points")
    _sub(doc, "description", "Reference camera points generated from uploaded site pins")

    viewpoint = _site_viewpoint([pin for pin, _points in sites])
    if viewpoint:
        center_lat, center_lng, range_m = viewpoint
        _add_look_at(doc, center_lat, center_lng, range_m)

    _add_shared_styles(doc)

    core_r_val = core_r if core_r is not None else settings.CORE_RADIUS_M
    fade_r_val = fade_r if fade_r is not None else settings.FADE_RADIUS_M

    for idx, (pin, points) in enumerate(sites, start=1):
        folder = _sub(doc, "Folder")
        site_name = pin.name or f"Site {idx}"
        _sub(folder, "name", f"{site_name} reference cameras")
        _sub(
            folder,
            "description",
            f"Reference points near ({pin.lat:.6f}, {pin.lng:.6f})",
        )

        for pt in points:
            if pt.distance_m <= core_r_val:
                zone = "core"
            else:
                zone = "fade"
            _build_placemark(folder, pt, zone)

    tree = ET.ElementTree(kml)
    ET.indent(tree, space="  ")
    xml_bytes: bytes = ET.tostring(
        kml,
        encoding="unicode",  # type: ignore[arg-type]
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_bytes}'


def generate_normalized_request_kml(output: NormalizedGenerationOutput) -> str:
    """Generate globally de-duplicated Camera output from normalized evidence."""
    kml = ET.Element("kml")
    kml.set("xmlns", KML_NS)
    doc = _sub(kml, "Document")
    _sub(doc, "name", "Survey Camera Plan")
    _sub(
        doc,
        "description",
        "Camera locations generated from Canonical Site and historical evidence.",
    )
    pins = [
        PinLocation(lat=site.lat, lng=site.lng, name=site.name)
        for site in output.sites
    ]
    viewpoint = _site_viewpoint(pins)
    if viewpoint:
        _add_look_at(doc, *viewpoint)
    _add_shared_styles(doc)
    _add_normalized_generation_styles(doc)
    _add_generated_camera_color_styles(doc, output)
    multi_color_output = len(_request_site_icon_colors(output)) > 1

    cameras_folder = _sub(doc, "Folder")
    _sub(cameras_folder, "name", "Camera Locations")
    for index, camera in enumerate(output.cameras, start=1):
        placemark = _sub(cameras_folder, "Placemark")
        _sub(placemark, "name", _camera_display_name(camera, index))
        _sub(placemark, "description", _camera_description(camera))
        _sub(placemark, "styleUrl", f"#{_camera_style_id(camera, multi_color_output)}")
        extended = _sub(placemark, "ExtendedData")
        for key, value in (
            ("camera_location_cluster_id", camera.camera_location_cluster_id),
            (
                "historical_observation_count",
                str(camera.historical_observation_count),
            ),
            ("covered_site_count", str(len(camera.coverages))),
            (
                "ranking_scores",
                ",".join(
                    f"{coverage.canonical_site_id}:{coverage.ranking_score:.6f}"
                    for coverage in camera.coverages
                ),
            ),
            (
                "ranking_methods",
                ",".join(
                    dict.fromkeys(
                        coverage.ranking_method for coverage in camera.coverages
                    )
                ),
            ),
            (
                "ranking_reasons",
                " | ".join(
                    f"{coverage.canonical_site_id}: "
                    + " ".join(coverage.ranking_reasons)
                    for coverage in camera.coverages
                    if coverage.ranking_reasons
                ),
            ),
            (
                "selection_tiers",
                ",".join(
                    dict.fromkeys(
                        coverage.selection_tier for coverage in camera.coverages
                    )
                ),
            ),
            (
                "layout_roles",
                ",".join(
                    dict.fromkeys(
                        coverage.layout_role for coverage in camera.coverages
                    )
                ),
            ),
            ("azimuth", str(camera.azimuth) if camera.azimuth is not None else ""),
        ):
            data = _sub(extended, "Data")
            data.set("name", key)
            _sub(data, "value", value)
        point = _sub(placemark, "Point")
        _sub(point, "coordinates", f"{camera.lng},{camera.lat},0")

    direction_folder = _sub(doc, "Folder")
    _sub(direction_folder, "name", "Direction Lines")
    for index, camera in enumerate(output.cameras, start=1):
        if len(camera.direction_positions) < 2:
            continue
        placemark = _sub(direction_folder, "Placemark")
        camera_name = _camera_display_name(camera, index)
        _sub(placemark, "name", f"{camera_name} Direction")
        description_lines = [
            "Advisory historical direction. Field installation may adjust orientation."
        ]
        if camera.azimuth is not None:
            description_lines.append(
                f"Suggested centre bearing: {camera.azimuth:.1f} degrees"
            )
        if camera.suggested_coverage_angle is not None:
            description_lines.append(
                "Suggested coverage angle: "
                f"{camera.suggested_coverage_angle:.1f} degrees"
            )
        _sub(placemark, "description", "\n".join(description_lines))
        _sub(placemark, "styleUrl", "#camera_direction_advisory")
        extended = _sub(placemark, "ExtendedData")
        for key, value in (
            ("camera_location_cluster_id", camera.camera_location_cluster_id),
            (
                "suggested_centre_bearing",
                str(camera.azimuth) if camera.azimuth is not None else "",
            ),
            (
                "suggested_coverage_angle",
                str(camera.suggested_coverage_angle)
                if camera.suggested_coverage_angle is not None
                else "",
            ),
        ):
            data = _sub(extended, "Data")
            data.set("name", key)
            _sub(data, "value", value)
        line = _sub(placemark, "LineString")
        _sub(line, "tessellate", "1")
        _sub(
            line,
            "coordinates",
            " ".join(
                _format_kml_position(position)
                for position in _normalise_direction_positions(
                    camera.direction_positions
                )
            ),
        )

    if output.unmatched_request_items:
        unmatched_folder = _sub(doc, "Folder")
        _sub(unmatched_folder, "name", "Unmatched Request Items")
        _sub(
            unmatched_folder,
            "description",
            "No Canonical Site match: " + ", ".join(output.unmatched_request_items),
        )

    ET.indent(ET.ElementTree(kml), space="  ")
    xml_text = ET.tostring(kml, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_text}'
