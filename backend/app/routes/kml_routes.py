from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import Response
from html import escape
import io
import logging
from uuid import UUID
import zipfile

from ..auth import verify_google_token
from ..config import settings
from ..database import (
    fetch_historical_review_items,
    fetch_clustered_points,
    insert_survey_points,
    submit_historical_review_decisions,
    store_raw_upload,
    store_raw_upload_layer_reviews,
)
from ..business_classifier import classify_raw_placemarks
from ..historical_layer_review import layer_path_for_placemark
from ..ingestion_service import persist_normalized_ingestion_layers
from ..generation_request_parser import parse_generation_request_geometries
from ..historical_layer_review import LayerReviewSelection
from ..kml_generator import generate_normalized_request_kml, generate_reference_kml
from ..kml_parser import extract_kml_text, parse_kml_file
from ..models import (
    BatchUploadResponse,
    ClusteredPoint,
    ConfirmUploadRequest,
    ConfirmUploadResponse,
    FilePreview,
    LayerPreview,
    PreviewResponse,
    ReviewItem,
    ReviewItemsResponse,
    SubmitReviewRequest,
    SubmitReviewResponse,
    SurveyPoint,
    UploadResponse,
)
from ..preview_cache import PreviewFileData, get_preview, remove_preview, store_preview
from ..normalized_generation import generate_from_request_geometries
from ..raw_placemark_parser import RawPlacemarkRecord, extract_raw_placemarks

router = APIRouter(prefix="/api/v1", tags=["kml"])
logger = logging.getLogger(__name__)
MAX_PINNED_MAP_SITES = 50


def _generation_quality_counts(generated) -> dict[str, int]:
    """Summarize normalized generation quality for the Extension."""
    statuses = defaultdict(int)
    for site in generated.sites:
        statuses[site.generation_status] += 1

    supplemental_camera_count = sum(
        1
        for camera in generated.cameras
        if any(
            coverage.layout_role == "supplemental_historical"
            for coverage in camera.coverages
        )
    )
    return {
        "site_count": len(generated.sites),
        "complete_count": statuses["complete"],
        "partial_count": statuses["partial"],
        "requires_review_count": statuses["requires_review"],
        "camera_count": len(generated.cameras),
        "supplemental_camera_count": supplemental_camera_count,
        "unmatched_count": len(generated.unmatched_request_items),
    }


def _records_to_clustered_points(records) -> list[ClusteredPoint]:
    return [
        ClusteredPoint(
            lat=r['representative_lat'],
            lng=r['representative_lng'],
            azimuth=r['latest_azimuth'],
            equipment_type=r['equipment_type'],
            layout_family=r['layout_family'],
            source_group=r['source_group'],
            total_usage=r['total_usage'],
            time_decay_score=r['time_decay_score'],
            record_count=r['record_count'],
            distance_m=r['distance_m'],
            latest_timestamp=r['latest_timestamp']
        ) for r in records
    ]


def _build_reference_kmz(kml_content: str) -> bytes:
    """Package generated KML as KMZ for My Maps import."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", kml_content.encode("utf-8"))
    return buffer.getvalue()


def _review_response_from_data(data: dict) -> ReviewItemsResponse:
    items = [ReviewItem(**item) for item in data["items"]]
    return ReviewItemsResponse(
        raw_upload_id=data["raw_upload_id"],
        source_file=data["source_file"],
        review_count=len(items),
        items=items,
    )


def _generate_review_kml(review_data: dict) -> str:
    source_file = review_data["source_file"] or "historical_upload"
    items = review_data["items"]
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "<Document>",
        f"<name>{escape(source_file)} review items</name>",
        "<Style id=\"review-site\"><IconStyle><color>ffffaa00</color><scale>0.9</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/blu-circle.png</href></Icon></IconStyle></Style>",
        "<Folder><name>Review Camera Locations</name>",
    ]
    for item in items:
        if item["lat"] is None or item["lng"] is None:
            continue
        style_id = f"review-camera-{item['review_id']}"
        style = item.get("style_definition") or {}
        icon_href = style.get("icon_href") or "http://maps.google.com/mapfiles/kml/paddle/red-circle.png"
        icon_color = style.get("icon_color") or "ffffffff"
        parts.extend(
            [
                f"<Style id=\"{escape(style_id)}\">",
                "<IconStyle>",
                f"<color>{escape(icon_color)}</color>",
                "<scale>1.0</scale>",
                "<Icon>",
                f"<href>{escape(icon_href)}</href>",
                "</Icon>",
                "</IconStyle>",
                "</Style>",
            ]
        )
        label_source = item["camera_name"] or item["title"]
        name = f"REVIEW {item['review_id']} | {label_source}"
        description = "\n".join(
            [
                f"Review ID: {item['review_id']}",
                f"Type: {item['item_type']}",
                f"Reason: {item['reason']}",
                f"Suggested family: {item['current_site_task_family'] or item['camera_task_family']}",
                f"Suggested Site: {item['current_site_name']}",
            ]
        )
        parts.extend(
            [
                "<Placemark>",
                f"<name>{escape(name)}</name>",
                f"<description>{escape(description)}</description>",
                f"<styleUrl>#{escape(style_id)}</styleUrl>",
                "<Point>",
                f"<coordinates>{item['lng']},{item['lat']},0</coordinates>",
                "</Point>",
                "</Placemark>",
            ]
        )
    parts.append("</Folder><Folder><name>Candidate Sites</name>")
    seen_sites = set()
    for item in items:
        for site in item["candidate_sites"][:3]:
            site_id = site["site_observation_id"]
            if site_id in seen_sites:
                continue
            if site.get("lat") is None or site.get("lng") is None:
                continue
            seen_sites.add(site_id)
            label = (
                f"{site['task_family'].upper()} | {site['name']}"
                if site["task_family"]
                else site["name"]
            )
            parts.extend(
                [
                    "<Placemark>",
                    f"<name>{escape(label)}</name>",
                    "<description>Candidate Site listed in the Extension review panel.</description>",
                    "<styleUrl>#review-site</styleUrl>",
                    "<Point>",
                    f"<coordinates>{site['lng']},{site['lat']},0</coordinates>",
                    "</Point>",
                    "</Placemark>",
                ]
            )
    parts.extend(["</Folder>", "</Document>", "</kml>"])
    return "\n".join(parts)


def _build_file_preview(
    file_id: str,
    filename: str,
    points: list[SurveyPoint],
    placemarks: list[RawPlacemarkRecord],
) -> FilePreview:
    """Build complete Point and geometry counts for every detected Layer."""
    layers_map: dict[str, list[SurveyPoint]] = defaultdict(list)
    for p in points:
        key = p.source_group or "(root)"
        layers_map[key].append(p)

    placemarks_map: dict[str, list[RawPlacemarkRecord]] = defaultdict(list)
    for placemark in placemarks:
        placemarks_map[layer_path_for_placemark(placemark)].append(placemark)
    decisions_by_index = {
        decision.placemark_index: decision
        for decision in classify_raw_placemarks(placemarks, filename)
    }

    layers: list[LayerPreview] = []
    for layer_name in sorted(set(layers_map) | set(placemarks_map)):
        layer_points = layers_map[layer_name]
        layer_placemarks = placemarks_map[layer_name]
        family_counts: dict[str, int] = defaultdict(int)
        equip_set: set[str] = set()
        sample_names: list[str] = []
        for p in layer_points:
            family_counts[p.layout_family] += 1
            equip_set.add(p.equipment_type.value)
            if len(sample_names) < 3 and p.name:
                sample_names.append(p.name)

        layer_decisions = [
            decisions_by_index[placemark.placemark_index]
            for placemark in layer_placemarks
        ]
        if layer_decisions and all(
            decision.task_family == "atc" for decision in layer_decisions
        ):
            dominant_family = "atc"
        elif family_counts:
            dominant_family = max(family_counts, key=family_counts.get)
        else:
            if layer_decisions and all(
                decision.business_role == "site" for decision in layer_decisions
            ):
                dominant_family = "site"
            else:
                decision_families: dict[str, int] = defaultdict(int)
                for decision in layer_decisions:
                    decision_families[decision.task_family] += 1
                dominant_family = (
                    max(decision_families, key=decision_families.get)
                    if decision_families
                    else "unknown"
                )

        raw_sample_names = [
            placemark.placemark_name
            for placemark in layer_placemarks
            if placemark.placemark_name
        ][:3]
        geometry_types = sorted(
            {
                geometry_type
                for placemark in layer_placemarks
                for geometry_type in placemark.geometry_types
            }
        )
        business_role_counts: dict[str, int] = defaultdict(int)
        for placemark in layer_placemarks:
            decision = decisions_by_index[placemark.placemark_index]
            business_role_counts[decision.business_role] += 1

        layers.append(LayerPreview(
            layer_name=layer_name,
            inferred_family=dominant_family,
            point_count=len(layer_points),
            placemark_count=len(layer_placemarks),
            equipment_types=sorted(equip_set),
            geometry_types=geometry_types,
            business_role_counts=dict(sorted(business_role_counts.items())),
            sample_names=raw_sample_names or sample_names,
        ))

    return FilePreview(
        file_id=file_id,
        filename=filename,
        total_points=len(points),
        total_placemarks=len(placemarks),
        layers=layers,
    )


async def _process_uploaded_kml_file(
    file: UploadFile,
    user_email: str,
) -> UploadResponse:
    """Store one raw upload and refresh its current-version parsed IC rows."""
    if not file.filename or not file.filename.lower().endswith((".kml", ".kmz")):
        logger.error(f"400 Bad Request: File must be a .kml or .kmz file: {file.filename}")
        raise HTTPException(status_code=400, detail="File must be a .kml or .kmz file")

    content = await file.read()
    if not content:
        logger.error(f"400 Bad Request: Empty file uploaded: {file.filename}")
        raise HTTPException(status_code=400, detail=f"Empty file uploaded: {file.filename}")

    if content == b"[object Object]":
        raise HTTPException(status_code=400, detail="Chrome Extension bug detected (sent '[object Object]'). Please refresh the extension in chrome://extensions/ using the refresh icon, then try again!")


    try:
        kml_text = extract_kml_text(content, file.filename)
    except ValueError as exc:
        logger.error(f"400 Bad Request: KML extraction error: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not kml_text.lstrip().startswith("<"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Upload {file.filename!r} does not look like KML/KMZ content. "
                "Reload the extension and try again."
            ),
        )

    parsed_points = parse_kml_file(kml_text, file.filename)
    points = [
        point
        for point in parsed_points
        if point.layout_family == "ic"
    ]

    raw_upload_id = await store_raw_upload(
        file.filename,
        content,
        file.content_type,
        user_email,
    )
    await persist_normalized_ingestion_layers(
        raw_upload_id,
        kml_text,
        file.filename,
    )

    if not points:
        return UploadResponse(
            status="success",
            points_inserted=0,
            filename=file.filename,
            message="No valid IC points found in the KML file",
            raw_upload_id=str(raw_upload_id),
            parser_version=settings.HISTORY_PARSER_VERSION,
        )

    inserted = await insert_survey_points(points, file.filename, raw_upload_id)

    return UploadResponse(
        status="success",
        points_inserted=inserted,
        filename=file.filename,
        message=f"Successfully processed {inserted} survey points",
        raw_upload_id=str(raw_upload_id),
        parser_version=settings.HISTORY_PARSER_VERSION,
    )

@router.get("/reference-kml")
async def get_reference_kml(
    lat: float,
    lng: float,
    radius: float | None = None,
    user_info: dict = Depends(verify_google_token)
) -> Response:
    """Download a KML file containing clustered reference points for deployment planning."""
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        raise HTTPException(status_code=400, detail="Invalid coordinates")
        
    try:
        effective_radius = radius if radius is not None else settings.FADE_RADIUS_M
        # Fetch clustered historical points
        records = await fetch_clustered_points(lat, lng, radius_m=effective_radius)
        points = _records_to_clustered_points(records)
        
        # Generate KML content
        kml_content = generate_reference_kml(
            lat,
            lng,
            points,
            fade_r=effective_radius,
            core_r=min(settings.CORE_RADIUS_M, effective_radius),
        )
        
        return Response(
            content=kml_content,
            media_type="application/vnd.google-earth.kml+xml",
            headers={
                "Content-Disposition": f"attachment; filename=reference_{lat:.5f}_{lng:.5f}.kml"
            }
        )
    except Exception as e:
        logger.error(f"Error generating reference KML: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/pinned-map-reference-kml")
async def pinned_map_reference_kml(
    file: UploadFile = File(...),
    radius: float | None = None,
    user_info: dict = Depends(verify_google_token),
) -> Response:
    """Generate reference camera KML from user pins in an uploaded My Maps KML."""
    if not file.filename or not file.filename.lower().endswith((".kml", ".kmz")):
        raise HTTPException(status_code=400, detail="File must be a .kml or .kmz file")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail=f"Empty file uploaded: {file.filename}")

    try:
        kml_text = extract_kml_text(content, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pins, polygons = parse_generation_request_geometries(kml_text)
    if not pins and not polygons:
        raise HTTPException(
            status_code=400,
            detail="No Site Point or valid Polygon found in the uploaded map.",
        )
    if len(pins) > MAX_PINNED_MAP_SITES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Found {len(pins)} point pins; limit is {MAX_PINNED_MAP_SITES}. "
                "Upload a pinned site map, not a full survey camera map."
            ),
        )

    try:
        effective_radius = radius if radius is not None else settings.SITE_MATCH_RADIUS_M
        generated = await generate_from_request_geometries(
            pins,
            polygons,
            effective_radius,
            MAX_PINNED_MAP_SITES,
        )
        kml_content = generate_normalized_request_kml(generated)
        quality = _generation_quality_counts(generated)
        total_points = quality["camera_count"]

        safe_name = (file.filename.rsplit(".", 1)[0] or "pinned_map").replace('"', "")
        kmz_content = _build_reference_kmz(kml_content)
        return Response(
            content=kmz_content,
            media_type="application/vnd.google-earth.kmz",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{safe_name}_reference_{total_points}_cameras.kmz"'
                ),
                "X-SurveySpots-Pin-Count": str(len(pins)),
                "X-SurveySpots-Polygon-Count": str(len(polygons)),
                "X-SurveySpots-Canonical-Site-Count": str(quality["site_count"]),
                "X-SurveySpots-Complete-Count": str(quality["complete_count"]),
                "X-SurveySpots-Partial-Count": str(quality["partial_count"]),
                "X-SurveySpots-Requires-Review-Count": str(
                    quality["requires_review_count"]
                ),
                "X-SurveySpots-Camera-Count": str(total_points),
                "X-SurveySpots-Supplemental-Camera-Count": str(
                    quality["supplemental_camera_count"]
                ),
                "X-SurveySpots-Unmatched-Count": str(quality["unmatched_count"]),
            },
        )
    except Exception as e:
        logger.error(f"Error generating pinned map reference KML: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ---------- Two-Step Upload: Preview → Confirm ----------


@router.post("/preview-upload", response_model=PreviewResponse)
async def preview_upload(
    files: list[UploadFile] = File(...),
    user_info: dict = Depends(verify_google_token),
):
    """Parse uploaded KML/KMZ files and return a layer preview without committing."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    user_email = user_info.get("email", "")
    file_previews: list[FilePreview] = []
    cached_files: list[PreviewFileData] = []

    for file_index, file in enumerate(files, start=1):
        if not file.filename or not file.filename.lower().endswith((".kml", ".kmz")):
            raise HTTPException(
                status_code=400,
                detail=f"File must be .kml or .kmz: {file.filename}",
            )

        content = await file.read()
        if not content:
            raise HTTPException(
                status_code=400, detail=f"Empty file: {file.filename}"
            )

        if content == b"[object Object]":
            raise HTTPException(
                status_code=400,
                detail="Chrome Extension bug detected. Please refresh the extension.",
            )

        try:
            kml_text = extract_kml_text(content, file.filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not kml_text.lstrip().startswith("<"):
            raise HTTPException(
                status_code=400,
                detail=f"Upload {file.filename!r} does not look like KML/KMZ content.",
            )

        parsed_points = parse_kml_file(kml_text, file.filename)
        raw_placemarks = extract_raw_placemarks(kml_text)
        file_id = f"file-{file_index}"

        file_previews.append(
            _build_file_preview(
                file_id,
                file.filename,
                parsed_points,
                raw_placemarks,
            )
        )
        cached_files.append(PreviewFileData(
            filename=file.filename,
            content=content,
            content_type=(
                file.content_type or "application/vnd.google-earth.kml+xml"
            ),
            kml_text=kml_text,
            points=parsed_points,
            file_id=file_id,
            layer_names=tuple(
                sorted(
                    {
                        layer_path_for_placemark(placemark)
                        for placemark in raw_placemarks
                    }
                )
            ),
        ))

    preview_id = store_preview(cached_files, user_email)

    total_points = sum(fp.total_points for fp in file_previews)
    total_placemarks = sum(fp.total_placemarks for fp in file_previews)
    return PreviewResponse(
        preview_id=preview_id,
        files=file_previews,
        message=(
            f"Parsed {len(file_previews)} file(s) with {total_placemarks} "
            f"Placemarks and {total_points} Point records. Review Layers below."
        ),
    )


@router.post("/confirm-upload", response_model=ConfirmUploadResponse)
async def confirm_upload(
    body: ConfirmUploadRequest,
    user_info: dict = Depends(verify_google_token),
):
    """Commit selected layers from a previously previewed upload."""
    entry = get_preview(body.preview_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail="Preview not found or expired. Please upload the files again.",
        )

    selection_maps = _resolve_preview_selections(entry.files, body.selections)

    results: list[UploadResponse] = []
    total_inserted = 0
    total_normalized = 0

    for cached_file in entry.files:
        selection_map = selection_maps[cached_file.file_id]
        selected_points: list[SurveyPoint] = []
        for p in cached_file.points:
            layer_key = p.source_group or "(root)"
            if layer_key in selection_map:
                review_selection = selection_map[layer_key]
                overridden = p.model_copy(
                    update={"layout_family": review_selection.selected_family}
                )
                selected_points.append(overridden)

        if not selection_map:
            results.append(UploadResponse(
                status="success",
                points_inserted=0,
                normalized_placemarks=0,
                filename=cached_file.filename,
                message="No layers selected for this file",
                raw_upload_id=None,
                parser_version=settings.HISTORY_PARSER_VERSION,
            ))
            continue

        raw_id = await store_raw_upload(
            cached_file.filename,
            cached_file.content,
            cached_file.content_type,
            entry.user_email,
        )
        await store_raw_upload_layer_reviews(
            raw_id,
            list(cached_file.layer_names),
            selection_map,
            entry.user_email,
        )
        normalized_counts = await persist_normalized_ingestion_layers(
            raw_id,
            cached_file.kml_text,
            cached_file.filename,
            selected_layers=selection_map,
        )
        normalized_placemarks = normalized_counts[1]
        inserted = await insert_survey_points(
            selected_points, cached_file.filename, raw_id
        )
        total_inserted += inserted
        total_normalized += normalized_placemarks

        results.append(UploadResponse(
            status="success",
            points_inserted=inserted,
            normalized_placemarks=normalized_placemarks,
            filename=cached_file.filename,
            message=(
                f"Imported {normalized_placemarks} reviewed Placemarks "
                f"and {inserted} legacy Point records"
            ),
            raw_upload_id=str(raw_id),
            parser_version=settings.HISTORY_PARSER_VERSION,
        ))

    remove_preview(body.preview_id)

    return ConfirmUploadResponse(
        status="success",
        points_inserted=total_inserted,
        normalized_placemarks=total_normalized,
        files_processed=len(results),
        message=(
            f"Imported {total_normalized} reviewed Placemarks and "
            f"{total_inserted} legacy Point records from {len(results)} file(s)"
        ),
        details=results,
    )


@router.get("/review-items", response_model=ReviewItemsResponse)
async def get_review_items(
    raw_upload_id: UUID,
    user_info: dict = Depends(verify_google_token),
):
    """Return reviewable historical-data decisions for one upload."""
    data = await fetch_historical_review_items(raw_upload_id)
    if not data["source_file"]:
        raise HTTPException(status_code=404, detail="Raw upload not found")
    return _review_response_from_data(data)


@router.get("/review-kml")
async def get_review_kml(
    raw_upload_id: UUID,
    user_info: dict = Depends(verify_google_token),
) -> Response:
    """Download a KML overlay that labels review items with stable IDs."""
    data = await fetch_historical_review_items(raw_upload_id)
    if not data["source_file"]:
        raise HTTPException(status_code=404, detail="Raw upload not found")
    kml = _generate_review_kml(data)
    safe_name = (data["source_file"].rsplit(".", 1)[0] or "review").replace('"', "")
    return Response(
        content=kml,
        media_type="application/vnd.google-earth.kml+xml",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_review_items.kml"'
        },
    )


@router.post("/submit-review", response_model=SubmitReviewResponse)
async def submit_review(
    body: SubmitReviewRequest,
    user_info: dict = Depends(verify_google_token),
):
    """Persist human review decisions for Camera-Site review items."""
    saved, dismissed = await submit_historical_review_decisions(
        UUID(body.raw_upload_id),
        [decision.model_dump() for decision in body.decisions],
        user_info.get("email", ""),
    )
    return SubmitReviewResponse(
        status="success",
        saved_count=saved,
        dismissed_count=dismissed,
        message=(
            f"Saved {saved} review decision(s) and dismissed "
            f"{dismissed} item(s)."
        ),
    )


def _resolve_preview_selections(
    cached_files: list[PreviewFileData],
    selections,
) -> dict[str, dict[str, LayerReviewSelection]]:
    """Resolve selections by preview file identity and reject ambiguous legacy input."""
    result = {cached_file.file_id: {} for cached_file in cached_files}
    files_by_id = {cached_file.file_id: cached_file for cached_file in cached_files}
    for selection in selections:
        if selection.file_id:
            cached_file = files_by_id.get(selection.file_id)
            if cached_file is None:
                raise HTTPException(status_code=400, detail="Unknown preview file selection")
        else:
            candidates = [
                cached_file
                for cached_file in cached_files
                if selection.layer_name in cached_file.layer_names
                and (
                    not selection.filename
                    or cached_file.filename == selection.filename
                )
            ]
            if len(candidates) != 1:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Layer selection is ambiguous. Refresh the Extension and "
                        "preview the files again."
                    ),
                )
            cached_file = candidates[0]

        if selection.layer_name not in cached_file.layer_names:
            raise HTTPException(
                status_code=400,
                detail="Selected Layer does not exist in the previewed file",
            )
        result[cached_file.file_id][selection.layer_name] = LayerReviewSelection(
            selected_family=selection.selected_family,
            review_tags=tuple(dict.fromkeys(selection.review_tags)),
        )
    return result


# ---------- Legacy Upload Endpoints (backward-compatible) ----------


@router.post("/upload-kml", response_model=UploadResponse)
async def upload_survey_kml(
    file: UploadFile = File(...),
    user_info: dict = Depends(verify_google_token)
):
    """Upload a completed survey KML file to update the historical database."""
    try:
        return await _process_uploaded_kml_file(file, user_info.get("email", ""))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing uploaded KML: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-kml-batch", response_model=BatchUploadResponse)
async def upload_survey_kml_batch(
    files: list[UploadFile] = File(...),
    user_info: dict = Depends(verify_google_token)
):
    """Upload multiple completed survey KML/KMZ files in one request."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    results: list[UploadResponse] = []
    try:
        for file in files:
            results.append(await _process_uploaded_kml_file(file, user_info.get("email", "")))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing uploaded KML batch: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    total_points = sum(result.points_inserted for result in results)
    return BatchUploadResponse(
        status="success",
        files_processed=len(results),
        points_inserted=total_points,
        results=results,
        message=(
            f"Successfully processed {len(results)} file"
            f"{'' if len(results) == 1 else 's'} with {total_points} point"
            f"{'' if total_points == 1 else 's'} inserted"
        ),
    )
