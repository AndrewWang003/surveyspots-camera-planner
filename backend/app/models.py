"""Pydantic v2 request / response models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from typing import Literal

from pydantic import BaseModel, Field


class EquipmentType(str, Enum):
    """Supported traffic-survey equipment types."""

    NODE = "node"
    ZONE = "zone"
    CAMERA_DIR = "camera_dir"
    CAMERA_OMNI = "camera_omni"


class ClusteredPoint(BaseModel):
    """A spatially-clustered reference point returned by the database."""

    lat: float
    lng: float
    azimuth: float | None = None
    equipment_type: EquipmentType
    layout_family: str = "unknown"
    source_group: str = ""
    total_usage: int
    time_decay_score: float = 0.0
    record_count: int
    distance_m: float
    latest_timestamp: datetime


class SurveyPoint(BaseModel):
    """A single survey point parsed from an uploaded KML file."""

    lat: float
    lng: float
    azimuth: float | None = None
    equipment_type: EquipmentType = EquipmentType.NODE
    layout_family: str = "unknown"
    source_group: str = ""
    name: str = ""
    description: str = ""
    required_camera_count: int | None = None
    camera_count_source: str = ""
    site_name: str = ""
    site_source_group: str = ""
    site_lat: float | None = None
    site_lng: float | None = None
    site_distance_m: float | None = None


class PinLocation(BaseModel):
    """A user-selected site pin parsed from a My Maps KML export."""

    lat: float
    lng: float
    name: str = ""
    description: str = ""
    icon_color: str = ""
    task_family: str = "unknown"


class PolygonLocation(BaseModel):
    """A user-drawn request Polygon parsed without historical persistence."""

    name: str = ""
    description: str = ""
    outer_ring: tuple[tuple[float, ...], ...]
    inner_rings: tuple[tuple[tuple[float, ...], ...], ...] = ()


class UploadResponse(BaseModel):
    """Response payload for KML upload."""

    status: str
    points_inserted: int
    normalized_placemarks: int = 0
    filename: str
    message: str
    raw_upload_id: str | None = None
    parser_version: str | None = None


class BatchUploadResponse(BaseModel):
    """Response payload for a multi-file KML upload."""

    status: str
    files_processed: int
    points_inserted: int
    results: list[UploadResponse]
    message: str


class HealthResponse(BaseModel):
    """Response payload for the health-check endpoint."""

    status: str
    version: str
    database: str


# ---------- Two-Step Upload Preview Models ----------


class LayerPreview(BaseModel):
    """Summary of a detected layer/folder in a KML file."""

    layer_name: str
    inferred_family: str
    point_count: int
    placemark_count: int = 0
    equipment_types: list[str]
    geometry_types: list[str] = Field(default_factory=list)
    business_role_counts: dict[str, int] = Field(default_factory=dict)
    sample_names: list[str] = Field(
        default_factory=list, description="First 3 placemark names"
    )


class FilePreview(BaseModel):
    """Preview of a single parsed KML file."""

    file_id: str = ""
    filename: str
    total_points: int
    total_placemarks: int = 0
    layers: list[LayerPreview]


class PreviewResponse(BaseModel):
    """Response from the preview endpoint."""

    preview_id: str
    files: list[FilePreview]
    message: str


class LayerSelection(BaseModel):
    """User's selection for a single layer to import."""

    file_id: str = ""
    filename: str = ""
    layer_name: str
    selected_family: Literal["ic", "od", "ipod", "hik", "atc", "site", "unknown"]
    review_tags: list[
        Literal["site", "camera", "direction", "polygon", "route", "atc"]
    ] = Field(default_factory=list)


class ConfirmUploadRequest(BaseModel):
    """Request to confirm and commit a previewed upload."""

    preview_id: str
    selections: list[LayerSelection]


class ConfirmUploadResponse(BaseModel):
    """Response from the confirm endpoint."""

    status: str
    points_inserted: int
    normalized_placemarks: int = 0
    files_processed: int
    message: str
    details: list[UploadResponse] = Field(default_factory=list)


class ReviewSiteCandidate(BaseModel):
    """A possible Site assignment for one review item."""

    site_observation_id: str
    name: str
    task_family: str
    distance_m: float | None = None
    lat: float | None = None
    lng: float | None = None
    is_recommended: bool = False


class ReviewItem(BaseModel):
    """A user-reviewable historical-data decision."""

    review_id: str
    item_type: Literal["camera_site", "camera_count_conflict"]
    title: str
    reason: str
    source_file: str
    raw_upload_id: str
    camera_observation_id: str | None = None
    camera_name: str = ""
    camera_task_family: str = ""
    lat: float | None = None
    lng: float | None = None
    current_site_observation_id: str | None = None
    current_site_name: str = ""
    current_site_task_family: str = ""
    suggested_relationship_role: Literal[
        "primary", "fallback", "unassigned", "ignored"
    ] = "primary"
    candidate_sites: list[ReviewSiteCandidate] = Field(default_factory=list)


class ReviewItemsResponse(BaseModel):
    """Review items for one historical upload."""

    raw_upload_id: str
    source_file: str
    review_count: int
    items: list[ReviewItem]


class ReviewDecision(BaseModel):
    """One human decision from the Extension review panel."""

    camera_observation_id: str
    selected_site_observation_id: str | None = None
    selected_task_family: Literal["ic", "od", "ipod", "hik", "atc", "unknown"] = "unknown"
    relationship_role: Literal["primary", "fallback", "unassigned"] = "primary"
    ignored: bool = False


class SubmitReviewRequest(BaseModel):
    """Request to save human review decisions for one upload."""

    raw_upload_id: str
    decisions: list[ReviewDecision]


class SubmitReviewResponse(BaseModel):
    """Response after saving review decisions."""

    status: str
    saved_count: int
    dismissed_count: int
    message: str
