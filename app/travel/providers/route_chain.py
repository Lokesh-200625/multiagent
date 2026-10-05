from __future__ import annotations

import logging
from typing import Callable

from app.travel.providers.routing import (
    route_haversine,
    route_ors,
    route_osrm,
)
from app.travel.schemas import (
    Coordinate,
    ProviderError,
    TravelEvidence,
    TravelFact,
    TravelResult,
)

logger = logging.getLogger(__name__)


RouteProvider = Callable[
    [Coordinate, Coordinate],
    list[TravelFact],
]


def _build_evidence(
    facts: list[TravelFact],
) -> list[TravelEvidence]:
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


def route_with_fallbacks(
    origin: Coordinate,
    destination: Coordinate,
    *,
    use_ors: bool = True,
    use_osrm: bool = True,
    allow_haversine: bool = True,
) -> TravelResult:
    """
    Deterministic route provider chain:

        OpenRouteService
            ↓ failure
        OSRM
            ↓ failure
        Haversine

    Haversine provides approximate straight-line distance only.
    """

    errors: list[ProviderError] = []

    providers: list[tuple[str, RouteProvider]] = []

    if use_ors:
        providers.append(
            (
                "openrouteservice",
                route_ors,
            )
        )

    if use_osrm:
        providers.append(
            (
                "osrm",
                route_osrm,
            )
        )

    for provider_name, provider in providers:
        try:
            facts = provider(
                origin,
                destination,
            )

            if not facts:
                raise RuntimeError(
                    "Provider returned no travel facts."
                )

            return TravelResult(
                status="COMPLETED",
                facts=facts,
                evidence=_build_evidence(facts),
                errors=errors,
                metadata={
                    "selected_provider": provider_name,
                    "fallbacks_attempted": [
                        error.provider
                        for error in errors
                    ],
                },
            )

        except Exception as exc:
            logger.warning(
                "Travel provider failed: %s: %s",
                provider_name,
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
            facts = route_haversine(
                origin,
                destination,
            )

            return TravelResult(
                status="PARTIAL",
                facts=facts,
                evidence=_build_evidence(facts),
                errors=errors,
                metadata={
                    "selected_provider": "haversine",
                    "approximate": True,
                    "distance_type": "straight_line",
                    "fallbacks_attempted": [
                        error.provider
                        for error in errors
                    ],
                },
            )

        except Exception as exc:
            logger.warning(
                "Haversine fallback failed: %s",
                exc,
            )

            errors.append(
                ProviderError(
                    provider="haversine",
                    operation="route",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    retryable=False,
                )
            )

    return TravelResult(
        status="FAILED",
        facts=[],
        evidence=[],
        errors=errors,
        metadata={
            "selected_provider": None,
            "fallbacks_attempted": [
                error.provider
                for error in errors
            ],
        },
    )