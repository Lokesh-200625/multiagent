from __future__ import annotations

import argparse
import csv
import io
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


# Hyderabad bounding box used only for C0 geographic distribution checks.
#
# This is intentionally configurable through audit_gtfs_zip(..., bbox=...)
# so the production ingestion layer does not become coupled to this value.
DEFAULT_HYDERABAD_BBOX = {
    "min_lat": 17.15,
    "max_lat": 17.65,
    "min_lon": 78.15,
    "max_lon": 78.75,
}


GTFS_TIME_PATTERN = re.compile(
    r"^(?P<hours>\d+):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)$"
)


REQUIRED_FILES = {
    "stops.txt",
    "routes.txt",
    "calendar.txt",
    "trips.txt",
    "stop_times.txt",
}


def _open_csv(
    archive: zipfile.ZipFile,
    filename: str,
) -> TextIO | None:
    """
    Open a GTFS CSV directly from the ZIP.

    The returned TextIO streams decompressed content from the archive
    instead of materializing the complete file as bytes/list objects.
    """
    try:
        binary_file = archive.open(filename, "r")
    except KeyError:
        return None

    return io.TextIOWrapper(
        binary_file,
        encoding="utf-8-sig",
        newline="",
    )


def _row_count(
    archive: zipfile.ZipFile,
    filename: str,
) -> int:
    stream = _open_csv(archive, filename)

    if stream is None:
        return 0

    try:
        reader = csv.DictReader(stream)
        return sum(1 for _ in reader)
    finally:
        stream.close()


def _is_inside_bbox(
    latitude: float,
    longitude: float,
    bbox: dict[str, float],
) -> bool:
    return (
        bbox["min_lat"]
        <= latitude
        <= bbox["max_lat"]
        and bbox["min_lon"]
        <= longitude
        <= bbox["max_lon"]
    )


def _parse_gtfs_time(
    value: str | None,
) -> dict[str, Any]:
    """
    Classify a GTFS time without rejecting it.

    GTFS permits service-day times beyond 24:00:00.

    C0 deliberately does not decide whether such values should later
    be accepted, repaired, or quarantined. It only measures them.
    """
    value = (value or "").strip()

    if not value:
        return {
            "status": "missing",
            "value": value,
            "hours": None,
            "seconds": None,
        }

    match = GTFS_TIME_PATTERN.fullmatch(value)

    if match is None:
        # Also detect the known class of defective values such as
        # "311:57", which has an hour/minute shape but no seconds.
        parts = value.split(":")

        hour_like = None

        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            hour_like = int(parts[0])

        return {
            "status": "malformed",
            "value": value,
            "hours": hour_like,
            "seconds": None,
        }

    hours = int(match.group("hours"))
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))

    total_seconds = (
        hours * 3600
        + minutes * 60
        + seconds
    )

    if hours > 24:
        status = "extended_over_24h"
    elif hours == 24:
        status = "extended_at_24h"
    else:
        status = "normal"

    return {
        "status": status,
        "value": value,
        "hours": hours,
        "seconds": total_seconds,
    }


def _audit_time_column(
    archive: zipfile.ZipFile,
    column: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "total_values": 0,
        "missing": 0,
        "normal": 0,
        "extended_at_24h": 0,
        "extended_over_24h": 0,
        "malformed": 0,
        "malformed_hour_over_24": 0,
        "max_valid_hour": None,
        "max_valid_value": None,
        "malformed_examples": [],
    }

    malformed_examples: list[str] = []
    max_hours = -1
    max_value: str | None = None

    stream = _open_csv(archive, "stop_times.txt")

    if stream is None:
        return result

    try:
        reader = csv.DictReader(stream)

        for row in reader:
            result["total_values"] += 1

            parsed = _parse_gtfs_time(row.get(column))

            status = parsed["status"]
            result[status] += 1

            if (
                status == "malformed"
                and parsed["hours"] is not None
                and parsed["hours"] > 24
            ):
                result["malformed_hour_over_24"] += 1

            if parsed["hours"] is not None:
                if parsed["hours"] > max_hours:
                    max_hours = parsed["hours"]
                    max_value = parsed["value"]

            if (
                status == "malformed"
                and len(malformed_examples) < 20
            ):
                malformed_examples.append(
                    str(parsed["value"])
                )

    finally:
        stream.close()

    result["max_valid_hour"] = (
        max_hours if max_hours >= 0 else None
    )
    result["max_valid_value"] = max_value
    result["malformed_examples"] = malformed_examples

    return result


def _audit_stops(
    archive: zipfile.ZipFile,
    bbox: dict[str, float],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "total": 0,
        "duplicate_stop_ids": 0,
        "duplicate_stop_id_values": [],
        "invalid_coordinates": 0,
        "outside_bbox": 0,
        "inside_bbox": 0,
        "min_lat": None,
        "max_lat": None,
        "min_lon": None,
        "max_lon": None,
        "outside_bbox_examples": [],
    }

    stop_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    outside_examples: list[dict[str, Any]] = []

    stream = _open_csv(archive, "stops.txt")

    if stream is None:
        return result

    try:
        reader = csv.DictReader(stream)

        for row in reader:
            result["total"] += 1

            stop_id = (row.get("stop_id") or "").strip()

            if stop_id in stop_ids:
                duplicate_ids.add(stop_id)
            else:
                stop_ids.add(stop_id)

            try:
                latitude = float(row["stop_lat"])
                longitude = float(row["stop_lon"])
            except (KeyError, TypeError, ValueError):
                result["invalid_coordinates"] += 1
                continue

            result["min_lat"] = (
                latitude
                if result["min_lat"] is None
                else min(result["min_lat"], latitude)
            )
            result["max_lat"] = (
                latitude
                if result["max_lat"] is None
                else max(result["max_lat"], latitude)
            )
            result["min_lon"] = (
                longitude
                if result["min_lon"] is None
                else min(result["min_lon"], longitude)
            )
            result["max_lon"] = (
                longitude
                if result["max_lon"] is None
                else max(result["max_lon"], longitude)
            )

            if _is_inside_bbox(
                latitude,
                longitude,
                bbox,
            ):
                result["inside_bbox"] += 1
            else:
                result["outside_bbox"] += 1

                if len(outside_examples) < 20:
                    outside_examples.append(
                        {
                            "stop_id": stop_id,
                            "stop_name": row.get("stop_name"),
                            "latitude": latitude,
                            "longitude": longitude,
                        }
                    )

    finally:
        stream.close()

    result["duplicate_stop_ids"] = len(duplicate_ids)
    result["duplicate_stop_id_values"] = sorted(
        duplicate_ids
    )[:100]

    result["outside_bbox_examples"] = outside_examples

    return result


def _audit_calendars(
    archive: zipfile.ZipFile,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "total": 0,
        "service_ids": 0,
        "duplicate_service_ids": 0,
        "duplicate_service_id_values": [],
        "missing_start_date": 0,
        "missing_end_date": 0,
        "date_range": {
            "min_start_date": None,
            "max_end_date": None,
        },
        "active_day_counts": {
            "monday": 0,
            "tuesday": 0,
            "wednesday": 0,
            "thursday": 0,
            "friday": 0,
            "saturday": 0,
            "sunday": 0,
        },
        "always_on_services": 0,
        "never_on_services": 0,
    }

    service_ids: set[str] = set()
    duplicate_ids: set[str] = set()

    stream = _open_csv(archive, "calendar.txt")

    if stream is None:
        return result

    try:
        reader = csv.DictReader(stream)

        for row in reader:
            result["total"] += 1

            service_id = (row.get("service_id") or "").strip()

            if service_id in service_ids:
                duplicate_ids.add(service_id)
            else:
                service_ids.add(service_id)

            start_date = (
                row.get("start_date") or ""
            ).strip()

            end_date = (
                row.get("end_date") or ""
            ).strip()

            if not start_date:
                result["missing_start_date"] += 1

            if not end_date:
                result["missing_end_date"] += 1

            if start_date:
                current = result["date_range"]["min_start_date"]

                if current is None or start_date < current:
                    result["date_range"]["min_start_date"] = start_date

            if end_date:
                current = result["date_range"]["max_end_date"]

                if current is None or end_date > current:
                    result["date_range"]["max_end_date"] = end_date

            active_days = 0

            for day in result["active_day_counts"]:
                value = row.get(day)

                if value == "1":
                    result["active_day_counts"][day] += 1
                    active_days += 1

            if active_days == 7:
                result["always_on_services"] += 1

            if active_days == 0:
                result["never_on_services"] += 1

    finally:
        stream.close()

    result["service_ids"] = len(service_ids)
    result["duplicate_service_ids"] = len(duplicate_ids)
    result["duplicate_service_id_values"] = sorted(
        duplicate_ids
    )[:100]

    return result


def _audit_trips(
    archive: zipfile.ZipFile,
    calendar_service_ids: set[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "total": 0,
        "trip_ids": 0,
        "duplicate_trip_ids": 0,
        "duplicate_trip_id_values": [],
        "missing_route_id": 0,
        "missing_service_id": 0,
        "invalid_service_id": 0,
        "invalid_service_id_examples": [],
    }

    trip_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    invalid_service_examples: list[str] = []

    stream = _open_csv(archive, "trips.txt")

    if stream is None:
        return result

    try:
        reader = csv.DictReader(stream)

        for row in reader:
            result["total"] += 1

            trip_id = (row.get("trip_id") or "").strip()
            route_id = (row.get("route_id") or "").strip()
            service_id = (row.get("service_id") or "").strip()

            if trip_id in trip_ids:
                duplicate_ids.add(trip_id)
            else:
                trip_ids.add(trip_id)

            if not route_id:
                result["missing_route_id"] += 1

            if not service_id:
                result["missing_service_id"] += 1
            elif service_id not in calendar_service_ids:
                result["invalid_service_id"] += 1

                if len(invalid_service_examples) < 20:
                    invalid_service_examples.append(
                        service_id
                    )

    finally:
        stream.close()

    result["trip_ids"] = len(trip_ids)
    result["duplicate_trip_ids"] = len(duplicate_ids)
    result["duplicate_trip_id_values"] = sorted(
        duplicate_ids
    )[:100]
    result["invalid_service_id_examples"] = (
        invalid_service_examples
    )

    return result


def _audit_stop_times(
    archive: zipfile.ZipFile,
    stop_ids: set[str],
    trip_ids: set[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "total": 0,
        "orphan_trip_id_rows": 0,
        "orphan_trip_id_values": [],
        "orphan_stop_id_rows": 0,
        "orphan_stop_id_values": [],
        "duplicate_trip_stop_sequence": 0,
        "duplicate_trip_stop_sequence_examples": [],
        "invalid_stop_sequence": 0,
        "arrival_time": {},
        "departure_time": {},
    }

    orphan_trip_ids: set[str] = set()
    orphan_stop_ids: set[str] = set()

    seen_trip_stop_sequences: set[
        tuple[str, int]
    ] = set()

    duplicate_sequence_examples: list[dict[str, Any]] = []

    stream = _open_csv(archive, "stop_times.txt")

    if stream is None:
        return result

    try:
        reader = csv.DictReader(stream)

        for row in reader:
            result["total"] += 1

            trip_id = (row.get("trip_id") or "").strip()
            stop_id = (row.get("stop_id") or "").strip()

            if trip_id not in trip_ids:
                orphan_trip_ids.add(trip_id)

            if stop_id not in stop_ids:
                orphan_stop_ids.add(stop_id)

            try:
                sequence = int(
                    row.get("stop_sequence") or ""
                )

                if sequence < 0:
                    raise ValueError

            except ValueError:
                result["invalid_stop_sequence"] += 1
                sequence = None

            if sequence is not None:
                key = (trip_id, sequence)

                if key in seen_trip_stop_sequences:
                    result["duplicate_trip_stop_sequence"] += 1

                    if len(duplicate_sequence_examples) < 20:
                        duplicate_sequence_examples.append(
                            {
                                "trip_id": trip_id,
                                "stop_sequence": sequence,
                                "stop_id": stop_id,
                            }
                        )
                else:
                    seen_trip_stop_sequences.add(key)

    finally:
        stream.close()

    result["orphan_trip_id_rows"] = len(
        orphan_trip_ids
    )
    result["orphan_trip_id_values"] = sorted(
        orphan_trip_ids
    )[:100]

    result["orphan_stop_id_rows"] = len(
        orphan_stop_ids
    )
    result["orphan_stop_id_values"] = sorted(
        orphan_stop_ids
    )[:100]

    result["duplicate_trip_stop_sequence_examples"] = (
        duplicate_sequence_examples
    )

    result["arrival_time"] = _audit_time_column(
        archive,
        "arrival_time",
    )

    result["departure_time"] = _audit_time_column(
        archive,
        "departure_time",
    )

    return result


def _audit_fares(
    archive: zipfile.ZipFile,
) -> dict[str, Any]:
    fare_ids: set[str] = set()
    duplicate_fare_ids: set[str] = set()

    total_fares = 0
    missing_price = 0
    invalid_price = 0
    negative_price = 0

    currencies: Counter[str] = Counter()

    stream = _open_csv(
        archive,
        "fare_attributes.txt",
    )

    if stream is not None:
        try:
            reader = csv.DictReader(stream)

            for row in reader:
                total_fares += 1

                fare_id = (
                    row.get("fare_id") or ""
                ).strip()

                if fare_id in fare_ids:
                    duplicate_fare_ids.add(fare_id)
                else:
                    fare_ids.add(fare_id)

                raw_price = (
                    row.get("price") or ""
                ).strip()

                if not raw_price:
                    missing_price += 1
                else:
                    try:
                        price = float(raw_price)

                        if price < 0:
                            negative_price += 1

                    except ValueError:
                        invalid_price += 1

                currency = (
                    row.get("currency_type")
                    or row.get("currency")
                    or ""
                ).strip()

                if currency:
                    currencies[currency] += 1

        finally:
            stream.close()

    rule_count = 0
    rules_missing_fare_id = 0
    rules_unknown_fare_id = 0
    rules_missing_origin = 0
    rules_missing_destination = 0

    duplicate_rules = 0

    rule_keys: set[
        tuple[str, str, str]
    ] = set()

    unknown_fare_examples: list[str] = []

    stream = _open_csv(
        archive,
        "fare_rules.txt",
    )

    if stream is not None:
        try:
            reader = csv.DictReader(stream)

            for row in reader:
                rule_count += 1

                fare_id = (
                    row.get("fare_id") or ""
                ).strip()

                origin_id = (
                    row.get("origin_id") or ""
                ).strip()

                destination_id = (
                    row.get("destination_id") or ""
                ).strip()

                if not fare_id:
                    rules_missing_fare_id += 1
                elif fare_id not in fare_ids:
                    rules_unknown_fare_id += 1

                    if len(unknown_fare_examples) < 20:
                        unknown_fare_examples.append(
                            fare_id
                        )

                if not origin_id:
                    rules_missing_origin += 1

                if not destination_id:
                    rules_missing_destination += 1

                key = (
                    fare_id,
                    origin_id,
                    destination_id,
                )

                if key in rule_keys:
                    duplicate_rules += 1
                else:
                    rule_keys.add(key)

        finally:
            stream.close()

    return {
        "fare_attributes": {
            "total": total_fares,
            "unique_fare_ids": len(fare_ids),
            "duplicate_fare_ids": len(
                duplicate_fare_ids
            ),
            "duplicate_fare_id_values": sorted(
                duplicate_fare_ids
            )[:100],
            "missing_price": missing_price,
            "invalid_price": invalid_price,
            "negative_price": negative_price,
            "currencies": dict(currencies),
        },
        "fare_rules": {
            "total": rule_count,
            "duplicate_rules": duplicate_rules,
            "rules_missing_fare_id": (
                rules_missing_fare_id
            ),
            "rules_unknown_fare_id": (
                rules_unknown_fare_id
            ),
            "unknown_fare_id_examples": (
                unknown_fare_examples
            ),
            "rules_missing_origin": (
                rules_missing_origin
            ),
            "rules_missing_destination": (
                rules_missing_destination
            ),
        },
    }


def audit_gtfs_zip(
    zip_path: str | Path,
    *,
    provider_id: str,
    bbox: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Run the C0 streaming audit against a GTFS ZIP.

    This function intentionally does NOT call parse_gtfs().

    It only reads the ZIP's CSV streams and keeps compact reference
    sets needed for cross-table integrity checks.
    """
    path = Path(zip_path)

    if not path.exists():
        raise FileNotFoundError(
            f"GTFS ZIP not found: {path}"
        )

    bbox = bbox or DEFAULT_HYDERABAD_BBOX

    started_at = datetime.now(timezone.utc)

    with zipfile.ZipFile(path, "r") as archive:
        names = set(archive.namelist())

        missing_required_files = sorted(
            REQUIRED_FILES - names
        )

        duplicate_zip_members = [
            name
            for name, count in Counter(
                archive.namelist()
            ).items()
            if count > 1
        ]

        table_rows: dict[str, int] = {}

        for filename in sorted(
            names.intersection(
                {
                    "agency.txt",
                    "feed_info.txt",
                    "stops.txt",
                    "routes.txt",
                    "calendar.txt",
                    "trips.txt",
                    "stop_times.txt",
                    "shapes.txt",
                    "fare_attributes.txt",
                    "fare_rules.txt",
                    "transfers.txt",
                }
            )
        ):
            table_rows[filename] = _row_count(
                archive,
                filename,
            )

        stops = _audit_stops(
            archive,
            bbox,
        )

        calendars = _audit_calendars(
            archive,
        )

        calendar_service_ids: set[str] = set()

        calendar_stream = _open_csv(
            archive,
            "calendar.txt",
        )

        if calendar_stream is not None:
            try:
                reader = csv.DictReader(
                    calendar_stream
                )

                for row in reader:
                    service_id = (
                        row.get("service_id") or ""
                    ).strip()

                    if service_id:
                        calendar_service_ids.add(
                            service_id
                        )
            finally:
                calendar_stream.close()

        trips = _audit_trips(
            archive,
            calendar_service_ids,
        )

        trip_ids: set[str] = set()

        trip_stream = _open_csv(
            archive,
            "trips.txt",
        )

        if trip_stream is not None:
            try:
                reader = csv.DictReader(
                    trip_stream
                )

                for row in reader:
                    trip_id = (
                        row.get("trip_id") or ""
                    ).strip()

                    if trip_id:
                        trip_ids.add(trip_id)
            finally:
                trip_stream.close()

        stop_ids: set[str] = set()

        stop_stream = _open_csv(
            archive,
            "stops.txt",
        )

        if stop_stream is not None:
            try:
                reader = csv.DictReader(
                    stop_stream
                )

                for row in reader:
                    stop_id = (
                        row.get("stop_id") or ""
                    ).strip()

                    if stop_id:
                        stop_ids.add(stop_id)
            finally:
                stop_stream.close()

        stop_times = _audit_stop_times(
            archive,
            stop_ids,
            trip_ids,
        )

        fares = _audit_fares(
            archive,
        )

        finished_at = datetime.now(timezone.utc)

    return {
        "audit_version": "c0-v1",
        "provider_id": provider_id,
        "zip_path": str(path),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "archive": {
            "required_files": sorted(
                REQUIRED_FILES
            ),
            "missing_required_files": (
                missing_required_files
            ),
            "duplicate_zip_members": (
                duplicate_zip_members
            ),
            "file_count": len(names),
        },
        "rows": table_rows,
        "bbox": bbox,
        "stops": stops,
        "calendars": calendars,
        "trips": trips,
        "stop_times": stop_times,
        "fares": fares,
        "interpretation": {
            "times_are_measured_only": True,
            "extended_gtfs_times_are_not_rejected": True,
            "database_ingestion_not_performed": True,
            "redis_not_used": True,
            "router_not_used": True,
        },
    }


def write_audit_json(
    report: dict[str, Any],
    output_path: str | Path,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _flatten_for_csv(
    value: Any,
    prefix: str = "",
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []

    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = (
                f"{prefix}.{key}"
                if prefix
                else str(key)
            )

            rows.extend(
                _flatten_for_csv(
                    child,
                    child_prefix,
                )
            )

    elif isinstance(value, list):
        rows.append(
            (
                prefix,
                json.dumps(
                    value,
                    ensure_ascii=False,
                ),
            )
        )

    else:
        rows.append(
            (
                prefix,
                "" if value is None else str(value),
            )
        )

    return rows


def write_audit_csv(
    report: dict[str, Any],
    output_path: str | Path,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    rows = _flatten_for_csv(report)

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.writer(handle)

        writer.writerow(
            [
                "metric",
                "value",
            ]
        )

        writer.writerows(rows)


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the streaming C0 GTFS data-quality audit."
        )
    )

    parser.add_argument(
        "--provider",
        required=True,
        help="Provider identifier, e.g. tgsrtc or hmrl.",
    )

    parser.add_argument(
        "--zip",
        required=True,
        dest="zip_path",
        help="Path to the GTFS ZIP.",
    )

    parser.add_argument(
        "--json",
        default=None,
        dest="json_path",
        help="Optional output JSON path.",
    )

    parser.add_argument(
        "--csv",
        default=None,
        dest="csv_path",
        help="Optional output CSV path.",
    )

    return parser


def main() -> None:
    parser = _build_cli()
    args = parser.parse_args()

    report = audit_gtfs_zip(
        args.zip_path,
        provider_id=args.provider,
    )

    if args.json_path:
        write_audit_json(
            report,
            args.json_path,
        )

    if args.csv_path:
        write_audit_csv(
            report,
            args.csv_path,
        )

    print(
        json.dumps(
            report,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()