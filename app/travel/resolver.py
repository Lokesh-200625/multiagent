from __future__ import annotations

from typing import Any

from app.services.entity_registry import get_entity_registry
from app.travel.geography import get_geography_registry
from app.travel.providers.nominatim import (
    resolve as nominatim_resolve,
    search as nominatim_search,
)
from app.travel.schemas import (
    Coordinate,
    PlaceCandidate,
    ResolvedPlace,
)


class TravelResolverError(RuntimeError):
    """Raised when a travel place cannot be resolved safely."""


class TravelResolverClarificationError(TravelResolverError):
    """Raised when a travel place has multiple plausible candidates."""

    def __init__(
        self,
        query: str,
        candidates: list[PlaceCandidate],
    ) -> None:
        self.query = query
        self.candidates = candidates

        super().__init__(
            f"Ambiguous travel place: {query}"
        )


def _extract_entity_id(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None

    if not isinstance(value, dict):
        return None

    for key in (
        "entity_id",
        "entityId",
        "canonical_entity_id",
        "canonicalEntityId",
        "temple_id",
        "templeId",
        "hotel_id",
        "hotelId",
        "restaurant_id",
        "restaurantId",
    ):
        candidate = value.get(key)

        if candidate is None:
            continue

        candidate = str(candidate).strip()

        if candidate:
            return candidate

    return None


def _coordinates_from_location(
    location: Any,
) -> Coordinate | None:
    if not isinstance(location, dict):
        return None

    latitude = location.get("latitude")
    longitude = location.get("longitude")

    if latitude is None or longitude is None:
        return None

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None

    if not -90 <= latitude <= 90:
        return None

    if not -180 <= longitude <= 180:
        return None

    return Coordinate(
        latitude=latitude,
        longitude=longitude,
    )


def _resolve_from_entity_registry(
    value: Any,
) -> ResolvedPlace | None:
    entity_id = _extract_entity_id(value)

    if not entity_id:
        return None

    registry = get_entity_registry()
    entity = registry.get(entity_id)

    if not entity:
        return None

    coordinates = _coordinates_from_location(
        entity.get("location")
    )

    if coordinates is None:
        return None

    canonical_name = str(
        entity.get(
            "canonical_name",
            entity_id,
        )
    )

    return ResolvedPlace(
        query=canonical_name,
        display_name=canonical_name,
        coordinates=coordinates,
        source="entity_registry",
        canonical_entity_id=str(
            entity.get(
                "entity_id",
                entity_id,
            )
        ),
        canonical_entity_type=entity.get(
            "entity_type"
        ),
        confidence=1.0,
    )


def _resolve_named_entity(
    query: str,
) -> ResolvedPlace | None:
    registry = get_entity_registry()

    result = registry.resolve(
        mention=query
    )

    if result.get("status") != "RESOLVED":
        return None

    entity = result.get("entity")

    if not isinstance(entity, dict):
        return None

    coordinates = _coordinates_from_location(
        entity.get("location")
    )

    if coordinates is None:
        return None

    canonical_name = str(
        entity.get(
            "canonical_name",
            query,
        )
    )

    return ResolvedPlace(
        query=query,
        display_name=canonical_name,
        coordinates=coordinates,
        source="entity_registry",
        canonical_entity_id=entity.get(
            "entity_id"
        ),
        canonical_entity_type=entity.get(
            "entity_type"
        ),
        confidence=1.0,
    )


def _resolve_from_geography_registry(
    query: str,
) -> ResolvedPlace | None:
    registry = get_geography_registry()

    place = registry.resolve(query)

    if place is None:
        return None

    coordinates = _coordinates_from_location(
        place.get("location")
    )

    if coordinates is None:
        return None

    canonical_name = str(
        place.get(
            "canonical_name",
            query,
        )
    )

    return ResolvedPlace(
        query=query,
        display_name=canonical_name,
        coordinates=coordinates,
        source="geography_registry",
        canonical_entity_id=str(
            place.get("place_id")
        ),
        canonical_entity_type=str(
            place.get(
                "place_type",
                "place",
            )
        ),
        confidence=1.0,
    )


def resolve_place(
    value: Any,
) -> ResolvedPlace:
    """
    Deterministic resolution order:

    1. Canonical entity ID -> Entity Registry.
    2. Named canonical entity -> Entity Registry.
    3. Known geographic place -> Geography Registry.
    4. Unknown/free text -> Nominatim.
    """

    entity_result = _resolve_from_entity_registry(
        value
    )

    if entity_result is not None:
        return entity_result

    if isinstance(value, dict):
        query = (
            value.get("query")
            or value.get("name")
        )

        if not isinstance(query, str):
            raise TravelResolverError(
                "Travel place dictionary must contain "
                "'query' or 'name'."
            )

        query = query.strip()

    elif isinstance(value, str):
        query = value.strip()

    else:
        raise TravelResolverError(
            "Travel place must be a string or dictionary."
        )

    if not query:
        raise TravelResolverError(
            "Travel place cannot be empty."
        )

    named_entity = _resolve_named_entity(
        query
    )

    if named_entity is not None:
        return named_entity

    geographic_place = _resolve_from_geography_registry(
        query
    )

    if geographic_place is not None:
        return geographic_place

    candidates = nominatim_search(
        query,
        limit=5,
    )

    if not candidates:
        raise TravelResolverError(
            f"Unable to resolve travel place: {query}"
        )

    if len(candidates) > 1:
        top = candidates[0]
        second = candidates[1]

        if (
            top.importance is not None
            and second.importance is not None
            and abs(
                top.importance - second.importance
            ) < 0.05
        ):
            raise TravelResolverClarificationError(
                query=query,
                candidates=candidates,
            )

    top = candidates[0]

    return ResolvedPlace(
        query=query,
        display_name=top.display_name,
        coordinates=Coordinate(
            latitude=top.latitude,
            longitude=top.longitude,
        ),
        source="nominatim",
        confidence=top.importance,
    )