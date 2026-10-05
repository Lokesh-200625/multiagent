from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

from app.core.config import settings
from app.services.session import redis_client
from app.travel.cache import increment_quota
from app.travel.schemas import Coordinate, TravelFact

logger = logging.getLogger(__name__)


def _request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers=headers or {},
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout_seconds,
    ) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def _timestamp() -> datetime:
    return datetime.now(timezone.utc)


def _log_provider_latency(
    provider: str,
    kind: str,
    started_at: float,
    status: str,
) -> None:
    elapsed_ms = (time.monotonic() - started_at) * 1000.0
    logger.info(
        "provider=%s kind=%s ms=%.2f status=%s",
        provider,
        kind,
        elapsed_ms,
        status,
    )


def try_consume_ors_quota() -> bool:
    date_key = date.today().isoformat()

    count = increment_quota(
        provider="ors",
        date_key=date_key,
        ttl_seconds=60 * 60 * 24,
    )

    if count is None:
        return False

    return count <= settings.ors_daily_quota


def route_ors(
    origin: Coordinate,
    destination: Coordinate,
    *,
    timeout_seconds: float | None = None,
) -> list[TravelFact]:
    started_at = time.monotonic()

    try:
        if not settings.ors_enabled:
            raise RuntimeError("ORS provider is disabled.")

        if not settings.ors_api_key:
            raise RuntimeError(
                "ORS_API_KEY is not configured."
            )

        if not try_consume_ors_quota():
            raise RuntimeError(
                "ORS daily quota is exhausted or unavailable."
            )

        coordinates = [
            [origin.longitude, origin.latitude],
            [destination.longitude, destination.latitude],
        ]

        payload = json.dumps(
            {
                "coordinates": coordinates,
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            settings.ors_url,
            data=payload,
            headers={
                "Authorization": settings.ors_api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(
            request,
            timeout=(
                timeout_seconds
                if timeout_seconds is not None
                else settings.ors_timeout_s
            ),
        ) as response:
            data = json.loads(
                response.read().decode("utf-8")
            )

        route = data["routes"][0]
        summary = route["summary"]
        fetched_at = _timestamp()

        facts = [
            TravelFact(
                key="distance",
                value=round(
                    float(summary["distance"]) / 1000.0,
                    2,
                ),
                unit="km",
                provider="openrouteservice",
                source_tier="open-data",
                fetched_at=fetched_at,
                confidence=0.8,
            ),
            TravelFact(
                key="duration",
                value=round(
                    float(summary["duration"]) / 60.0,
                    2,
                ),
                unit="minutes",
                provider="openrouteservice",
                source_tier="open-data",
                fetched_at=fetched_at,
                confidence=0.8,
            ),
            TravelFact(
                key="route",
                value=route.get("geometry"),
                unit=None,
                provider="openrouteservice",
                source_tier="open-data",
                fetched_at=fetched_at,
                confidence=0.8,
                metadata={
                    "format": "encoded_polyline",
                },
            ),
        ]

        _log_provider_latency(
            "openrouteservice",
            "route",
            started_at,
            "success",
        )

        return facts

    except Exception:
        _log_provider_latency(
            "openrouteservice",
            "route",
            started_at,
            "error",
        )
        raise


def route_osrm(
    origin: Coordinate,
    destination: Coordinate,
    *,
    timeout_seconds: float | None = None,
) -> list[TravelFact]:
    started_at = time.monotonic()

    try:
        if not settings.osrm_enabled:
            raise RuntimeError("OSRM provider is disabled.")

        coordinates = (
            f"{origin.longitude},{origin.latitude};"
            f"{destination.longitude},{destination.latitude}"
        )

        params = urllib.parse.urlencode(
            {
                "overview": "false",
            }
        )

        url = (
            f"{settings.osrm_url.rstrip('/')}"
            f"/route/v1/driving/{coordinates}?{params}"
        )

        data = _request_json(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "PilgrimAI/1.0",
            },
            timeout_seconds=(
                timeout_seconds
                if timeout_seconds is not None
                else settings.osrm_timeout_s
            ),
        )

        if data.get("code") != "Ok":
            raise RuntimeError(
                f"OSRM returned code={data.get('code')}"
            )

        route = data["routes"][0]
        fetched_at = _timestamp()

        facts = [
            TravelFact(
                key="distance",
                value=round(
                    float(route["distance"]) / 1000.0,
                    2,
                ),
                unit="km",
                provider="osrm",
                source_tier="open-data",
                fetched_at=fetched_at,
                confidence=0.8,
            ),
            TravelFact(
                key="duration",
                value=round(
                    float(route["duration"]) / 60.0,
                    2,
                ),
                unit="minutes",
                provider="osrm",
                source_tier="open-data",
                fetched_at=fetched_at,
                confidence=0.8,
            ),
        ]

        _log_provider_latency(
            "osrm",
            "route",
            started_at,
            "success",
        )

        return facts

    except Exception:
        _log_provider_latency(
            "osrm",
            "route",
            started_at,
            "error",
        )
        raise


def route_haversine(
    origin: Coordinate,
    destination: Coordinate,
) -> list[TravelFact]:
    import math

    radius_km = 6371.0088

    lat1 = math.radians(origin.latitude)
    lat2 = math.radians(destination.latitude)

    delta_lat = math.radians(
        destination.latitude - origin.latitude
    )
    delta_lon = math.radians(
        destination.longitude - origin.longitude
    )

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )

    distance = (
        radius_km
        * 2
        * math.atan2(
            math.sqrt(a),
            math.sqrt(1 - a),
        )
    )

    return [
        TravelFact(
            key="distance",
            value=round(distance, 2),
            unit="km",
            provider="haversine",
            source_tier="approx",
            fetched_at=_timestamp(),
            confidence=0.3,
            approximate=True,
            metadata={
                "distance_type": "straight_line",
            },
        )
    ]