from __future__ import annotations

import json
import logging
import math
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

from app.core.config import settings
from app.travel.schemas import Coordinate, TravelFact

logger = logging.getLogger(__name__)

_OSRM_PROFILES = {"driving": "driving", "walking": "walking"}


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
        return json.loads(response.read().decode("utf-8"))


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


def route_osrm(
    origin: Coordinate,
    destination: Coordinate,
    *,
    mode: str = "driving",
    timeout_seconds: float | None = None,
) -> list[TravelFact]:
    started_at = time.monotonic()

    try:
        if not settings.osrm_enabled:
            raise RuntimeError("OSRM provider is disabled.")

        profile = _OSRM_PROFILES.get(mode)
        if profile is None:
            raise RuntimeError(f"OSRM has no profile for mode={mode}.")

        coordinates = (
            f"{origin.longitude},{origin.latitude};"
            f"{destination.longitude},{destination.latitude}"
        )
        params = urllib.parse.urlencode({"overview": "false"})
        url = (
            f"{settings.osrm_url.rstrip('/')}"
            f"/route/v1/{profile}/{coordinates}?{params}"
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
            raise RuntimeError(f"OSRM returned code={data.get('code')}")

        route = data["routes"][0]
        fetched_at = _timestamp()

        facts = [
            TravelFact(
                key="distance",
                value=round(float(route["distance"]) / 1000.0, 2),
                unit="km",
                provider="osrm",
                source_tier="open-data",
                fetched_at=fetched_at,
                confidence=0.8,
            ),
            TravelFact(
                key="duration",
                value=round(float(route["duration"]) / 60.0, 2),
                unit="minutes",
                provider="osrm",
                source_tier="open-data",
                fetched_at=fetched_at,
                confidence=0.8,
            ),
        ]

        _log_provider_latency("osrm", "route", started_at, "success")
        return facts

    except Exception:
        _log_provider_latency("osrm", "route", started_at, "error")
        raise


def route_haversine(
    origin: Coordinate,
    destination: Coordinate,
) -> list[TravelFact]:
    radius_km = 6371.0088

    lat1 = math.radians(origin.latitude)
    lat2 = math.radians(destination.latitude)

    delta_lat = math.radians(destination.latitude - origin.latitude)
    delta_lon = math.radians(destination.longitude - origin.longitude)

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

    distance = round(distance, 2)
    duration_min = round((distance / 40.0) * 60, 2) if distance > 0 else 0
    now = _timestamp()

    return [
        TravelFact(
            key="distance",
            value=distance,
            unit="km",
            provider="haversine",
            source_tier="approx",
            fetched_at=now,
            confidence=0.3,
            approximate=True,
            metadata={"distance_type": "straight_line"},
        ),
        TravelFact(
            key="duration",
            value=duration_min,
            unit="minutes",
            provider="haversine",
            source_tier="approx",
            fetched_at=now,
            confidence=0.3,
            approximate=True,
            metadata={"estimated_speed_kmph": 40},
        ),
    ]
