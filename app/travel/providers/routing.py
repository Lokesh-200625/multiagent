from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.travel.schemas import Coordinate, TravelFact

logger = logging.getLogger(__name__)

ORS_URL = (
    "https://api.openrouteservice.org/v2/"
    "directions/driving-car"
)

OSRM_URL = (
    "https://router.project-osrm.org/route/v1/driving"
)


def _request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 5.0,
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


def route_ors(
    origin: Coordinate,
    destination: Coordinate,
    *,
    timeout_seconds: float = 5.0,
) -> list[TravelFact]:

    if not settings.ors_api_key:
        raise RuntimeError(
            "ORS_API_KEY is not configured."
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
        ORS_URL,
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
        timeout=timeout_seconds,
    ) as response:
        data = json.loads(
            response.read().decode("utf-8")
        )

    route = data["routes"][0]
    summary = route["summary"]

    fetched_at = _timestamp()

    return [
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


def route_osrm(
    origin: Coordinate,
    destination: Coordinate,
    *,
    timeout_seconds: float = 5.0,
) -> list[TravelFact]:

    coordinates = (
        f"{origin.longitude},{origin.latitude};"
        f"{destination.longitude},{destination.latitude}"
    )

    params = urllib.parse.urlencode(
        {
            "overview": "false",
        }
    )

    url = f"{OSRM_URL}/{coordinates}?{params}"

    data = _request_json(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "PilgrimAI/1.0",
        },
        timeout_seconds=timeout_seconds,
    )

    if data.get("code") != "Ok":
        raise RuntimeError(
            f"OSRM returned code={data.get('code')}"
        )

    route = data["routes"][0]
    fetched_at = _timestamp()

    return [
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