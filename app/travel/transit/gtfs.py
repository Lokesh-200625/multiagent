from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone

from app.travel.transit.schemas import (
    TransitAgency,
    TransitCalendar,
    TransitDataset,
    TransitFare,
    TransitFareRule,
    TransitFeedInfo,
    TransitFeedMetadata,
    TransitRoute,
    TransitShapePoint,
    TransitStop,
    TransitStopTime,
    TransitTrip,
)


def _read_table(
    archive: zipfile.ZipFile,
    filename: str,
) -> list[dict[str, str]]:
    try:
        raw = archive.read(filename)
    except KeyError:
        return []

    return list(
        csv.DictReader(
            io.StringIO(raw.decode("utf-8-sig"))
        )
    )


def _int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _bool(value: str | None) -> bool:
    return value == "1"


def _route_type_to_mode(route_type: int | None) -> str:
    if route_type == 0:
        return "tram"
    if route_type == 1:
        return "metro"
    if route_type == 2:
        return "rail"
    if route_type == 3:
        return "bus"
    if route_type in {4, 5}:
        return "ferry"
    return "unknown"


def parse_gtfs(
    data: bytes,
    provider_id: str,
    source_url: str,
    attribution: str | None = None,
) -> TransitDataset:
    fetched_at = datetime.now(timezone.utc)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        agency_rows = _read_table(archive, "agency.txt")
        feed_info_rows = _read_table(archive, "feed_info.txt")
        stop_rows = _read_table(archive, "stops.txt")
        route_rows = _read_table(archive, "routes.txt")
        calendar_rows = _read_table(archive, "calendar.txt")
        trip_rows = _read_table(archive, "trips.txt")
        stop_time_rows = _read_table(archive, "stop_times.txt")
        shape_rows = _read_table(archive, "shapes.txt")
        fare_rows = _read_table(archive, "fare_attributes.txt")
        fare_rule_rows = _read_table(archive, "fare_rules.txt")

    agencies = [
        TransitAgency(
            agency_id=row.get("agency_id", provider_id),
            name=row.get("agency_name", provider_id),
            source_url=row.get("agency_url"),
            timezone=row.get("agency_timezone"),
            language=row.get("agency_lang"),
            fare_url=row.get("agency_fare_url"),
            email=row.get("agency_email"),
            phone=row.get("agency_phone"),
        )
        for row in agency_rows
    ]

    feed_info = None

    if feed_info_rows:
        row = feed_info_rows[0]

        feed_info = TransitFeedInfo(
            publisher_name=row.get("feed_publisher_name"),
            publisher_url=row.get("feed_publisher_url"),
            contact_email=row.get("feed_contact_email"),
            contact_url=row.get("feed_contact_url"),
            language=row.get("feed_lang"),
            start_date=row.get("feed_start_date"),
            end_date=row.get("feed_end_date"),
            version=row.get("feed_version"),
        )

    stops = [
        TransitStop(
            stop_id=row["stop_id"],
            name=row.get("stop_name", row["stop_id"]),
            latitude=float(row["stop_lat"]),
            longitude=float(row["stop_lon"]),
            agency_id=provider_id,
            zone_id=row.get("zone_id") or None,
            parent_station_id=row.get("parent_station") or None,
            platform_code=row.get("platform_code") or None,
            location_type=int(row.get("location_type") or 0),
            metadata={
                key: value
                for key, value in row.items()
                if key not in {
                    "stop_id",
                    "stop_name",
                    "stop_lat",
                    "stop_lon",
                    "zone_id",
                    "parent_station",
                    "platform_code",
                    "location_type",
                }
                and value
            },
        )
        for row in stop_rows
    ]

    routes = [
        TransitRoute(
            route_id=row["route_id"],
            agency_id=row.get("agency_id") or provider_id,
            short_name=row.get("route_short_name") or None,
            long_name=row.get("route_long_name") or None,
            mode=_route_type_to_mode(
                _int(row.get("route_type"))
            ),
            color=row.get("route_color") or None,
            text_color=row.get("route_text_color") or None,
            sort_order=_int(row.get("route_sort_order")),
            metadata={
                key: value
                for key, value in row.items()
                if key not in {
                    "route_id",
                    "agency_id",
                    "route_short_name",
                    "route_long_name",
                    "route_type",
                    "route_color",
                    "route_text_color",
                    "route_sort_order",
                }
                and value
            },
        )
        for row in route_rows
    ]

    calendars = [
        TransitCalendar(
            service_id=row["service_id"],
            monday=_bool(row.get("monday")),
            tuesday=_bool(row.get("tuesday")),
            wednesday=_bool(row.get("wednesday")),
            thursday=_bool(row.get("thursday")),
            friday=_bool(row.get("friday")),
            saturday=_bool(row.get("saturday")),
            sunday=_bool(row.get("sunday")),
            start_date=row["start_date"],
            end_date=row["end_date"],
        )
        for row in calendar_rows
    ]

    trips = [
        TransitTrip(
            trip_id=row["trip_id"],
            route_id=row["route_id"],
            service_id=row["service_id"],
            trip_headsign=row.get("trip_headsign") or None,
            trip_short_name=row.get("trip_short_name") or None,
            direction_id=_int(row.get("direction_id")),
            block_id=row.get("block_id") or None,
            shape_id=row.get("shape_id") or None,
            metadata={
                key: value
                for key, value in row.items()
                if key not in {
                    "trip_id",
                    "route_id",
                    "service_id",
                    "trip_headsign",
                    "trip_short_name",
                    "direction_id",
                    "block_id",
                    "shape_id",
                }
                and value
            },
        )
        for row in trip_rows
    ]

    stop_times = [
        TransitStopTime(
            trip_id=row["trip_id"],
            stop_id=row["stop_id"],
            arrival_time=row["arrival_time"],
            departure_time=row["departure_time"],
            stop_sequence=int(row["stop_sequence"]),
            pickup_type=int(row.get("pickup_type") or 0),
            drop_off_type=int(row.get("drop_off_type") or 0),
            timepoint=_int(row.get("timepoint")),
            shape_dist_traveled=_float(
                row.get("shape_dist_traveled")
            ),
        )
        for row in stop_time_rows
    ]

    shapes = [
        TransitShapePoint(
            shape_id=row["shape_id"],
            latitude=float(row["shape_pt_lat"]),
            longitude=float(row["shape_pt_lon"]),
            sequence=int(row["shape_pt_sequence"]),
            distance_traveled=_float(
                row.get("shape_dist_traveled")
            ),
        )
        for row in shape_rows
    ]

    fares = [
        TransitFare(
            fare_id=row["fare_id"],
            price=float(row["price"]),
            currency=(
                row.get("currency_type")
                or row.get("currency")
                or "UNKNOWN"
            ),
            payment_method=_int(row.get("payment_method")),
            transfers=_int(row.get("transfers")),
            agency_id=row.get("agency_id") or None,
            metadata={
                key: value
                for key, value in row.items()
                if key not in {
                    "fare_id",
                    "price",
                    "currency_type",
                    "currency",
                    "payment_method",
                    "transfers",
                    "agency_id",
                }
                and value
            },
        )
        for row in fare_rows
    ]

    fare_rules = [
        TransitFareRule(
            fare_id=row["fare_id"],
            origin_id=row.get("origin_id") or None,
            destination_id=row.get("destination_id") or None,
            metadata={
                key: value
                for key, value in row.items()
                if key not in {
                    "fare_id",
                    "origin_id",
                    "destination_id",
                }
                and value
            },
        )
        for row in fare_rule_rows
    ]

    feed_version = (
        feed_info.version
        if feed_info is not None
        else None
    )

    valid_from = (
        feed_info.start_date
        if feed_info is not None
        else None
    )

    valid_until = (
        feed_info.end_date
        if feed_info is not None
        else None
    )

    metadata = TransitFeedMetadata(
        provider_id=provider_id,
        feed_version=feed_version,
        fetched_at=fetched_at,
        valid_from=valid_from,
        valid_until=valid_until,
        source_url=source_url,
        static=True,
        attribution=attribution,
    )

    return TransitDataset(
        metadata=metadata,
        agencies=agencies,
        feed_info=feed_info,
        stops=stops,
        routes=routes,
        calendars=calendars,
        trips=trips,
        stop_times=stop_times,
        shapes=shapes,
        fares=fares,
        fare_rules=fare_rules,
    )