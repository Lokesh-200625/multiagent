from __future__ import annotations

import io
import zipfile

from app.travel.transit.cache import _dataset_key, _metadata_key
from app.travel.transit.ingestion import ingest_gtfs
from app.services.session import redis_client


def make_gtfs_zip() -> bytes:
    files = {
        "agency.txt": """agency_id,agency_name,agency_url,agency_timezone
TGSRTC,TGSRTC,https://tgsrtc.telangana.gov.in,Asia/Kolkata
""",
        "stops.txt": """stop_id,stop_name,stop_lat,stop_lon
S1,Hyderabad,17.3850,78.4867
S2,Yadadri,17.5900,78.9430
""",
        "routes.txt": """route_id,agency_id,route_short_name,route_long_name,route_type
R1,TGSRTC,1,Hyderabad-Yadadri,3
""",
        "trips.txt": """route_id,service_id,trip_id,trip_headsign
R1,WD,T1,Yadadri
""",
        "stop_times.txt": """trip_id,arrival_time,departure_time,stop_id,stop_sequence
T1,08:00:00,08:05:00,S1,1
T1,10:00:00,10:05:00,S2,2
""",
        "fare_attributes.txt": """fare_id,price,currency,payment_method
F1,50.00,INR,1
""",
    }

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)

    return buffer.getvalue()


def test_ingest_gtfs(monkeypatch):
    data = make_gtfs_zip()

    redis_client.delete(
        _dataset_key("tgsrtc"),
        _metadata_key("tgsrtc"),
    )

    monkeypatch.setattr(
        "app.travel.transit.ingestion.download_gtfs",
        lambda feed_url, timeout_seconds: data,
    )

    dataset = ingest_gtfs(
        "tgsrtc",
        "https://example.com/tgsrtc.zip",
    )

    assert dataset.metadata.provider_id == "tgsrtc"
    assert dataset.metadata.source_url == "https://example.com/tgsrtc.zip"

    assert len(dataset.agencies) == 1
    assert len(dataset.stops) == 2
    assert len(dataset.routes) == 1
    assert len(dataset.trips) == 1
    assert len(dataset.stop_times) == 2
    assert len(dataset.fares) == 1

    assert dataset.routes[0].mode == "bus"
    assert dataset.fares[0].currency == "INR"