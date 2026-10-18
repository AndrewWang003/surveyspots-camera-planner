"""KML file parser for extracting survey points from uploaded KML data."""

import io
import logging
import math
import zipfile
from typing import Optional

from lxml import etree
from shapely.geometry import Polygon as ShapelyPolygon

from .business_classifier import extract_required_camera_count, is_explicit_camera_name
from .models import EquipmentType, PinLocation, SurveyPoint

logger = logging.getLogger(__name__)

# KML namespace
KML_NS = "{http://www.opengis.net/kml/2.2}"

# Keywords for inferring equipment type from placemark names/descriptions
EQUIPMENT_KEYWORDS: dict[str, EquipmentType] = {
    "camera_dir": EquipmentType.CAMERA_DIR,
    "camera_omni": EquipmentType.CAMERA_OMNI,
    "directional": EquipmentType.CAMERA_DIR,
    "cam": EquipmentType.CAMERA_DIR,
    "omni": EquipmentType.CAMERA_OMNI,
    "zone": EquipmentType.ZONE,
    "polygon": EquipmentType.ZONE,
    "node": EquipmentType.NODE,
    "camera": EquipmentType.CAMERA_DIR,  # default camera type if ambiguous
}

FAMILY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("queue", "ic"),
    ("ql", "ic"),
    ("od", "od"),
    ("origin", "od"),
    ("destination", "od"),
    ("bicycle", "ic"),
    ("bike", "ic"),
    ("pedestrian", "ic"),
    ("ped", "ic"),
    ("ic", "ic"),
    ("intersection", "ic"),
    ("hik", "hik"),
    ("hikvision", "hik"),
    ("cctv", "hik"),
    ("site", "site"),
)

CAMERA_LINE_MATCH_RADIUS_M = 4.0
SITE_CAMERA_ASSIGNMENT_MAX_RADIUS_M = 100.0
SITE_LAYOUT_FAMILIES = {"ic", "od"}
CAMERA_EQUIPMENT_TYPES = {
    EquipmentType.CAMERA_DIR,
    EquipmentType.CAMERA_OMNI,
}


def extract_kml_text(file_content: bytes, source_filename: str) -> str:
    """Return decoded KML text from a KML or KMZ upload."""
    if source_filename.lower().endswith(".kmz"):
        with zipfile.ZipFile(io.BytesIO(file_content), "r") as archive:
            kml_names = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".kml")
            ]
            if not kml_names:
                msg = f"No KML file found inside KMZ upload {source_filename!r}."
                raise ValueError(msg)
            with archive.open(kml_names[0], "r") as kml_file:
                return kml_file.read().decode("utf-8", errors="replace")

    return file_content.decode("utf-8", errors="replace")


def _parse_coordinates(coord_text: str) -> Optional[tuple[float, float, float]]:
    """Parse KML coordinate string (lng,lat,alt) into (lat, lng, alt) tuple.

    KML coordinates are in longitude,latitude,altitude order.
    Returns (lat, lng, alt) or None if parsing fails.
    """
    try:
        parts = coord_text.strip().split(",")
        if len(parts) < 2:
            return None
        lng = float(parts[0].strip())
        lat = float(parts[1].strip())
        alt = float(parts[2].strip()) if len(parts) > 2 else 0.0

        # Validate coordinate ranges
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            logger.warning("Coordinates out of range: lat=%s, lng=%s", lat, lng)
            return None

        return (lat, lng, alt)
    except (ValueError, IndexError) as exc:
        logger.warning("Failed to parse coordinates '%s': %s", coord_text, exc)
        return None


def _parse_coordinate_pairs(coord_text: str) -> list[tuple[float, float]]:
    """Parse KML coordinate text into ``[(lat, lng), ...]`` pairs."""
    pairs: list[tuple[float, float]] = []
    for raw_pair in coord_text.strip().split():
        parsed = _parse_coordinates(raw_pair)
        if parsed:
            lat, lng, _ = parsed
            pairs.append((lat, lng))
    return pairs


def _extract_extended_data(placemark: etree._Element) -> dict[str, str]:
    """Extract ExtendedData key-value pairs from a Placemark element."""
    data: dict[str, str] = {}
    extended = placemark.find(f"{KML_NS}ExtendedData")
    if extended is None:
        return data

    # Handle <Data name="key"><value>val</value></Data> format
    for data_elem in extended.findall(f"{KML_NS}Data"):
        name = data_elem.get("name", "")
        value_elem = data_elem.find(f"{KML_NS}value")
        if name and value_elem is not None and value_elem.text:
            data[name.lower()] = value_elem.text.strip()

    # Handle <SimpleData name="key">val</SimpleData> inside <SchemaData>
    for schema_data in extended.findall(f"{KML_NS}SchemaData"):
        for simple in schema_data.findall(f"{KML_NS}SimpleData"):
            name = simple.get("name", "")
            if name and simple.text:
                data[name.lower()] = simple.text.strip()

    return data


def _infer_equipment_type(
    name: str, description: str, extended_data: dict[str, str]
) -> EquipmentType:
    """Infer equipment type from available metadata.

    Priority:
    1. ExtendedData 'equipment_type' field
    2. Keyword matching in name
    3. Keyword matching in description
    4. Default to NODE
    """
    # Check ExtendedData first
    ext_type = extended_data.get("equipment_type", "").lower().strip()
    if ext_type:
        try:
            return EquipmentType(ext_type)
        except ValueError:
            pass

    name_text = _normalise_text(name)
    name_tokens = set(name_text.split())
    if name_tokens:
        first_token = next(iter(name_text.split()), "")
        if (
            first_token in {"site", "ic", "station", "ql", "ped"}
            and "cam" not in name_tokens
            and "camera" not in name_tokens
            and "cameras" not in name_tokens
        ):
            return EquipmentType.NODE

    # A description such as "2 cameras" is an installation requirement for a
    # Site, not evidence that the Point itself is a Camera location.
    if (
        extract_required_camera_count(description) is not None
        and not is_explicit_camera_name(name)
    ):
        return EquipmentType.NODE

    # Check name and description for keywords
    search_text = _normalise_text(f"{name} {description}")
    tokens = set(search_text.split())
    for keyword, eq_type in EQUIPMENT_KEYWORDS.items():
        if len(keyword) <= 3:
            if keyword in tokens:
                return eq_type
            continue
        if keyword in search_text:
            return eq_type

    return EquipmentType.NODE


def _extract_azimuth(
    placemark: etree._Element, extended_data: dict[str, str]
) -> Optional[float]:
    """Extract azimuth from ExtendedData or IconStyle heading.

    Priority:
    1. ExtendedData 'azimuth' or 'heading' field
    2. IconStyle <heading> element
    3. None
    """
    # Check ExtendedData
    for key in ("azimuth", "heading", "direction", "bearing"):
        if key in extended_data:
            try:
                val = float(extended_data[key])
                if 0 <= val < 360:
                    return val
            except ValueError:
                pass

    # Check IconStyle heading
    style = placemark.find(f".//{KML_NS}IconStyle/{KML_NS}heading")
    if style is not None and style.text:
        try:
            val = float(style.text.strip())
            if 0 <= val < 360:
                return val
        except ValueError:
            pass

    return None


def _extract_text(element: Optional[etree._Element]) -> str:
    """Safely extract text content from an XML element."""
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def _normalise_text(text: str) -> str:
    return " ".join(text.lower().replace("\xa0", " ").replace("-", " ").split())


def _infer_layout_family(*texts: str) -> str:
    search_text = _normalise_text(" ".join(texts))
    tokens = set(search_text.split())
    for keyword, family in FAMILY_KEYWORDS:
        if len(keyword) <= 3:
            if keyword in tokens:
                return family
            continue
        if keyword in search_text:
            return family
    return "unknown"


def _ancestor_folder_names(placemark: etree._Element) -> list[str]:
    names: list[str] = []
    parent = placemark.getparent()
    while parent is not None:
        if etree.QName(parent).localname == "Folder":
            name = _extract_text(parent.find(f"{KML_NS}name"))
            if not name:
                name = _extract_text(parent.find("name"))
            if name:
                names.append(name)
        parent = parent.getparent()
    return list(reversed(names))


def _haversine_m(start: tuple[float, float], end: tuple[float, float]) -> float:
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


def _bearing_deg(start: tuple[float, float], end: tuple[float, float]) -> float:
    lat1, lng1 = map(math.radians, start)
    lat2, lng2 = map(math.radians, end)
    dlng = lng2 - lng1
    y = math.sin(dlng) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _line_heading_from_camera(
    camera_point: tuple[float, float],
    line_points: list[tuple[float, float]],
) -> tuple[float | None, float]:
    """Return the likely facing bearing and distance for a nearby folded camera line."""
    if len(line_points) < 2:
        return None, math.inf

    nearest_index = min(
        range(len(line_points)),
        key=lambda idx: _haversine_m(camera_point, line_points[idx]),
    )
    nearest_distance = _haversine_m(camera_point, line_points[nearest_index])
    if nearest_distance > CAMERA_LINE_MATCH_RADIUS_M:
        return None, nearest_distance

    candidates: list[tuple[float, float]] = []
    if nearest_index > 0:
        prev_point = line_points[nearest_index - 1]
        candidates.append((_haversine_m(camera_point, prev_point), _bearing_deg(camera_point, prev_point)))
    if nearest_index < len(line_points) - 1:
        next_point = line_points[nearest_index + 1]
        candidates.append((_haversine_m(camera_point, next_point), _bearing_deg(camera_point, next_point)))
    if not candidates:
        return None, nearest_distance

    candidates.sort(reverse=True)
    return candidates[0][1], nearest_distance


def _match_line_azimuth(
    camera_point: tuple[float, float],
    line_features: list[dict[str, object]],
    layout_family: str,
    source_group: str,
) -> float | None:
    best: tuple[float, float] | None = None
    for line in line_features:
        if line["layout_family"] != layout_family or line["source_group"] != source_group:
            continue
        heading, distance = _line_heading_from_camera(camera_point, line["points"])  # type: ignore[arg-type]
        if heading is None:
            continue
        if best is None or distance < best[0]:
            best = (distance, heading)
    return best[1] if best else None


def _is_named_camera_line(name: str, description: str, extended_data: dict[str, str]) -> bool:
    """Return True when a LineString placemark represents an individual camera."""
    return _infer_equipment_type(name, description, extended_data) in {
        EquipmentType.CAMERA_DIR,
        EquipmentType.CAMERA_OMNI,
    }


def _has_nearby_named_camera_lines(
    camera_point: tuple[float, float],
    line_features: list[dict[str, object]],
    layout_family: str,
    source_group: str,
) -> bool:
    """Detect individual camera lines anchored to an aggregate camera point."""
    for line in line_features:
        if not line.get("is_named_camera"):
            continue
        if line["layout_family"] != layout_family or line["source_group"] != source_group:
            continue
        heading, _distance = _line_heading_from_camera(camera_point, line["points"])  # type: ignore[arg-type]
        if heading is not None:
            return True
    return False


def _nearest_site_anchor(
    camera: SurveyPoint,
    site_anchors: list[SurveyPoint],
) -> tuple[SurveyPoint | None, float]:
    """Return the nearest IC/OD site anchor for a camera."""
    best_site: SurveyPoint | None = None
    best_distance = math.inf
    for site in site_anchors:
        distance = _haversine_m((camera.lat, camera.lng), (site.lat, site.lng))
        if distance < best_distance:
            best_distance = distance
            best_site = site
    return best_site, best_distance


def _link_cameras_to_nearest_site(
    points: list[SurveyPoint],
    source_filename: str,
) -> list[SurveyPoint]:
    """Use file-local IC/OD site marks to classify and link camera layers.

    Historical maps often keep the camera points in a generic layer such as
    ``Cam locations``. In those cases the camera icon tells us what it is, but
    the nearest site marker tells us which site it belongs to.
    """
    site_anchors = [
        point
        for point in points
        if point.layout_family in SITE_LAYOUT_FAMILIES
        and point.equipment_type == EquipmentType.NODE
    ]
    if not site_anchors:
        return points

    linked = 0
    output: list[SurveyPoint] = []
    for point in points:
        if point.equipment_type in CAMERA_EQUIPMENT_TYPES:
            compatible_sites = [
                site
                for site in site_anchors
                if point.layout_family not in SITE_LAYOUT_FAMILIES
                or site.layout_family == point.layout_family
            ]
            site, distance = _nearest_site_anchor(point, compatible_sites or site_anchors)
            if site and distance <= SITE_CAMERA_ASSIGNMENT_MAX_RADIUS_M:
                update = {
                    "site_name": site.name,
                    "site_source_group": site.source_group,
                    "site_lat": site.lat,
                    "site_lng": site.lng,
                    "site_distance_m": distance,
                }
                if point.layout_family not in SITE_LAYOUT_FAMILIES:
                    update["layout_family"] = site.layout_family
                point = point.model_copy(update=update)
                linked += 1
        output.append(point)

    if linked:
        logger.info(
            "Linked %d camera(s) in '%s' to nearest IC/OD site.",
            linked,
            source_filename,
        )
    return output


def _polygon_centroid(coord_text: str) -> Optional[tuple[float, float]]:
    """Calculate the centroid of a polygon defined by KML coordinate string.

    Returns (lat, lng) or None if calculation fails.
    """
    try:
        coords = []
        for pair in coord_text.strip().split():
            parts = pair.split(",")
            if len(parts) >= 2:
                lng = float(parts[0])
                lat = float(parts[1])
                coords.append((lng, lat))

        if len(coords) < 3:
            return None

        polygon = ShapelyPolygon(coords)
        centroid = polygon.centroid
        # Return as (lat, lng) — shapely uses (x, y) = (lng, lat)
        return (centroid.y, centroid.x)
    except Exception as exc:
        logger.warning("Failed to compute polygon centroid: %s", exc)
        return None


def parse_kml_file(kml_content: str, source_filename: str) -> list[SurveyPoint]:
    """Parse a KML file and extract survey points.

    Handles both Point and Polygon geometries. Polygons are reduced to
    their centroid and assigned equipment_type='zone'.

    Args:
        kml_content: Raw KML XML string.
        source_filename: Name of the uploaded file (for logging).

    Returns:
        List of SurveyPoint models extracted from the KML.
    """
    points: list[SurveyPoint] = []

    try:
        # Parse XML — handle potential encoding declaration
        kml_bytes = kml_content.encode("utf-8") if isinstance(kml_content, str) else kml_content
        root = etree.fromstring(kml_bytes)
    except etree.XMLSyntaxError as exc:
        logger.error("Failed to parse KML file '%s': %s", source_filename, exc)
        return points

    # Find all Placemark elements (handle namespace)
    placemarks = root.findall(f".//{KML_NS}Placemark")
    if not placemarks:
        # Try without namespace (some KML files don't use namespace prefix)
        placemarks = root.findall(".//Placemark")

    logger.info(
        "Parsing KML file '%s': found %d placemarks", source_filename, len(placemarks)
    )

    line_features: list[dict[str, object]] = []
    for placemark in placemarks:
        folder_names = _ancestor_folder_names(placemark)
        source_group = " / ".join(folder_names)
        name = _extract_text(placemark.find(f"{KML_NS}name")) or _extract_text(placemark.find("name"))
        description = _extract_text(placemark.find(f"{KML_NS}description")) or _extract_text(placemark.find("description"))
        extended_data = _extract_extended_data(placemark)
        layout_family = _infer_layout_family(source_group, name, description)
        line_elem = placemark.find(f".//{KML_NS}LineString/{KML_NS}coordinates")
        if line_elem is None:
            line_elem = placemark.find(".//LineString/coordinates")
        if line_elem is not None and line_elem.text:
            line_points = _parse_coordinate_pairs(line_elem.text)
            if len(line_points) >= 2:
                line_features.append(
                    {
                        "layout_family": layout_family,
                        "source_group": source_group,
                        "name": name,
                        "description": description,
                        "extended_data": extended_data,
                        "is_named_camera": _is_named_camera_line(
                            name,
                            description,
                            extended_data,
                        ),
                        "points": line_points,
                    }
                )

    for idx, placemark in enumerate(placemarks):
        try:
            folder_names = _ancestor_folder_names(placemark)
            source_group = " / ".join(folder_names)
            name = _extract_text(placemark.find(f"{KML_NS}name"))
            if not name:
                name = _extract_text(placemark.find("name"))

            description = _extract_text(placemark.find(f"{KML_NS}description"))
            if not description:
                description = _extract_text(placemark.find("description"))

            extended_data = _extract_extended_data(placemark)
            layout_family = _infer_layout_family(source_group, name, description)

            # Try to extract Point geometry
            point_elem = placemark.find(f".//{KML_NS}Point/{KML_NS}coordinates")
            if point_elem is None:
                point_elem = placemark.find(".//Point/coordinates")

            if point_elem is not None and point_elem.text:
                parsed = _parse_coordinates(point_elem.text)
                if parsed:
                    lat, lng, _ = parsed
                    equipment_type = _infer_equipment_type(
                        name, description, extended_data
                    )
                    azimuth = _extract_azimuth(placemark, extended_data)
                    if (
                        equipment_type
                        in {EquipmentType.CAMERA_DIR, EquipmentType.CAMERA_OMNI}
                        and _has_nearby_named_camera_lines(
                            (lat, lng),
                            line_features,
                            layout_family,
                            source_group,
                        )
                    ):
                        logger.debug(
                            "Skipping aggregate camera point '%s'; nearby named "
                            "camera LineStrings provide the individual cameras.",
                            name,
                        )
                        continue
                    if azimuth is None and equipment_type in {
                        EquipmentType.CAMERA_DIR,
                        EquipmentType.CAMERA_OMNI,
                    }:
                        azimuth = _match_line_azimuth(
                            (lat, lng),
                            line_features,
                            layout_family,
                            source_group,
                        )

                    required_camera_count = None
                    camera_count_source = ""
                    if equipment_type == EquipmentType.NODE:
                        required_camera_count = extract_required_camera_count(description)
                        if required_camera_count is not None:
                            camera_count_source = "explicit_description"

                    points.append(
                        SurveyPoint(
                            lat=lat,
                            lng=lng,
                            azimuth=azimuth,
                            equipment_type=equipment_type,
                            layout_family=layout_family,
                            source_group=source_group,
                            name=name or f"Point {idx + 1}",
                            description=description,
                            required_camera_count=required_camera_count,
                            camera_count_source=camera_count_source,
                        )
                    )
                continue

            # Try to extract Polygon geometry → centroid
            polygon_coords = placemark.find(
                f".//{KML_NS}Polygon//{KML_NS}coordinates"
            )
            if polygon_coords is None:
                polygon_coords = placemark.find(".//Polygon//coordinates")

            if polygon_coords is not None and polygon_coords.text:
                centroid = _polygon_centroid(polygon_coords.text)
                if centroid:
                    lat, lng = centroid
                    azimuth = _extract_azimuth(placemark, extended_data)

                    points.append(
                        SurveyPoint(
                            lat=lat,
                            lng=lng,
                            azimuth=azimuth,
                            equipment_type=EquipmentType.ZONE,
                            layout_family=layout_family,
                            source_group=source_group,
                            name=name or f"Zone {idx + 1}",
                            description=description,
                        )
                    )
                continue

            # Some source KMLs encode a camera as a folded LineString only:
            # arm -> camera vertex -> arm. Use the middle vertex as the camera.
            line_coords = placemark.find(f".//{KML_NS}LineString/{KML_NS}coordinates")
            if line_coords is None:
                line_coords = placemark.find(".//LineString/coordinates")

            if line_coords is not None and line_coords.text:
                line_points = _parse_coordinate_pairs(line_coords.text)
                equipment_type = _infer_equipment_type(name, description, extended_data)
                if len(line_points) >= 2 and equipment_type == EquipmentType.CAMERA_DIR:
                    camera_point = line_points[len(line_points) // 2]
                    azimuth = _extract_azimuth(placemark, extended_data)
                    if azimuth is None:
                        azimuth, _ = _line_heading_from_camera(camera_point, line_points)
                    points.append(
                        SurveyPoint(
                            lat=camera_point[0],
                            lng=camera_point[1],
                            azimuth=azimuth,
                            equipment_type=equipment_type,
                            layout_family=layout_family,
                            source_group=source_group,
                            name=name or f"Camera {idx + 1}",
                            description=description,
                        )
                    )
                continue

            logger.debug(
                "Placemark %d ('%s') has no Point or Polygon geometry, skipping",
                idx,
                name,
            )

        except Exception as exc:
            logger.warning(
                "Error processing placemark %d in '%s': %s",
                idx,
                source_filename,
                exc,
            )
            continue

    logger.info(
        "Extracted %d survey points from '%s'", len(points), source_filename
    )
    return _link_cameras_to_nearest_site(points, source_filename)


def parse_pin_locations(kml_content: str, source_filename: str) -> list[PinLocation]:
    """Extract user site pins from a My Maps KML export.

    This intentionally reads Point placemarks only. It does not infer cameras or
    consume broken-line direction geometry; the uploaded file is just a carrier
    for the exact site coordinates the user marked.
    """
    pins: list[PinLocation] = []

    try:
        kml_bytes = kml_content.encode("utf-8") if isinstance(kml_content, str) else kml_content
        root = etree.fromstring(kml_bytes)
    except etree.XMLSyntaxError as exc:
        logger.error("Failed to parse pinned map KML '%s': %s", source_filename, exc)
        return pins

    placemarks = root.findall(f".//{KML_NS}Placemark")
    if not placemarks:
        placemarks = root.findall(".//Placemark")

    for idx, placemark in enumerate(placemarks):
        point_elem = placemark.find(f".//{KML_NS}Point/{KML_NS}coordinates")
        if point_elem is None:
            point_elem = placemark.find(".//Point/coordinates")
        if point_elem is None or not point_elem.text:
            continue

        parsed = _parse_coordinates(point_elem.text)
        if not parsed:
            continue

        lat, lng, _ = parsed
        name = _extract_text(placemark.find(f"{KML_NS}name")) or _extract_text(placemark.find("name"))
        description = (
            _extract_text(placemark.find(f"{KML_NS}description"))
            or _extract_text(placemark.find("description"))
        )
        pins.append(
            PinLocation(
                lat=lat,
                lng=lng,
                name=name or f"Pin {idx + 1}",
                description=description,
            )
        )

    logger.info("Extracted %d site pins from '%s'.", len(pins), source_filename)
    return pins
