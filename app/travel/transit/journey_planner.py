
from __future__ import annotations

from datetime import date
from typing import Any

from app.travel.transit.db import get_connection


WEEKDAY_COLUMNS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def seconds_to_gtfs_time(seconds: int | None) -> str | None:
    """Convert GTFS service-day seconds to HH:MM:SS."""
    if seconds is None:
        return None
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _validate(
    provider_id: str,
    origin_stop_id: str,
    destination_stop_id: str,
    service_date: date,
    limit: int,
) -> str:
    if not provider_id.strip():
        raise ValueError("provider_id cannot be empty.")
    if not origin_stop_id.strip() or not destination_stop_id.strip():
        raise ValueError("Origin and destination cannot be empty.")
    if origin_stop_id == destination_stop_id:
        raise ValueError("Origin and destination must differ.")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    return WEEKDAY_COLUMNS[service_date.weekday()]


def _journey_result(
    row: tuple[Any, ...],
    service_date: date,
) -> dict[str, Any]:
    return {
        "provider_id": row[0],
        "service_date": service_date.isoformat(),
        "origin_stop_id": row[1],
        "origin_stop_name": row[2],
        "destination_stop_id": row[3],
        "destination_stop_name": row[4],
        "departure_seconds": row[5],
        "departure_time": seconds_to_gtfs_time(row[5]),
        "arrival_seconds": row[6],
        "arrival_time": seconds_to_gtfs_time(row[6]),
        "travel_seconds": row[6] - row[5],
        "travel_minutes": round((row[6] - row[5]) / 60, 1),
        "legs": row[7],
    }


def find_direct_journeys(
    provider_id: str,
    origin_stop_id: str,
    destination_stop_id: str,
    service_date: date,
    after_seconds: int = 0,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Find direct trips, expanding station IDs to child platforms."""
    weekday = _validate(
        provider_id,
        origin_stop_id,
        destination_stop_id,
        service_date,
        limit,
    )

    if after_seconds < 0:
        raise ValueError("after_seconds cannot be negative.")

    sql = f"""
        WITH f AS (
            SELECT id, provider_id, valid_from, valid_until
            FROM public.transit_feeds
            WHERE provider_id = %s
              AND UPPER(status) = 'ACTIVE'
            ORDER BY activated_at DESC NULLS LAST, created_at DESC
            LIMIT 1
        ),
        origins AS (
            SELECT stop_id, stop_name
            FROM public.transit_stops
            WHERE feed_id = (SELECT id FROM f)
              AND (stop_id = %s OR parent_station = %s)
        ),
        destinations AS (
            SELECT stop_id, stop_name
            FROM public.transit_stops
            WHERE feed_id = (SELECT id FROM f)
              AND (stop_id = %s OR parent_station = %s)
        )
        SELECT
            f.provider_id,
            os.stop_id,
            os.stop_name,
            ds.stop_id,
            ds.stop_name,
            ost.departure_seconds,
            dst.arrival_seconds,
            jsonb_build_array(
                jsonb_build_object(
                    'route_id', r.route_id,
                    'route_short_name', r.route_short_name,
                    'route_long_name', r.route_long_name,
                    'mode', r.mode,
                    'trip_id', t.trip_id,
                    'headsign', t.trip_headsign,
                    'board_stop_id', os.stop_id,
                    'board_stop_name', os.stop_name,
                    'alight_stop_id', ds.stop_id,
                    'alight_stop_name', ds.stop_name,
                    'departure_seconds', ost.departure_seconds,
                    'arrival_seconds', dst.arrival_seconds
                )
            ) AS legs
        FROM f
        JOIN public.transit_trips t
          ON t.feed_id = f.id
        JOIN public.transit_calendar c
          ON c.feed_id = t.feed_id
         AND c.service_id = t.service_id
         AND c.{weekday} IS TRUE
         AND %s::date BETWEEN c.start_date AND c.end_date
         AND %s::date BETWEEN f.valid_from AND f.valid_until
        JOIN public.transit_routes r
          ON r.feed_id = t.feed_id
         AND r.route_id = t.route_id
        JOIN public.transit_stop_times ost
          ON ost.feed_id = t.feed_id
         AND ost.trip_id = t.trip_id
        JOIN origins os
          ON os.stop_id = ost.stop_id
        JOIN public.transit_stop_times dst
          ON dst.feed_id = t.feed_id
         AND dst.trip_id = t.trip_id
         AND dst.stop_sequence > ost.stop_sequence
        JOIN destinations ds
          ON ds.stop_id = dst.stop_id
        WHERE ost.departure_seconds >= %s
          AND dst.arrival_seconds >= ost.departure_seconds
        ORDER BY ost.departure_seconds, dst.arrival_seconds
        LIMIT %s
    """

    params = (
        provider_id,
        origin_stop_id,
        origin_stop_id,
        destination_stop_id,
        destination_stop_id,
        service_date,
        service_date,
        after_seconds,
        limit,
    )

    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()

    return [_journey_result(row, service_date) for row in rows]



def find_transfer_journeys(
    provider_id: str,
    origin_stop_id: str,
    destination_stop_id: str,
    service_date: date,
    min_transfer_seconds: int = 300,
    after_seconds: int = 0,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Find one-transfer journeys, keeping the best connection per first train."""
    weekday = _validate(
        provider_id,
        origin_stop_id,
        destination_stop_id,
        service_date,
        limit,
    )

    if min_transfer_seconds < 0 or after_seconds < 0:
        raise ValueError("Time values cannot be negative.")

    sql = f"""
        WITH f AS MATERIALIZED (
            SELECT id, provider_id, valid_from, valid_until
            FROM public.transit_feeds
            WHERE provider_id = %s
              AND UPPER(status) = 'ACTIVE'
            ORDER BY activated_at DESC NULLS LAST, created_at DESC
            LIMIT 1
        ),
        origins AS MATERIALIZED (
            SELECT stop_id, stop_name
            FROM public.transit_stops
            WHERE feed_id = (SELECT id FROM f)
              AND (stop_id = %s OR parent_station = %s)
        ),
        destinations AS MATERIALIZED (
            SELECT stop_id, stop_name
            FROM public.transit_stops
            WHERE feed_id = (SELECT id FROM f)
              AND (stop_id = %s OR parent_station = %s)
        ),
        active_services AS MATERIALIZED (
            SELECT t.trip_id, t.route_id, t.service_id, t.trip_headsign
            FROM f
            JOIN public.transit_trips t ON t.feed_id = f.id
            JOIN public.transit_calendar c
              ON c.feed_id = t.feed_id
             AND c.service_id = t.service_id
             AND c.{weekday} IS TRUE
             AND %s::date BETWEEN c.start_date AND c.end_date
             AND %s::date BETWEEN f.valid_from AND f.valid_until
        ),
        first_legs AS MATERIALIZED (
            SELECT
                t.trip_id,
                t.route_id,
                t.service_id,
                t.trip_headsign,
                r.route_short_name,
                r.route_long_name,
                r.mode,
                os.stop_id AS board_id,
                os.stop_name AS board_name,
                x.stop_id AS transfer_platform,
                x.parent_station AS transfer_station_id,
                x.stop_name AS transfer_platform_name,
                ts.stop_name AS transfer_station_name,
                a.departure_seconds,
                b.arrival_seconds
            FROM active_services t
            JOIN public.transit_routes r
              ON r.feed_id = (SELECT id FROM f)
             AND r.route_id = t.route_id
            JOIN public.transit_stop_times a
              ON a.feed_id = (SELECT id FROM f)
             AND a.trip_id = t.trip_id
            JOIN origins os ON os.stop_id = a.stop_id
            JOIN public.transit_stop_times b
              ON b.feed_id = a.feed_id
             AND b.trip_id = a.trip_id
             AND b.stop_sequence > a.stop_sequence
            JOIN public.transit_stops x
              ON x.feed_id = b.feed_id
             AND x.stop_id = b.stop_id
             AND x.parent_station IS NOT NULL
            JOIN public.transit_stops ts
              ON ts.feed_id = x.feed_id
             AND ts.stop_id = x.parent_station
            WHERE a.departure_seconds >= %s
              AND b.arrival_seconds >= a.departure_seconds
        ),
        transfer_stations AS MATERIALIZED (
            SELECT DISTINCT transfer_station_id
            FROM first_legs
        ),
        second_legs AS MATERIALIZED (
            SELECT
                t.trip_id,
                t.route_id,
                t.service_id,
                t.trip_headsign,
                r.route_short_name,
                r.route_long_name,
                r.mode,
                x.parent_station AS transfer_station_id,
                x.stop_id AS transfer_platform,
                x.stop_name AS transfer_platform_name,
                ds.stop_id AS alight_id,
                ds.stop_name AS alight_name,
                a.departure_seconds,
                b.arrival_seconds
            FROM active_services t
            JOIN public.transit_routes r
              ON r.feed_id = (SELECT id FROM f)
             AND r.route_id = t.route_id
            JOIN public.transit_stop_times a
              ON a.feed_id = (SELECT id FROM f)
             AND a.trip_id = t.trip_id
            JOIN public.transit_stops x
              ON x.feed_id = a.feed_id
             AND x.stop_id = a.stop_id
             AND x.parent_station IS NOT NULL
            JOIN transfer_stations s
              ON s.transfer_station_id = x.parent_station
            JOIN public.transit_stop_times b
              ON b.feed_id = a.feed_id
             AND b.trip_id = a.trip_id
             AND b.stop_sequence > a.stop_sequence
            JOIN destinations ds ON ds.stop_id = b.stop_id
            WHERE b.arrival_seconds >= a.departure_seconds
        ),
        ranked_journeys AS (
            SELECT
                f.provider_id,
                l1.board_id,
                l1.board_name,
                l2.alight_id,
                l2.alight_name,
                l1.departure_seconds,
                l2.arrival_seconds,
                l1.trip_id AS first_trip_id,
                l2.departure_seconds AS second_departure_seconds,
                jsonb_build_array(
                    jsonb_build_object(
                        'route_id', l1.route_id,
                        'route_short_name', l1.route_short_name,
                        'route_long_name', l1.route_long_name,
                        'mode', l1.mode,
                        'trip_id', l1.trip_id,
                        'headsign', l1.trip_headsign,
                        'board_stop_id', l1.board_id,
                        'board_stop_name', l1.board_name,
                        'alight_stop_id', l1.transfer_platform,
                        'alight_stop_name', l1.transfer_platform_name,
                        'transfer_station', l1.transfer_station_name,
                        'departure_seconds', l1.departure_seconds,
                        'arrival_seconds', l1.arrival_seconds
                    ),
                    jsonb_build_object(
                        'route_id', l2.route_id,
                        'route_short_name', l2.route_short_name,
                        'route_long_name', l2.route_long_name,
                        'mode', l2.mode,
                        'trip_id', l2.trip_id,
                        'headsign', l2.trip_headsign,
                        'board_stop_id', l2.transfer_platform,
                        'board_stop_name', l2.transfer_platform_name,
                        'alight_stop_id', l2.alight_id,
                        'alight_stop_name', l2.alight_name,
                        'transfer_station', l1.transfer_station_name,
                        'departure_seconds', l2.departure_seconds,
                        'arrival_seconds', l2.arrival_seconds
                    )
                ) AS legs,
                ROW_NUMBER() OVER (
                    PARTITION BY l1.trip_id
                    ORDER BY
                        l2.arrival_seconds,
                        l2.departure_seconds,
                        l2.trip_id
                ) AS connection_rank
            FROM first_legs l1
            JOIN second_legs l2
              ON l2.transfer_station_id = l1.transfer_station_id
             AND l2.trip_id <> l1.trip_id
             AND l2.departure_seconds >=
                 l1.arrival_seconds + %s
            CROSS JOIN f
        )
        SELECT
            provider_id,
            board_id,
            board_name,
            alight_id,
            alight_name,
            departure_seconds,
            arrival_seconds,
            legs
        FROM ranked_journeys
        WHERE connection_rank = 1
        ORDER BY departure_seconds, arrival_seconds
        LIMIT %s
    """

    params = (
        provider_id,
        origin_stop_id,
        origin_stop_id,
        destination_stop_id,
        destination_stop_id,
        service_date,
        service_date,
        after_seconds,
        min_transfer_seconds,
        limit,
    )

    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()

    return [_journey_result(row, service_date) for row in rows]

