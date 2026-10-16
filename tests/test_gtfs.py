from __future__ import annotations

import io
import zipfile

from app.travel.transit.gtfs import parse_gtfs


def _gtfs_zip() -> bytes:
    files = {
        "agency.txt": (
            "agency_id,agency_name,agency_url\n"
            "A1,Test Transit,https://example.com\n"
        ),
        "stops.txt": (
            "stop_id,stop_name,stop_lat,stop_lon\n"
            "S1,Start,17.385,78.4867\n"
            "S2,End,17.400,78.500\n"
        ),
        "routes.txt": (
            "route_id,agency_id,route_short_name,"
            "route_long_name,route_type\n"
            "R1,A1,10,Test Route,3\n"
        ),
        "trips.txt": (
            "route_id,service_id,trip_id,trip_headsign\n"
            "R1,WK,T1,End\n"
        ),
        "stop_times.txt": (
            "trip_id,arrival_time,departure_time,"
            "stop_id,stop_sequence\n"
            "T1,08:00:00,08:00:00,S1,1\n"
            "T1,08:30:00,08:30:00,S2,2\n"
        ),
        "fare_attributes.txt": (
            "fare_id,price,currency_type,payment_method\n"
            "F1,25.0,INR,0\n"
        ),
    }

    buffer = io.BytesIO()

    with zipfile.ZipFile(
        buffer,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)

    return buffer.getvalue()


def test_parse_gtfs():
    dataset = parse_gtfs(
        _gtfs_zip(),
        provider_id="test",
        source_url="https://example.com/feed.zip",
        attribution="Test feed",
    )

    assert len(dataset.agencies) == 1
    assert len(dataset.stops) == 2
    assert len(dataset.routes) == 1
    assert len(dataset.trips) == 1
    assert len(dataset.stop_times) == 2
    assert len(dataset.fares) == 1

    assert dataset.routes[0].mode == "bus"
    assert dataset.fares[0].currency == "INR"
    assert dataset.metadata.provider_id == "test"
    assert dataset.metadata.static is True