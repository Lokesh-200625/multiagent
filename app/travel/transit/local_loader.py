from __future__ import annotations

from pathlib import Path

from app.travel.transit.cache import set_dataset
from app.travel.transit.gtfs import parse_gtfs
from app.travel.transit.providers import get_provider


def load_local_gtfs(
    provider_id: str,
    file_path: str,
):
    provider = get_provider(provider_id)

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"GTFS file not found: {path}"
        )

    data = path.read_bytes()

    dataset = parse_gtfs(
        data,
        provider_id=provider.provider_id,
        source_url=f"local://{path.as_posix()}",
        attribution=provider.attribution,
    )

    set_dataset(
        provider.provider_id,
        dataset,
    )

    return dataset