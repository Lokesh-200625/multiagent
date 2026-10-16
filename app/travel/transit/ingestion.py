from __future__ import annotations

from app.travel.transit.cache import get_dataset, set_dataset
from app.travel.transit.downloader import (
    download_gtfs,
    validate_gtfs_zip,
)
from app.travel.transit.gtfs import parse_gtfs
from app.travel.transit.providers import get_provider
from app.travel.transit.schemas import TransitDataset


def ingest_gtfs(
    provider_id: str,
    feed_url: str,
    *,
    timeout_seconds: float = 10.0,
    force_refresh: bool = False,
) -> TransitDataset:
    provider = get_provider(provider_id)

    if not force_refresh:
        cached = get_dataset(provider.provider_id)

        if cached is not None:
            return cached

    data = download_gtfs(
        feed_url,
        timeout_seconds=timeout_seconds,
    )

    validate_gtfs_zip(data)

    dataset = parse_gtfs(
        data,
        provider_id=provider.provider_id,
        source_url=feed_url,
        attribution=provider.attribution,
    )

    set_dataset(
        provider.provider_id,
        dataset,
    )

    return dataset