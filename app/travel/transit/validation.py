
from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection


TABLE_COUNTS = {
    "agency": "transit_agencies",
    "calendar": "transit_calendar",
    "stops": "transit_stops",
    "routes": "transit_routes",
    "trips": "transit_trips",
    "stop_times": "transit_stop_times",
    "fare_attributes": "transit_fares",
    "fare_rules": "transit_fare_rules",
}


ORPHAN_CHECKS = {
    "routes_without_agency": """
        SELECT count(*)
        FROM public.transit_routes r
        LEFT JOIN public.transit_agencies a
          ON a.feed_id = r.feed_id
         AND a.agency_id = r.agency_id
        WHERE r.feed_id = %s
          AND r.agency_id IS NOT NULL
          AND a.agency_id IS NULL
    """,
    "trips_without_route": """
        SELECT count(*)
        FROM public.transit_trips t
        LEFT JOIN public.transit_routes r
          ON r.feed_id = t.feed_id
         AND r.route_id = t.route_id
        WHERE t.feed_id = %s
          AND r.route_id IS NULL
    """,
    "trips_without_calendar": """
        SELECT count(*)
        FROM public.transit_trips t
        LEFT JOIN public.transit_calendar c
          ON c.feed_id = t.feed_id
         AND c.service_id = t.service_id
        WHERE t.feed_id = %s
          AND c.service_id IS NULL
    """,
    "stop_times_without_trip": """
        SELECT count(*)
        FROM public.transit_stop_times st
        LEFT JOIN public.transit_trips t
          ON t.feed_id = st.feed_id
         AND t.trip_id = st.trip_id
        WHERE st.feed_id = %s
          AND t.trip_id IS NULL
    """,
    "stop_times_without_stop": """
        SELECT count(*)
        FROM public.transit_stop_times st
        LEFT JOIN public.transit_stops s
          ON s.feed_id = st.feed_id
         AND s.stop_id = st.stop_id
        WHERE st.feed_id = %s
          AND s.stop_id IS NULL
    """,
    "fare_rules_without_fare": """
        SELECT count(*)
        FROM public.transit_fare_rules fr
        LEFT JOIN public.transit_fares f
          ON f.feed_id = fr.feed_id
         AND f.fare_id = fr.fare_id
        WHERE fr.feed_id = %s
          AND f.fare_id IS NULL
    """,
}


def validate_feed(
    connection: Connection,
    feed_id: UUID,
) -> dict[str, Any]:
    """Validate a feed without modifying its status."""
    feed = connection.execute(
        """
        SELECT provider_id, status, row_counts, content_sha256
        FROM public.transit_feeds
        WHERE id = %s
        """,
        (feed_id,),
    ).fetchone()

    if feed is None:
        raise ValueError(f"Transit feed not found: {feed_id}")

    provider_id, status, expected_counts, content_sha256 = feed
    errors: list[str] = []
    actual_counts: dict[str, int] = {}
    count_mismatches: dict[str, dict[str, int]] = {}

    if status not in {"STAGING", "LOADED", "ACTIVE"}:
        errors.append(f"Feed status is not eligible for validation: {status}")

    if not content_sha256 or len(content_sha256) != 64:
        errors.append("Missing or invalid SHA-256 feed hash")

    if not isinstance(expected_counts, dict):
        errors.append("row_counts metadata is not a JSON object")
        expected_counts = {}

    for count_key, table_name in TABLE_COUNTS.items():
        actual = int(
            connection.execute(
                f"""
                SELECT count(*)
                FROM public.{table_name}
                WHERE feed_id = %s
                """,
                (feed_id,),
            ).fetchone()[0]
        )
        actual_counts[count_key] = actual

        if count_key not in expected_counts:
            errors.append(f"Missing row_counts metadata for {count_key}")
            continue

        try:
            expected = int(expected_counts[count_key])
        except (TypeError, ValueError):
            errors.append(f"Invalid expected row count for {count_key}")
            continue

        if expected != actual:
            count_mismatches[count_key] = {
                "expected": expected,
                "actual": actual,
            }

    if count_mismatches:
        errors.append("Stored row counts do not match database counts")

    for table in (
        "agency", "calendar", "stops", "routes", "trips", "stop_times"
    ):
        if actual_counts.get(table, 0) == 0:
            errors.append(f"Required table is empty: {table}")

    orphan_counts: dict[str, int] = {}

    for check_name, sql in ORPHAN_CHECKS.items():
        count = int(
            connection.execute(sql, (feed_id,)).fetchone()[0]
        )
        orphan_counts[check_name] = count

        if count:
            errors.append(f"{check_name}: {count} orphan rows")

    quarantine_count = int(
        connection.execute(
            """
            SELECT count(*)
            FROM public.transit_quarantine
            WHERE feed_id = %s
            """,
            (feed_id,),
        ).fetchone()[0]
    )

    if quarantine_count:
        errors.append(
            f"Feed contains {quarantine_count} quarantined rows"
        )

    return {
        "feed_id": str(feed_id),
        "provider_id": provider_id,
        "status": status,
        "passed": not errors,
        "errors": errors,
        "expected_counts": expected_counts,
        "actual_counts": actual_counts,
        "count_mismatches": count_mismatches,
        "orphan_counts": orphan_counts,
        "quarantine_count": quarantine_count,
    }
