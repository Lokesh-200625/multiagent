
from __future__ import annotations

from math import cos, isfinite, radians
from typing import Any

from app.travel.transit.db import get_connection


def _rows(cursor: Any) -> list[dict[str, Any]]:
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _one(cursor: Any) -> dict[str, Any] | None:
    columns = [column.name for column in cursor.description]
    row = cursor.fetchone()
    return dict(zip(columns, row)) if row is not None else None


def _limit(
    value: int,
    default: int = 10,
    maximum: int = 100,
) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default

    return max(1, min(value, maximum))


def get_active_feed(
    provider_id: str,
) -> dict[str, Any] | None:
    """Return metadata for the active provider feed."""
    with get_connection() as connection:
        cursor = connection.execute(
            """
            SELECT
                id,
                provider_id,
                provider_name,
                source_url,
                feed_version,
                valid_from,
                valid_until,
                fetched_at,
                activated_at,
                content_sha256,
                row_counts
            FROM public.transit_feeds
            WHERE provider_id = %s
              AND status = 'ACTIVE'
            LIMIT 1
            """,
            (provider_id.strip().lower(),),
        )
        return _one(cursor)


def search_stops(
    provider_id: str,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search stops by name, code, or ID."""
    query = query.strip()

    if not query:
        return []

    limit = _limit(limit)
    pattern = f"%{query}%"

    with get_connection() as connection:
        cursor = connection.execute(
            """
            SELECT
                s.stop_id,
                s.stop_code,
                s.stop_name,
                s.stop_desc,
                s.stop_lat AS latitude,
                s.stop_lon AS longitude,
                s.zone_id,
                s.location_type,
                s.parent_station,
                f.provider_id
            FROM public.transit_stops s
            JOIN public.transit_feeds f
              ON f.id = s.feed_id
            WHERE f.provider_id = %s
              AND f.status = 'ACTIVE'
              AND (
                  s.stop_name ILIKE %s
                  OR s.stop_code ILIKE %s
                  OR s.stop_id ILIKE %s
              )
            ORDER BY
                CASE
                    WHEN lower(s.stop_name) = lower(%s)
                    THEN 0
                    ELSE 1
                END,
                s.stop_name,
                s.stop_id
            LIMIT %s
            """,
            (
                provider_id.strip().lower(),
                pattern,
                pattern,
                pattern,
                query,
                limit,
            ),
        )
        return _rows(cursor)


def nearby_stops(
    provider_id: str,
    latitude: float,
    longitude: float,
    radius_m: int = 1000,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Find nearby stops and return their distance in meters."""
    latitude = float(latitude)
    longitude = float(longitude)

    if not isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90")

    if not isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180")

    radius_m = max(1, min(int(radius_m), 50000))
    limit = _limit(limit)

    longitude_delta = radius_m / (
        111000 * max(0.01, abs(cos(radians(latitude))))
    )

    with get_connection() as connection:
        cursor = connection.execute(
            """
            WITH candidates AS (
                SELECT
                    s.stop_id,
                    s.stop_code,
                    s.stop_name,
                    s.stop_lat AS latitude,
                    s.stop_lon AS longitude,
                    s.zone_id,
                    s.location_type,
                    s.parent_station,
                    6371000.0 * 2 * asin(
                        sqrt(
                            least(
                                1.0,
                                power(
                                    sin(
                                        radians(s.stop_lat - %s) / 2
                                    ),
                                    2
                                )
                                + cos(radians(%s))
                                * cos(radians(s.stop_lat))
                                * power(
                                    sin(
                                        radians(s.stop_lon - %s) / 2
                                    ),
                                    2
                                )
                            )
                        )
                    ) AS distance_m,
                    f.provider_id
                FROM public.transit_stops s
                JOIN public.transit_feeds f
                  ON f.id = s.feed_id
                WHERE f.provider_id = %s
                  AND f.status = 'ACTIVE'
                  AND s.stop_lat IS NOT NULL
                  AND s.stop_lon IS NOT NULL
                  AND s.stop_lat BETWEEN %s AND %s
                  AND s.stop_lon BETWEEN %s AND %s
            )
            SELECT *
            FROM candidates
            WHERE distance_m <= %s
            ORDER BY distance_m
            LIMIT %s
            """,
            (
                latitude,
                latitude,
                longitude,
                provider_id.strip().lower(),
                max(-90, latitude - radius_m / 111000),
                min(90, latitude + radius_m / 111000),
                max(-180, longitude - longitude_delta),
                min(180, longitude + longitude_delta),
                radius_m,
                limit,
            ),
        )
        return _rows(cursor)


def search_routes(
    provider_id: str,
    query: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search routes by short name, long name, or route ID."""
    provider_id = provider_id.strip().lower()
    query = query.strip()
    limit = _limit(limit)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            SELECT
                r.route_id,
                r.agency_id,
                r.route_short_name,
                r.route_long_name,
                r.route_desc,
                r.route_type,
                r.mode,
                r.route_color,
                r.route_text_color,
                f.provider_id
            FROM public.transit_routes r
            JOIN public.transit_feeds f
              ON f.id = r.feed_id
            WHERE f.provider_id = %s
              AND f.status = 'ACTIVE'
              AND (
                  %s = ''
                  OR r.route_short_name ILIKE %s
                  OR r.route_long_name ILIKE %s
                  OR r.route_id ILIKE %s
              )
            ORDER BY
                r.route_short_name NULLS LAST,
                r.route_long_name NULLS LAST
            LIMIT %s
            """,
            (
                provider_id,
                query,
                f"%{query}%",
                f"%{query}%",
                f"%{query}%",
                limit,
            ),
        )
        return _rows(cursor)


def get_route_stops(
    provider_id: str,
    route_id: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Return the ordered stops for one representative trip on a route.

    A route can contain trips in opposite directions. Selecting one trip
    prevents stops from unrelated trips being mixed together.
    """
    limit = _limit(limit, default=100, maximum=2000)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            WITH selected_feed AS (
                SELECT id
                FROM public.transit_feeds
                WHERE provider_id = %s
                  AND status = 'ACTIVE'
                LIMIT 1
            ),
            selected_trip AS (
                SELECT
                    t.feed_id,
                    t.trip_id,
                    count(st.stop_id) AS stop_count
                FROM selected_feed f
                JOIN public.transit_trips t
                  ON t.feed_id = f.id
                JOIN public.transit_stop_times st
                  ON st.feed_id = t.feed_id
                 AND st.trip_id = t.trip_id
                WHERE t.route_id = %s
                GROUP BY t.feed_id, t.trip_id
                ORDER BY stop_count DESC, t.trip_id
                LIMIT 1
            )
            SELECT
                st.trip_id,
                st.stop_sequence,
                st.stop_id,
                s.stop_name,
                s.stop_lat AS latitude,
                s.stop_lon AS longitude,
                st.arrival_seconds,
                st.departure_seconds,
                t.route_id,
                r.route_short_name,
                r.route_long_name
            FROM selected_trip chosen
            JOIN public.transit_stop_times st
              ON st.feed_id = chosen.feed_id
             AND st.trip_id = chosen.trip_id
            JOIN public.transit_stops s
              ON s.feed_id = st.feed_id
             AND s.stop_id = st.stop_id
            JOIN public.transit_trips t
              ON t.feed_id = st.feed_id
             AND t.trip_id = st.trip_id
            JOIN public.transit_routes r
              ON r.feed_id = t.feed_id
             AND r.route_id = t.route_id
            ORDER BY st.stop_sequence
            LIMIT %s
            """,
            (
                provider_id.strip().lower(),
                route_id.strip(),
                limit,
            ),
        )
        return _rows(cursor)


def get_stop_departures(
    provider_id: str,
    stop_id: str,
    after_seconds: int = 0,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Return scheduled departures from a stop.

    If stop_id identifies a parent station, include its child stops.
    Times are service-day seconds, not necessarily today's live departures.
    """
    after_seconds = max(0, int(after_seconds))
    limit = _limit(limit, default=20)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            WITH selected_feed AS (
                SELECT id
                FROM public.transit_feeds
                WHERE provider_id = %s
                  AND status = 'ACTIVE'
                LIMIT 1
            ),
            target_stops AS (
                SELECT s.stop_id
                FROM selected_feed f
                JOIN public.transit_stops requested
                  ON requested.feed_id = f.id
                JOIN public.transit_stops s
                  ON s.feed_id = requested.feed_id
                 AND (
                     s.stop_id = requested.stop_id
                     OR s.parent_station = requested.stop_id
                 )
                WHERE requested.stop_id = %s
            )
            SELECT
                st.stop_id,
                s.stop_name,
                st.trip_id,
                st.stop_sequence,
                st.arrival_seconds,
                st.departure_seconds,
                t.route_id,
                t.trip_headsign,
                t.service_id,
                r.route_short_name,
                r.route_long_name,
                r.mode,
                f.provider_id
            FROM selected_feed selected
            JOIN public.transit_feeds f
              ON f.id = selected.id
            JOIN public.transit_stop_times st
              ON st.feed_id = f.id
            JOIN target_stops target
              ON target.stop_id = st.stop_id
            JOIN public.transit_stops s
              ON s.feed_id = st.feed_id
             AND s.stop_id = st.stop_id
            JOIN public.transit_trips t
              ON t.feed_id = st.feed_id
             AND t.trip_id = st.trip_id
            JOIN public.transit_routes r
              ON r.feed_id = t.feed_id
             AND r.route_id = t.route_id
            WHERE COALESCE(
                st.departure_seconds,
                st.arrival_seconds
            ) >= %s
            ORDER BY
                COALESCE(
                    st.departure_seconds,
                    st.arrival_seconds
                ),
                st.trip_id,
                st.stop_sequence
            LIMIT %s
            """,
            (
                provider_id.strip().lower(),
                stop_id.strip(),
                after_seconds,
                limit,
            ),
        )
        return _rows(cursor)
