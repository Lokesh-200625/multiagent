from __future__ import annotations

import logging
from typing import Callable

from app.core.config import settings
from app.travel.cache import get_fact, set_fact, set_negative
from app.travel.providers.routing import route_haversine, route_osrm
from app.travel.schemas import (
    Coordinate,
    ProviderError,
    TravelEvidence,
    TravelFact,
    TravelResult,
)

logger = logging.getLogger(__name__)

RouteProvider = Callable[..., list[TravelFact]]


def _build_evidence(facts: list[TravelFact]) -> list[TravelEvidence]:
    return [
        TravelEvidence(
            fact_key=fact.key,
            provider=fact.provider,
            source_tier=fact.source_tier,
            fetched_at=fact.fetched_at,
            metadata=fact.metadata,
        )
        for fact in facts
    ]


def _cache_identifier(
    origin: Coordinate,
    destination: Coordinate,
    mode: str,
) -> tuple[str, str, str]:
    return (
        f"{origin.latitude:.6f},{origin.longitude:.6f}",
        f"{destination.latitude:.6f},{destination.longitude:.6f}",
        mode,
    )


def route_with_fallbacks(
    origin: Coordinate,
    destination: Coordinate,
    *,
    mode: str = "driving",
    allow_haversine: bool = True,
) -> TravelResult:
    """Deterministic provider chain:

        Google Routes   (only when GOOGLE_MAPS_API_KEY is configured)
            ↓ failure / not configured
        OSRM            (driving & walking profiles; skipped for transit)
            ↓ failure
        Haversine       (straight-line approximation, status=PARTIAL)

    Cached successful results are returned before any provider call.
    """
    cache_parts = _cache_identifier(origin, destination, mode)

    cached = get_fact("route", *cache_parts)
    if cached is not None:
        try:
            result = TravelResult.model_validate(cached)
            result.metadata = {
                **result.metadata,
                "cache_hit": True,
            }
            return result
        except Exception as exc:
            logger.warning("Invalid cached travel result: %s", exc)

    errors: list[ProviderError] = []
    providers: list[tuple[str, RouteProvider]] = []

    if settings.google_maps_api_key:
        from app.travel.providers.google_routes import route_google

        providers.append(("google_routes", route_google))

    if settings.osrm_enabled:
        providers.append(("osrm", route_osrm))

    for provider_name, provider in providers:
        try:
            kwargs = {"mode": mode}
            if provider_name == "osrm":
                kwargs["mode"] = mode  # routing validates profile per mode

            facts = provider(origin, destination, **kwargs)

            if not facts:
                raise RuntimeError("Provider returned no travel facts.")

            result = TravelResult(
                status="COMPLETED",
                facts=facts,
                evidence=_build_evidence(facts),
                errors=errors,
                metadata={
                    "selected_provider": provider_name,
                    "fallbacks_attempted": [
                        error.provider for error in errors
                    ],
                    "cache_hit": False,
                },
            )

            set_fact("route", *cache_parts, payload=result.model_dump(mode="json"))
            return result

        except Exception as exc:
            logger.warning(
                "Travel provider failed (%s, mode=%s): %s",
                provider_name,
                mode,
                exc,
            )
            errors.append(
                ProviderError(
                    provider=provider_name,
                    operation="route",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    retryable=True,
                )
            )

    if allow_haversine:
        try:
            facts = route_haversine(origin, destination)

            result = TravelResult(
                status="PARTIAL",
                facts=facts,
                evidence=_build_evidence(facts),
                errors=errors,
                metadata={
                    "selected_provider": "haversine",
                    "approximate": True,
                    "distance_type": "straight_line",
                    "fallbacks_attempted": [
                        error.provider for error in errors
                    ],
                    "cache_hit": False,
                },
            )

            set_fact("route", *cache_parts, payload=result.model_dump(mode="json"))
            return result

        except Exception as exc:
            logger.warning("Haversine fallback failed: %s", exc)
            errors.append(
                ProviderError(
                    provider="haversine",
                    operation="route",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    retryable=False,
                )
            )

    result = TravelResult(
        status="FAILED",
        facts=[],
        evidence=[],
        errors=errors,
        metadata={
            "selected_provider": None,
            "fallbacks_attempted": [
                error.provider for error in errors
            ],
            "cache_hit": False,
        },
    )

    set_negative(
        "route",
        *cache_parts,
        error={
            "status": "FAILED",
            "providers": [error.provider for error in errors],
        },
    )

    return result
