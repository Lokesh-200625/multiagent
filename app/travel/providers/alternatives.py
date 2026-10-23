from __future__ import annotations

import math
import urllib.parse
from typing import Any

from app.core.config import settings
from app.travel.providers.routing import _request_json
from app.travel.schemas import Coordinate


def get_osrm_alternative_routes(
    origin: Coordinate,
    destination: Coordinate,
    *,
    max_alternatives: int = 3,
) -> dict[str, Any]:
    """Retrieve distinct road routes from OSRM when available."""

    if not settings.osrm_enabled:
        return {
            "status": "UNAVAILABLE",
            "provider": "osrm",
            "routes": [],
            "reason": "provider_disabled",
        }

    if max_alternatives < 1 or max_alternatives > 5:
        raise ValueError("max_alternatives must be between 1 and 5.")

    coordinates = (
        f"{origin.longitude},{origin.latitude};"
        f"{destination.longitude},{destination.latitude}"
    )

    params = urllib.parse.urlencode({
        "alternatives": max_alternatives,
        "overview": "false",
        "steps": "false",
    })

    url = (
        f"{settings.osrm_url.rstrip('/')}"
        f"/route/v1/driving/{coordinates}?{params}"
    )

    try:
        data = _request_json(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "PilgrimAI/1.0",
            },
            timeout_seconds=settings.osrm_timeout_s,
        )
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "provider": "osrm",
            "routes": [],
            "reason": "provider_request_failed",
            "error_type": type(exc).__name__,
        }

    if data.get("code") != "Ok":
        return {
            "status": "UNAVAILABLE",
            "provider": "osrm",
            "routes": [],
            "reason": "provider_returned_error",
        }

    routes = []
    seen = set()

    for index, route in enumerate(data.get("routes", []), start=1):
        try:
            distance_km = round(float(route["distance"]) / 1000, 2)
            duration_minutes = round(float(route["duration"]) / 60, 2)
        except (KeyError, TypeError, ValueError):
            continue

        if (
            distance_km < 0
            or duration_minutes < 0
            or not math.isfinite(distance_km)
            or not math.isfinite(duration_minutes)
        ):
            continue

        signature = (distance_km, duration_minutes)
        if signature in seen:
            continue
        seen.add(signature)

        routes.append({
            "route_number": len(routes) + 1,
            "distance_km": distance_km,
            "duration_minutes": duration_minutes,
            "provider": "osrm",
            "estimated": True,
        })

        if len(routes) >= max_alternatives:
            break

    return {
        "status": "COMPLETED" if routes else "UNAVAILABLE",
        "provider": "osrm",
        "routes": routes,
        "reason": None if routes else "no_valid_routes_returned",
    }


def build_toll_status(
    *,
    provider: str,
    toll_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe toll-data availability without inventing toll charges."""
    if isinstance(toll_data, dict) and toll_data.get("verified") is True:
        amount = toll_data.get("amount_inr")
        if (
            isinstance(amount, (int, float))
            and not isinstance(amount, bool)
            and amount >= 0
            and math.isfinite(amount)
        ):
            return {
                "status": "AVAILABLE",
                "provider": provider,
                "amount_inr": amount,
                "verified": True,
                "toll_free": toll_data.get("toll_free"),
            }

    return {
        "status": "UNAVAILABLE",
        "provider": provider,
        "amount_inr": None,
        "verified": False,
        "toll_free": None,
        "message": (
            "Toll charges are unavailable from the current routing provider. "
            "This does not mean the route is toll-free."
        ),
    }
