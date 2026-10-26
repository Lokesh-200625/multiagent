"""Google Routes API provider (Compute Routes). Used only when
settings.google_maps_api_key is configured. Supports driving (traffic-aware),
walking, and transit modes."""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone

from app.core.config import settings
from app.travel.schemas import Coordinate, TravelFact

logger = logging.getLogger(__name__)

_MODE_MAP = {
    "driving": "DRIVING",
    "walking": "WALKING",
    "transit": "TRANSIT",
}

_FIELD_MASK = ",".join(
    [
        "routes.distanceMeters",
        "routes.duration",
        "routes.durationInTraffic",
        "routes.legs.steps.navigationInstruction",
        "routes.legs.steps.travelMode",
    ]
)


def _log(status: str, started_at: float) -> None:
    logger.info(
        "provider=%s kind=%s ms=%.2f status=%s",
        "google_routes",
        "route",
        (time.monotonic() - started_at) * 1000.0,
        status,
    )


def route_google(
    origin: Coordinate,
    destination: Coordinate,
    *,
    mode: str = "driving",
    timeout_seconds: float | None = None,
) -> list[TravelFact]:
    started_at = time.monotonic()

    try:
        if not settings.google_maps_api_key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY is not configured.")

        travel_mode = _MODE_MAP.get(mode, "DRIVING")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        payload: dict = {
            "origin": {
                "location": {
                    "latLng": {
                        "latitude": origin.latitude,
                        "longitude": origin.longitude,
                    }
                }
            },
            "destination": {
                "location": {
                    "latLng": {
                        "latitude": destination.latitude,
                        "longitude": destination.longitude,
                    }
                }
            },
            "travelMode": travel_mode,
            "languageCode": "en",
            "units": "METRIC",
            "computeAlternativeRoutes": False,
        }

        if travel_mode == "DRIVING":
            payload["routingPreference"] = "TRAFFIC_AWARE"
            payload["departureTime"] = now
        elif travel_mode == "TRANSIT":
            payload["transitOptions"] = {"departureTime": now}

        request = urllib.request.Request(
            f"{settings.google_routes_url}?key={settings.google_maps_api_key}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Goog-FieldMask": _FIELD_MASK,
            },
            method="POST",
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds or settings.google_timeout_s,
        ) as response:
            data = json.loads(response.read().decode("utf-8"))

        routes = data.get("routes") or []
        if not routes:
            raise RuntimeError(f"Google returned no routes for mode={travel_mode}")

        route = routes[0]
        fetched_at = datetime.now(timezone.utc)
        facts: list[TravelFact] = []

        distance_m = route.get("distanceMeters")
        if distance_m is not None:
            facts.append(
                TravelFact(
                    key="distance",
                    value=round(float(distance_m) / 1000.0, 2),
                    unit="km",
                    provider="google_routes",
                    source_tier="google",
                    fetched_at=fetched_at,
                    confidence=0.9,
                )
            )

        duration_s = (route.get("duration") or {}).get("seconds")
        if duration_s is not None:
            facts.append(
                TravelFact(
                    key="duration",
                    value=round(float(duration_s) / 60.0, 2),
                    unit="minutes",
                    provider="google_routes",
                    source_tier="google",
                    fetched_at=fetched_at,
                    confidence=0.9,
                )
            )

        traffic_s = (route.get("durationInTraffic") or {}).get("seconds")
        if traffic_s is not None:
            facts.append(
                TravelFact(
                    key="duration_in_traffic",
                    value=round(float(traffic_s) / 60.0, 2),
                    unit="minutes",
                    provider="google_routes",
                    source_tier="google",
                    fetched_at=fetched_at,
                    confidence=0.9,
                    metadata={"traffic_model": "best_guess"},
                )
            )

        steps: list[str] = []
        for leg in route.get("legs") or []:
            for step in leg.get("steps") or []:
                instruction = (
                    (step.get("navigationInstruction") or {}).get("instructions")
                    or ""
                ).strip()
                if instruction:
                    steps.append(instruction)
        if steps:
            facts.append(
                TravelFact(
                    key="steps",
                    value=steps[:12],
                    unit=None,
                    provider="google_routes",
                    source_tier="google",
                    fetched_at=fetched_at,
                    confidence=0.9,
                )
            )

        if not facts:
            raise RuntimeError("Google route contained no usable fields")

        _log("success", started_at)
        return facts

    except Exception:
        _log("error", started_at)
        raise
