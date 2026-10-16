from __future__ import annotations

import io
import logging
import zipfile
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def download_gtfs(
    feed_url: str,
    *,
    timeout_seconds: float = 10.0,
) -> bytes:
    request = Request(
        feed_url,
        headers={
            "User-Agent": "PilgrimAI/1.0",
            "Accept": "application/zip, application/octet-stream",
        },
    )

    with urlopen(
        request,
        timeout=timeout_seconds,
    ) as response:
        data = response.read()

    if not data:
        raise RuntimeError(
            "GTFS provider returned an empty response."
        )

    return data


def validate_gtfs_zip(
    data: bytes,
) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            "GTFS response is not a valid ZIP archive."
        ) from exc

    required_files = {
        "agency.txt",
        "stops.txt",
        "routes.txt",
        "trips.txt",
        "stop_times.txt",
    }

    missing = required_files - names

    if missing:
        raise RuntimeError(
            "GTFS feed is missing required files: "
            + ", ".join(sorted(missing))
        )