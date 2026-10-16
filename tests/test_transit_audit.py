from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from app.travel.transit.audit import (
    audit_gtfs_zip,
    write_audit_csv,
    write_audit_json,
)


def _make_gtfs_zip(
    path: Path,
) -> None:
    files = {
        "stops.txt": """stop_id,stop_name,stop_lat,stop_lon
S1,Hyderabad Stop,17.3850,78.4867
S1,Duplicate Hyderabad Stop,17.3860,78.4870
S2,Outside Stop,18.0000,79.0000
""",
        "routes.txt": """route_id,route_short_name,route_long_name,route_type
R1,101,Test Route,3
""",
        "calendar.txt": """service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
WK,1,1,1,1,1,0,0,20261001,20261231
""",
        "trips.txt": """route_id,service_id,trip_id
R1,WK,T1
R1,BAD_SERVICE,T2
""",
        "stop_times.txt": """trip_id,arrival_time,departure_time,stop_id,stop_sequence
T1,08:00:00,08:01:00,S1,1
T1,24:10:00,24:11:00,S2,2
T1,311:57,311:58,S1,3
T2,09:00:00,09:01:00,S999,1
T999,10:00:00,10:01:00,S1,1
""",
        "fare_attributes.txt": """fare_id,price,currency_type,payment_method,transfers
F1,12,INR,0,0
""",
        "fare_rules.txt": """fare_id,origin_id,destination_id
F1,O1,D1
BAD_FARE,O2,D2
""",
    }

    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for filename, content in files.items():
            archive.writestr(
                filename,
                content,
            )


def test_audit_detects_duplicate_and_outside_stops(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "test.zip"

    _make_gtfs_zip(zip_path)

    report = audit_gtfs_zip(
        zip_path,
        provider_id="tgsrtc",
    )

    assert report["rows"]["stops.txt"] == 3

    assert (
        report["stops"]["duplicate_stop_ids"]
        == 1
    )

    assert (
        report["stops"]["outside_bbox"]
        == 1
    )

    assert (
        report["stops"]["inside_bbox"]
        == 2
    )


def test_audit_detects_invalid_calendar_reference(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "test.zip"

    _make_gtfs_zip(zip_path)

    report = audit_gtfs_zip(
        zip_path,
        provider_id="tgsrtc",
    )

    assert (
        report["trips"]["invalid_service_id"]
        == 1
    )

    assert (
        "BAD_SERVICE"
        in report["trips"]["invalid_service_id_examples"]
    )


def test_audit_detects_orphan_trip_and_stop_ids(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "test.zip"

    _make_gtfs_zip(zip_path)

    report = audit_gtfs_zip(
        zip_path,
        provider_id="tgsrtc",
    )

    assert (
        report["stop_times"]["orphan_trip_id_rows"]
        == 1
    )

    assert (
        "T999"
        in report["stop_times"]["orphan_trip_id_values"]
    )

    assert (
        report["stop_times"]["orphan_stop_id_rows"]
        == 1
    )

    assert (
        "S999"
        in report["stop_times"]["orphan_stop_id_values"]
    )


def test_audit_classifies_normal_and_extended_times(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "test.zip"

    _make_gtfs_zip(zip_path)

    report = audit_gtfs_zip(
        zip_path,
        provider_id="tgsrtc",
    )

    arrival = report["stop_times"]["arrival_time"]

    assert arrival["normal"] == 3

    assert arrival["extended_at_24h"] == 1

    assert arrival["extended_over_24h"] == 0

    assert arrival["malformed"] == 1

    assert (
        "311:57"
        in arrival["malformed_examples"]
    )

    assert (
        arrival["malformed_hour_over_24"]
        == 1
    )


def test_audit_checks_fare_rule_integrity(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "test.zip"

    _make_gtfs_zip(zip_path)

    report = audit_gtfs_zip(
        zip_path,
        provider_id="hmrl",
    )

    assert (
        report["fares"]["fare_attributes"]["total"]
        == 1
    )

    assert (
        report["fares"]["fare_rules"]["total"]
        == 2
    )

    assert (
        report["fares"]["fare_rules"][
            "rules_unknown_fare_id"
        ]
        == 1
    )

    assert (
        "BAD_FARE"
        in report["fares"]["fare_rules"][
            "unknown_fare_id_examples"
        ]
    )


def test_audit_does_not_use_parser_or_redis(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "test.zip"

    _make_gtfs_zip(zip_path)

    report = audit_gtfs_zip(
        zip_path,
        provider_id="tgsrtc",
    )

    assert report["interpretation"][
        "database_ingestion_not_performed"
    ]

    assert report["interpretation"][
        "redis_not_used"
    ]

    assert report["interpretation"][
        "router_not_used"
    ]


def test_audit_writes_json_report(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "test.zip"

    _make_gtfs_zip(zip_path)

    report = audit_gtfs_zip(
        zip_path,
        provider_id="tgsrtc",
    )

    output = tmp_path / "audit.json"

    write_audit_json(
        report,
        output,
    )

    assert output.exists()

    loaded = json.loads(
        output.read_text(
            encoding="utf-8",
        )
    )

    assert loaded["provider_id"] == "tgsrtc"


def test_audit_writes_csv_report(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "test.zip"

    _make_gtfs_zip(zip_path)

    report = audit_gtfs_zip(
        zip_path,
        provider_id="hmrl",
    )

    output = tmp_path / "audit.csv"

    write_audit_csv(
        report,
        output,
    )

    assert output.exists()

    with output.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == [
        "metric",
        "value",
    ]

    assert any(
        row[0] == "provider_id"
        and row[1] == "hmrl"
        for row in rows
    )