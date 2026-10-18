"""Export relative camera-layout training rows from historical KML files."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile

from lxml import etree


KML_NS = "{http://www.opengis.net/kml/2.2}"
SITE_RE = re.compile(r"\b(?:site|ic)\s*\d+[a-z]?|^station\s*\d+[a-z]?", re.I)
CAMERA_RE = re.compile(
    r"\bcam(?:era)?\b|\b(?:wb|eb|nb|sb)\s*cam\b|\b(?:ip|ic)\s+camera\b",
    re.I,
)


@dataclass(frozen=True)
class PlacemarkFeature:
    index: int
    name: str
    folders: tuple[str, ...]
    style_url: str
    point: tuple[float, float] | None
    line: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class TrainingRow:
    source_file: str
    source_sha256: str
    layout_family: str
    site_name: str
    camera_name: str
    matched_line_name: str
    site_lat: float
    site_lng: float
    camera_lat: float
    camera_lng: float
    distance_m: float
    bearing_deg: float
    camera_heading_deg: float | None
    line_match_distance_m: float | None
    assignment_confidence: str


@dataclass(frozen=True)
class FileSummary:
    source_file: str
    status: str
    source_sha256: str
    placemark_count: int
    site_count: int
    camera_count: int
    line_count: int
    exported_rows: int
    notes: str = ""


def export_training_dataset(
    data_dir: Path,
    rows_output: Path,
    summary_output: Path,
    *,
    line_match_radius_m: float = 3.0,
) -> tuple[list[TrainingRow], list[FileSummary]]:
    """Export training rows and summaries for all KML/KMZ files in a directory."""
    rows: list[TrainingRow] = []
    summaries: list[FileSummary] = []
    seen_hashes: dict[str, str] = {}
    seen_feature_signatures: dict[str, str] = {}

    for path in sorted(data_dir.iterdir()):
        if path.suffix.lower() not in {".kml", ".kmz"}:
            continue

        source_hash = _file_sha256(path)
        if source_hash in seen_hashes:
            summaries.append(
                FileSummary(
                    source_file=path.name,
                    status="duplicate_skipped",
                    source_sha256=source_hash,
                    placemark_count=0,
                    site_count=0,
                    camera_count=0,
                    line_count=0,
                    exported_rows=0,
                    notes=f"Duplicate of {seen_hashes[source_hash]}",
                )
            )
            continue
        seen_hashes[source_hash] = path.name

        try:
            content = _read_kml_or_kmz(path)
        except ValueError as exc:
            summaries.append(
                FileSummary(
                    source_file=path.name,
                    status="skipped",
                    source_sha256=source_hash,
                    placemark_count=0,
                    site_count=0,
                    camera_count=0,
                    line_count=0,
                    exported_rows=0,
                    notes=str(exc),
                )
            )
            continue

        features = parse_kml_features(content)
        feature_signature = _feature_signature(features)
        if feature_signature in seen_feature_signatures:
            summaries.append(
                FileSummary(
                    source_file=path.name,
                    status="duplicate_skipped",
                    source_sha256=source_hash,
                    placemark_count=len(features),
                    site_count=0,
                    camera_count=0,
                    line_count=0,
                    exported_rows=0,
                    notes=f"Same normalized placemarks as {seen_feature_signatures[feature_signature]}",
                )
            )
            continue
        seen_feature_signatures[feature_signature] = path.name

        sites = [feature for feature in features if is_site_feature(feature)]
        cameras = [feature for feature in features if is_camera_feature(feature)]
        lines = [feature for feature in features if feature.line]
        file_rows = [
            row
            for row in build_training_rows(
                path.name,
                source_hash,
                sites,
                cameras,
                lines,
                line_match_radius_m=line_match_radius_m,
            )
        ]
        rows.extend(file_rows)
        summaries.append(
            FileSummary(
                source_file=path.name,
                status="exported",
                source_sha256=source_hash,
                placemark_count=len(features),
                site_count=len(sites),
                camera_count=len(cameras),
                line_count=len(lines),
                exported_rows=len(file_rows),
            )
        )

    _write_training_rows(rows_output, rows)
    _write_summary(summary_output, summaries)
    return rows, summaries


def parse_kml_features(kml_content: bytes) -> list[PlacemarkFeature]:
    """Parse point and line placemarks while preserving folder context."""
    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.fromstring(kml_content, parser=parser)
    features: list[PlacemarkFeature] = []

    for index, (placemark, folders) in enumerate(_walk_placemarks(root, ())):
        name = _child_text(placemark, "name")
        style_url = _child_text(placemark, "styleUrl")
        features.append(
            PlacemarkFeature(
                index=index,
                name=name,
                folders=folders,
                style_url=style_url,
                point=_extract_point(placemark),
                line=tuple(_extract_line(placemark)),
            )
        )

    return features


def build_training_rows(
    source_file: str,
    source_hash: str,
    sites: list[PlacemarkFeature],
    cameras: list[PlacemarkFeature],
    lines: list[PlacemarkFeature],
    *,
    line_match_radius_m: float,
) -> Iterable[TrainingRow]:
    """Build relative camera rows by assigning cameras to sites and facing lines."""
    for camera in cameras:
        if camera.point is None or not sites:
            continue

        site = min(
            (candidate for candidate in sites if candidate.point is not None),
            key=lambda candidate: haversine_m(candidate.point, camera.point),  # type: ignore[arg-type]
        )
        if site.point is None:
            continue

        matched_line, line_distance = _match_facing_line(
            camera,
            lines,
            line_match_radius_m=line_match_radius_m,
        )
        camera_heading = None
        matched_line_name = ""
        if matched_line is not None:
            matched_line_name = matched_line.name
            target = max(
                matched_line.line,
                key=lambda point: haversine_m(camera.point, point),  # type: ignore[arg-type]
            )
            camera_heading = bearing_deg(camera.point, target)

        distance = haversine_m(site.point, camera.point)
        yield TrainingRow(
            source_file=source_file,
            source_sha256=source_hash,
            layout_family=_layout_family(site, camera),
            site_name=site.name,
            camera_name=camera.name,
            matched_line_name=matched_line_name,
            site_lat=round(site.point[0], 7),
            site_lng=round(site.point[1], 7),
            camera_lat=round(camera.point[0], 7),
            camera_lng=round(camera.point[1], 7),
            distance_m=round(distance, 2),
            bearing_deg=round(bearing_deg(site.point, camera.point), 2),
            camera_heading_deg=round(camera_heading, 2)
            if camera_heading is not None
            else None,
            line_match_distance_m=round(line_distance, 2)
            if line_distance is not None
            else None,
            assignment_confidence=_assignment_confidence(distance),
        )


def is_site_feature(feature: PlacemarkFeature) -> bool:
    return feature.point is not None and SITE_RE.search(feature.name) is not None


def is_camera_feature(feature: PlacemarkFeature) -> bool:
    if feature.point is None or is_site_feature(feature):
        return False
    return CAMERA_RE.search(feature.name) is not None


def haversine_m(
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Return great-circle distance in meters for `(lat, lng)` pairs."""
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


def bearing_deg(
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Return bearing in degrees clockwise from north for `(lat, lng)` pairs."""
    lat1, lng1 = map(math.radians, start)
    lat2, lng2 = map(math.radians, end)
    dlng = lng2 - lng1
    y = math.sin(dlng) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        dlng
    )
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _match_facing_line(
    camera: PlacemarkFeature,
    lines: list[PlacemarkFeature],
    *,
    line_match_radius_m: float,
) -> tuple[PlacemarkFeature | None, float | None]:
    if camera.point is None:
        return None, None

    candidates: list[tuple[int, float, PlacemarkFeature]] = []
    for line in lines:
        distances = [haversine_m(camera.point, point) for point in line.line]
        if not distances:
            continue
        min_distance = min(distances)
        if min_distance <= line_match_radius_m:
            candidates.append((abs(line.index - camera.index), min_distance, line))

    if not candidates:
        return None, None

    _, distance, line = min(candidates, key=lambda item: (item[0], item[1]))
    return line, distance


def _assignment_confidence(distance_m: float) -> str:
    if distance_m <= 100:
        return "high"
    if distance_m <= 250:
        return "medium"
    return "low"


def _layout_family(site: PlacemarkFeature, camera: PlacemarkFeature) -> str:
    combined = f"{site.name} {' '.join(site.folders)} {camera.name} {' '.join(camera.folders)}"
    if re.search(r"\bstation\b|\bod\b|\b(?:wb|eb|nb|sb)\s*cam\b", combined, re.I):
        return "od"
    if re.search(r"queue|ql", combined, re.I):
        return "queue_length"
    return "intersection"


def _walk_placemarks(
    element: etree._Element,
    folders: tuple[str, ...],
) -> Iterable[tuple[etree._Element, tuple[str, ...]]]:
    local_name = etree.QName(element).localname
    if local_name == "Folder":
        folder_name = _child_text(element, "name")
        if folder_name:
            folders = (*folders, folder_name)
    if local_name == "Placemark":
        yield element, folders
    for child in element:
        yield from _walk_placemarks(child, folders)


def _extract_point(placemark: etree._Element) -> tuple[float, float] | None:
    coord = placemark.find(f".//{KML_NS}Point/{KML_NS}coordinates")
    if coord is None:
        coord = placemark.find(".//Point/coordinates")
    if coord is None or not coord.text:
        return None
    points = _parse_coordinate_list(coord.text)
    return points[0] if points else None


def _extract_line(placemark: etree._Element) -> list[tuple[float, float]]:
    coord = placemark.find(f".//{KML_NS}LineString/{KML_NS}coordinates")
    if coord is None:
        coord = placemark.find(".//LineString/coordinates")
    if coord is None or not coord.text:
        return []
    return _parse_coordinate_list(coord.text)


def _parse_coordinate_list(coord_text: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for pair in coord_text.strip().split():
        parts = pair.split(",")
        if len(parts) < 2:
            continue
        try:
            lng = float(parts[0].strip())
            lat = float(parts[1].strip())
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            points.append((lat, lng))
    return points


def _child_text(element: etree._Element, child_name: str) -> str:
    child = element.find(f"{KML_NS}{child_name}")
    if child is None:
        child = element.find(child_name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _read_kml_or_kmz(path: Path) -> bytes:
    if path.suffix.lower() == ".kmz":
        with ZipFile(path) as archive:
            kml_names = [name for name in archive.namelist() if name.lower().endswith(".kml")]
            if not kml_names:
                raise ValueError("KMZ contains no KML file")
            content = archive.read(kml_names[0])
            if b"<NetworkLink" in content and b"<Placemark" not in content:
                raise ValueError("KMZ contains only a NetworkLink; export full KML")
            return content
    return path.read_bytes()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _feature_signature(features: list[PlacemarkFeature]) -> str:
    normalized = [
        {
            "name": feature.name,
            "folders": feature.folders,
            "point": _round_point(feature.point),
            "line": [_round_point(point) for point in feature.line],
        }
        for feature in features
    ]
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _round_point(point: tuple[float, float] | None) -> tuple[float, float] | None:
    if point is None:
        return None
    return (round(point[0], 7), round(point[1], 7))


def _write_training_rows(path: Path, rows: list[TrainingRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(TrainingRow.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _write_summary(path: Path, summaries: list[FileSummary]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(summary) for summary in summaries]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
