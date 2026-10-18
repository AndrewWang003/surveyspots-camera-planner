"""Application configuration via environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


def get_projected_srid(lng: float) -> int:
    """Return the local projected SRID for metre-scale clustering.

    Australia uses GDA2020 MGA zones 49-56 (EPSG:7849-7856).
    New Zealand uses NZTM2000 (EPSG:2193).
    """
    if lng > 165:
        return 2193

    zone = int((lng + 186) // 6)
    if 49 <= zone <= 56:
        return 7800 + zone

    msg = f"Longitude {lng} is outside supported Australia/New Zealand bounds."
    raise ValueError(msg)


class Settings(BaseSettings):
    """Settings loaded from ``.env`` (or real environment variables)."""

    DATABASE_URL: str
    ALLOWED_ORIGINS: str = ""  # comma-separated
    DOMAIN_WHITELIST: str = "company.com.au"  # comma-separated
    DBSCAN_EPS_METERS: float = 1.5
    HEADING_BIN_DEG: float = 90.0
    RAW_PLACEMARK_PARSER_VERSION: str = "raw-v2-polygon-evidence-2026-07-16"
    CLASSIFICATION_RULE_VERSION: str = "rules-v2-atc-context-2026-07-17"
    OBSERVATION_NORMALIZATION_VERSION: str = "observations-v2-direction-derived-camera-2026-07-17"
    CAMERA_SITE_RELATIONSHIP_VERSION: str = "camera-site-v1-primary-fallback-2026-07-16"
    CAMERA_LOCATION_CLUSTER_VERSION: str = "camera-cluster-v1-complete-link-2026-07-16"
    CAMERA_LOCATION_CLUSTER_MAX_DIAMETER_M: float = 1.5
    CAMERA_COUNT_DECISION_VERSION: str = "camera-count-v1-explicit-or-mode-2026-07-16"
    CAMERA_GENERATION_VERSION: str = "camera-generation-v2-weighted-2026-07-16"
    NEARBY_HISTORICAL_CAMERA_RADIUS_M: float = 50.0
    POLYGON_SCOPE_VERSION: str = "polygon-scope-v1-containment-2026-07-16"
    CANONICAL_SITE_VERSION: str = "canonical-site-v1-complete-link-2026-07-16"
    HISTORY_PARSER_VERSION: str = "ic-v6-site-count-context-2026-07-16"
    CORE_RADIUS_M: float = 50.0
    FADE_RADIUS_M: float = 50.0
    SITE_MATCH_RADIUS_M: float = 75.0
    SITE_EQUIVALENCE_RADIUS_M: float = 25.0
    GOOGLE_CLIENT_ID: str = ""

    # -- derived helpers ---------------------------------------------------

    @property
    def allowed_origins_list(self) -> list[str]:
        """Return ALLOWED_ORIGINS as a trimmed list."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def domain_whitelist_list(self) -> list[str]:
        """Return DOMAIN_WHITELIST as a trimmed list."""
        return [d.strip() for d in self.DOMAIN_WHITELIST.split(",") if d.strip()]

    class Config:  # noqa: D106
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
