"""Travel agent - resolve places, run the provider chain, emit structured
evidence (facts + calculations + notes). Google first when a key is configured,
then OSRM, then Haversine (approximate)."""
from __future__ import annotations

from app.core.config import settings
from app.travel.calculations import calculate_travel
from app.travel.providers.route_chain import route_with_fallbacks
from app.travel.resolver import (
    TravelResolverClarificationError,
    TravelResolverError,
    resolve_place,
)


def _place_evidence(role: str, place) -> dict:
    return {
        "source": place.source,
        "kind": "place",
        "role": role,
        "name": place.display_name,
        "latitude": place.coordinates.latitude,
        "longitude": place.coordinates.longitude,
        "resolved_via": place.source,
    }


def _route_evidence(route_result) -> list[dict]:
    items: list[dict] = []

    for fact in route_result.facts:
        value = fact.value
        if isinstance(value, str) and len(value) > 400:
            value = value[:400] + " …"
        items.append(
            {
                "source": fact.provider,
                "kind": "fact",
                "key": fact.key,
                "value": value,
                "unit": fact.unit,
                "provider": fact.provider,
                "approximate": fact.approximate,
            }
        )

    for calc in route_result.calculations:
        items.append(
            {
                "source": "calculation",
                "kind": "calculation",
                "key": calc.key,
                "value": calc.value,
                "unit": calc.unit,
                "status": calc.status,
                "estimated": calc.estimated,
                "method": calc.method,
                "provider": (calc.metadata or {}).get("provider"),
            }
        )

    items.append(
        {
            "source": "route_chain",
            "kind": "status",
            "status": route_result.status,
            "selected": (route_result.metadata or {}).get("selected_provider"),
            "approximate": bool((route_result.metadata or {}).get("approximate")),
            "errors": [
                {"provider": err.provider, "error": err.message}
                for err in route_result.errors
            ],
        }
    )
    return items


def _run_nearby(subquery: dict) -> dict:
    """travel_nearby v1: resolve the anchor place; the Places-based search
    activates with GOOGLE_MAPS_API_KEY (P4). Honest note until then."""
    anchor_raw = subquery.get("destination") or subquery.get("origin")
    if not anchor_raw or not str(anchor_raw).strip():
        return {
            "status": "ERROR",
            "error": "place_missing",
            "evidence": [
                {
                    "source": "travel",
                    "kind": "error",
                    "error": "place_missing",
                }
            ],
        }

    place = resolve_place(anchor_raw)
    note = {
        "source": "travel",
        "kind": "note",
        "key": "nearby_search_unavailable",
        "error": "Nearby places search is not available in this deployment yet.",
    }
    if settings.google_maps_api_key:
        note["hint"] = "Nearby search integration is not enabled yet."
    else:
        note["hint"] = settings.fallback_transit_link

    return {
        "status": "COMPLETED",
        "evidence": [_place_evidence("near", place), note],
    }


def run(subquery: dict) -> dict:
    """Returns {status, evidence} or raises TravelResolverClarificationError
    (caught by the runner for a composer-phrased clarification)."""
    if subquery.get("category") == "travel_nearby":
        return _run_nearby(subquery)

    origin_raw = subquery.get("origin")
    destination_raw = subquery.get("destination")

    if not origin_raw or not str(origin_raw).strip() or not destination_raw or not str(destination_raw).strip():
        return {
            "status": "ERROR",
            "error": "origin_or_destination_missing",
            "evidence": [
                {
                    "source": "travel",
                    "kind": "error",
                    "error": "origin_or_destination_missing",
                }
            ],
        }

    mode = subquery.get("mode") or "driving"

    origin = resolve_place(origin_raw)      # may raise clarification
    destination = resolve_place(destination_raw)

    route_result = route_with_fallbacks(
        origin.coordinates,
        destination.coordinates,
        mode=mode,
    )

    calculations = []
    if route_result.status in {"COMPLETED", "PARTIAL"}:
        calculations = calculate_travel(
            route_result.facts,
            cost_per_km=settings.fuel_cost_per_km,
        )
        route_result.calculations = calculations

    evidence: list[dict] = [
        _place_evidence("origin", origin),
        _place_evidence("destination", destination),
    ]
    evidence.extend(_route_evidence(route_result))

    selected = (route_result.metadata or {}).get("selected_provider")

    if mode == "transit" and not (
        route_result.status in {"COMPLETED", "PARTIAL"}
        and selected == "google_routes"
    ):
        evidence.append(
            {
                "source": "travel",
                "kind": "note",
                "key": "transit_unavailable",
                "error": "No transit/bus data available for this route right now.",
                "hint": settings.fallback_transit_link,
            }
        )

    return {
        "status": "COMPLETED" if route_result.status in {"COMPLETED", "PARTIAL"} else "ERROR",
        "evidence": evidence,
        "provider_status": route_result.status,
    }


__all__ = ["run", "TravelResolverClarificationError", "TravelResolverError"]
