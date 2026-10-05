from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.travel.schemas import Coordinate, PlaceCandidate, ResolvedPlace

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Public Nominatim requires responsible usage.
MIN_REQUEST_INTERVAL_SECONDS = 1.0

_last_request_at = 0.0


def _rate_limit() -> None:
    global _last_request_at

    now = time.monotonic()
    elapsed = now - _last_request_at

    if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
        time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)

    _last_request_at = time.monotonic()


def search(
    query: str,
    *,
    timeout_seconds: float = 5.0,
    limit: int = 5,
) -> list[PlaceCandidate]:

    _rate_limit()

    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "jsonv2",
            "limit": limit,
            "addressdetails": 1,
        }
    )

    request = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={
            "User-Agent": (
                "PilgrimAI/1.0 "
                "(travel-agent; "
                "contact: pilgrimai@example.com)"
            ),
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
        ) as response:
            payload = json.loads(
                response.read().decode("utf-8")
            )

    except Exception:
        logger.exception(
            "Nominatim request failed for query=%s",
            query,
        )
        raise

    candidates: list[PlaceCandidate] = []

    for item in payload:
        try:
            candidates.append(
                PlaceCandidate(
                    display_name=str(
                        item.get("display_name", query)
                    ),
                    latitude=float(item["lat"]),
                    longitude=float(item["lon"]),
                    source="nominatim",
                    place_type=item.get("type"),
                    importance=(
                        float(item["importance"])
                        if item.get("importance") is not None
                        else None
                    ),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return candidates


def resolve(
    query: str,
    *,
    timeout_seconds: float = 5.0,
) -> ResolvedPlace | None:
    candidates = search(
        query,
        timeout_seconds=timeout_seconds,
        limit=5,
    )

    if not candidates:
        return None

    if len(candidates) > 1:
        top = candidates[0]

        if (
            top.importance is not None
            and candidates[1].importance is not None
            and abs(top.importance - candidates[1].importance) < 0.05
        ):
            return None

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