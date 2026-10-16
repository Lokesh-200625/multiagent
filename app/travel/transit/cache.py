from __future__ import annotations

import json

from app.core.config import settings
from app.services.session import redis_client
from app.travel.transit.schemas import TransitDataset


def _dataset_key(provider_id: str) -> str:
    return f"transit:gtfs:{provider_id}:dataset"


def _metadata_key(provider_id: str) -> str:
    return f"transit:gtfs:{provider_id}:metadata"


def get_dataset(provider_id: str) -> TransitDataset | None:
    raw = redis_client.get(_dataset_key(provider_id))

    if not raw:
        return None

    return TransitDataset.model_validate_json(raw)


def set_dataset(
    provider_id: str,
    dataset: TransitDataset,
) -> None:
    ttl = settings.transit_cache_ttl_s

    redis_client.set(
        _dataset_key(provider_id),
        dataset.model_dump_json(),
        ex=ttl,
    )

    metadata = {
        "provider_id": provider_id,
        "source_url": dataset.metadata.source_url,
        "fetched_at": dataset.metadata.fetched_at.isoformat(),
        "static": dataset.metadata.static,
    }

    redis_client.set(
        _metadata_key(provider_id),
        json.dumps(metadata),
        ex=ttl,
    )


def get_metadata(provider_id: str) -> dict | None:
    raw = redis_client.get(_metadata_key(provider_id))

    if not raw:
        return None

    return json.loads(raw)