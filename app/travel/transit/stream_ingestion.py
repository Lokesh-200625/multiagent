from __future__ import annotations

import csv

import hashlib

import io

import json

import zipfile

from datetime import date

from pathlib import Path

from uuid import UUID

from psycopg import Connection

from app.travel.transit.db import get_connection

from app.travel.transit.providers import get_provider
from app.travel.transit.validation import validate_feed

REQUIRED_FILES = {

    "agency.txt",

    "calendar.txt",

    "stops.txt",

    "routes.txt",

    "trips.txt",

    "stop_times.txt",

}

def _text(value: str | None) -> str | None:

    value = (value or "").strip()

    return value or None

def _int(value: str | None) -> int | None:

    value = _text(value)

    if value is None:

        return None

    return int(value)

def _float(value: str | None) -> float | None:

    value = _text(value)

    if value is None:

        return None

    return float(value)

def _date(value: str | None) -> date | None:

    value = _text(value)

    if value is None:

        return None

    if len(value) != 8 or not value.isdigit():

        raise ValueError(f"Invalid GTFS date: {value}")

    return date(

        int(value[:4]),

        int(value[4:6]),

        int(value[6:8]),

    )

def _gtfs_time_seconds(value: str | None) -> int | None:

    value = _text(value)

    if value is None:

        return None

    parts = value.split(":")

    if len(parts) != 3:

        raise ValueError(f"Invalid GTFS time: {value}")

    hour, minute, second = map(int, parts)

    if hour < 0:

        raise ValueError(f"Invalid GTFS hour: {value}")

    if not 0 <= minute <= 59:

        raise ValueError(f"Invalid GTFS minute: {value}")

    if not 0 <= second <= 59:

        raise ValueError(f"Invalid GTFS second: {value}")

    return hour * 3600 + minute * 60 + second

def _route_mode(route_type: int) -> str:

    if route_type == 0:

        return "tram"

    if route_type == 1:

        return "metro"

    if route_type == 2:

        return "rail"

    if route_type == 3:

        return "bus"

    if route_type == 4:

        return "ferry"

    return "unknown"

def _sha256(path: Path) -> str:

    digest = hashlib.sha256()

    with path.open("rb") as file:

        while chunk := file.read(1024 * 1024):

            digest.update(chunk)

    return digest.hexdigest()

def _zip_rows(

    archive: zipfile.ZipFile,

    filename: str,

):

    with archive.open(filename, "r") as file:

        text = io.TextIOWrapper(

            file,

            encoding="utf-8-sig",

            newline="",

        )

        try:

            reader = csv.DictReader(text)

            for row_number, row in enumerate(reader, start=2):

                yield row_number, row

        finally:

            text.detach()

def _quarantine(

    connection: Connection,

    feed_id: UUID,

    table_name: str,

    row_number: int,

    reason_code: str,

    reason_detail: str,

    raw_row: dict[str, str | None],

) -> None:

    connection.execute(

        """

        INSERT INTO public.transit_quarantine (

            feed_id,

            table_name,

            row_number,

            reason_code,

            reason_detail,

            raw_row

        )

        VALUES (%s, %s, %s, %s, %s, %s::jsonb)

        """,

        (

            feed_id,

            table_name,

            row_number,

            reason_code,

            reason_detail,

            json.dumps(raw_row),

        ),

    )

def _copy_rows(

    connection: Connection,

    sql: str,

    rows: list[tuple],

) -> None:

    if not rows:

        return

    buffer = io.StringIO()

    writer = csv.writer(buffer)

    for row in rows:

        writer.writerow(row)

    with connection.cursor() as cursor:

        with cursor.copy(sql) as copy:

            copy.write(buffer.getvalue())

def _load_agencies(

    connection: Connection,

    archive: zipfile.ZipFile,

    feed_id: UUID,

) -> tuple[int, set[str]]:

    rows = []

    agency_ids: set[str] = set()

    count = 0

    for row_number, row in _zip_rows(archive, "agency.txt"):

        try:

            agency_id = _text(row.get("agency_id"))

            agency_name = _text(row.get("agency_name"))

            if not agency_id:

                raise ValueError("agency_id is required")

            if not agency_name:

                raise ValueError("agency_name is required")

            if agency_id in agency_ids:

                raise ValueError("duplicate agency_id")

            agency_ids.add(agency_id)

            rows.append(

                (

                    feed_id,

                    agency_id,

                    agency_name,

                    _text(row.get("agency_url")),

                    _text(row.get("agency_timezone")),

                    _text(row.get("agency_lang")),

                    _text(row.get("agency_phone")),

                    _text(row.get("agency_fare_url")),

                )

            )

            count += 1

        except Exception as exc:

            _quarantine(

                connection,

                feed_id,

                "agency",

                row_number,

                "INVALID_ROW",

                str(exc),

                row,

            )

    _copy_rows(

        connection,

        """

        COPY public.transit_agencies (

            feed_id,

            agency_id,

            agency_name,

            agency_url,

            agency_timezone,

            agency_lang,

            agency_phone,

            agency_fare_url

        )

        FROM STDIN WITH (

            FORMAT CSV,

            NULL ''

        )

        """,

        rows,

    )

    return count, agency_ids

def _load_calendar(

    connection: Connection,

    archive: zipfile.ZipFile,

    feed_id: UUID,

) -> tuple[int, set[str]]:

    rows = []

    service_ids: set[str] = set()

    count = 0

    for row_number, row in _zip_rows(archive, "calendar.txt"):

        try:

            service_id = _text(row.get("service_id"))

            if not service_id:

                raise ValueError("service_id is required")

            if service_id in service_ids:

                raise ValueError("duplicate service_id")

            start_date = _date(row.get("start_date"))

            end_date = _date(row.get("end_date"))

            if start_date is None or end_date is None:

                raise ValueError(

                    "start_date and end_date are required"

                )

            if end_date < start_date:

                raise ValueError("end_date before start_date")

            service_ids.add(service_id)

            rows.append(

                (

                    feed_id,

                    service_id,

                    _text(row.get("monday")) == "1",

                    _text(row.get("tuesday")) == "1",

                    _text(row.get("wednesday")) == "1",

                    _text(row.get("thursday")) == "1",

                    _text(row.get("friday")) == "1",

                    _text(row.get("saturday")) == "1",

                    _text(row.get("sunday")) == "1",

                    start_date,

                    end_date,

                )

            )

            count += 1

        except Exception as exc:

            _quarantine(

                connection,

                feed_id,

                "calendar",

                row_number,

                "INVALID_ROW",

                str(exc),

                row,

            )

    _copy_rows(

        connection,

        """

        COPY public.transit_calendar (

            feed_id,

            service_id,

            monday,

            tuesday,

            wednesday,

            thursday,

            friday,

            saturday,

            sunday,

            start_date,

            end_date

        )

        FROM STDIN WITH (

            FORMAT CSV,

            NULL ''

        )

        """,

        rows,

    )

    return count, service_ids

def _load_stops(

    connection: Connection,

    archive: zipfile.ZipFile,

    feed_id: UUID,

) -> tuple[int, set[str]]:

    rows = []

    stop_ids: set[str] = set()

    count = 0

    for row_number, row in _zip_rows(archive, "stops.txt"):

        try:

            stop_id = _text(row.get("stop_id"))

            stop_name = _text(row.get("stop_name"))

            if not stop_id:

                raise ValueError("stop_id is required")

            if not stop_name:

                raise ValueError("stop_name is required")

            if stop_id in stop_ids:

                raise ValueError("duplicate stop_id")

            latitude = _float(row.get("stop_lat"))

            longitude = _float(row.get("stop_lon"))

            if latitude is not None and not -90 <= latitude <= 90:

                raise ValueError("invalid latitude")

            if longitude is not None and not -180 <= longitude <= 180:

                raise ValueError("invalid longitude")

            location_type = _int(row.get("location_type"))

            wheelchair = _int(row.get("wheelchair_boarding"))

            if location_type is not None and not 0 <= location_type <= 4:

                raise ValueError("invalid location_type")

            if wheelchair is not None and not 0 <= wheelchair <= 2:

                raise ValueError(

                    "invalid wheelchair_boarding"

                )

            stop_ids.add(stop_id)

            rows.append(

                (

                    feed_id,

                    stop_id,

                    _text(row.get("stop_code")),

                    stop_name,

                    _text(row.get("stop_desc")),

                    latitude,

                    longitude,

                    _text(row.get("zone_id")),

                    _text(row.get("stop_url")),

                    location_type,

                    _text(row.get("parent_station")),

                    wheelchair,

                )

            )

            count += 1

        except Exception as exc:

            _quarantine(

                connection,

                feed_id,

                "stops",

                row_number,

                "INVALID_ROW",

                str(exc),

                row,

            )

    _copy_rows(

        connection,

        """

        COPY public.transit_stops (

            feed_id,

            stop_id,

            stop_code,

            stop_name,

            stop_desc,

            stop_lat,

            stop_lon,

            zone_id,

            stop_url,

            location_type,

            parent_station,

            wheelchair_boarding

        )

        FROM STDIN WITH (

            FORMAT CSV,

            NULL ''

        )

        """,

        rows,

    )

    return count, stop_ids

def _load_routes(

    connection: Connection,

    archive: zipfile.ZipFile,

    feed_id: UUID,

    agency_ids: set[str],

) -> tuple[int, set[str]]:

    rows = []

    route_ids: set[str] = set()

    count = 0

    for row_number, row in _zip_rows(archive, "routes.txt"):

        try:

            route_id = _text(row.get("route_id"))

            if not route_id:

                raise ValueError("route_id is required")

            if route_id in route_ids:

                raise ValueError("duplicate route_id")

            route_type = _int(row.get("route_type"))

            if route_type is None:

                raise ValueError("route_type is required")

            agency_id = _text(row.get("agency_id"))

            if agency_id and agency_id not in agency_ids:

                raise ValueError("unknown agency_id")

            route_ids.add(route_id)

            rows.append(

                (

                    feed_id,

                    route_id,

                    agency_id,

                    _text(row.get("route_short_name")),

                    _text(row.get("route_long_name")),

                    _text(row.get("route_desc")),

                    route_type,

                    _route_mode(route_type),

                    _text(row.get("route_url")),

                    _text(row.get("route_color")),

                    _text(row.get("route_text_color")),

                )

            )

            count += 1

        except Exception as exc:

            _quarantine(

                connection,

                feed_id,

                "routes",

                row_number,

                "INVALID_ROW",

                str(exc),

                row,

            )

    _copy_rows(

        connection,

        """

        COPY public.transit_routes (

            feed_id,

            route_id,

            agency_id,

            route_short_name,

            route_long_name,

            route_desc,

            route_type,

            mode,

            route_url,

            route_color,

            route_text_color

        )

        FROM STDIN WITH (

            FORMAT CSV,

            NULL ''

        )

        """,

        rows,

    )

    return count, route_ids

def _load_trips(

    connection: Connection,

    archive: zipfile.ZipFile,

    feed_id: UUID,

    route_ids: set[str],

    service_ids: set[str],

) -> tuple[int, set[str]]:

    rows = []

    trip_ids: set[str] = set()

    count = 0

    for row_number, row in _zip_rows(archive, "trips.txt"):

        try:

            trip_id = _text(row.get("trip_id"))

            route_id = _text(row.get("route_id"))

            service_id = _text(row.get("service_id"))

            if not trip_id:

                raise ValueError("trip_id is required")

            if route_id not in route_ids:

                raise ValueError("unknown route_id")

            if service_id not in service_ids:

                raise ValueError("unknown service_id")

            if trip_id in trip_ids:

                raise ValueError("duplicate trip_id")

            direction_id = _int(row.get("direction_id"))

            if direction_id is not None and direction_id not in {0, 1}:

                raise ValueError("invalid direction_id")

            trip_ids.add(trip_id)

            rows.append(

                (

                    feed_id,

                    trip_id,

                    route_id,

                    service_id,

                    _text(row.get("trip_headsign")),

                    _text(row.get("trip_short_name")),

                    direction_id,

                    _text(row.get("block_id")),

                    _text(row.get("shape_id")),

                    _int(row.get("wheelchair_accessible")),

                    _int(row.get("bikes_allowed")),

                )

            )

            count += 1

        except Exception as exc:

            _quarantine(

                connection,

                feed_id,

                "trips",

                row_number,

                "INVALID_ROW",

                str(exc),

                row,

            )

    _copy_rows(

        connection,

        """

        COPY public.transit_trips (

            feed_id,

            trip_id,

            route_id,

            service_id,

            trip_headsign,

            trip_short_name,

            direction_id,

            block_id,

            shape_id,

            wheelchair_accessible,

            bikes_allowed

        )

        FROM STDIN WITH (

            FORMAT CSV,

            NULL ''

        )

        """,

        rows,

    )

    return count, trip_ids

def _load_stop_times(

    connection: Connection,

    archive: zipfile.ZipFile,

    feed_id: UUID,

    trip_ids: set[str],

    stop_ids: set[str],

) -> int:

    rows = []

    seen: set[tuple[str, int]] = set()

    count = 0

    copy_sql = """

        COPY public.transit_stop_times (

            feed_id,

            trip_id,

            stop_sequence,

            stop_id,

            arrival_seconds,

            departure_seconds,

            stop_headsign,

            pickup_type,

            drop_off_type,

            continuous_pickup,

            continuous_drop_off,

            timepoint

        )

        FROM STDIN WITH (

            FORMAT CSV,

            NULL ''

        )

    """

    for row_number, row in _zip_rows(

        archive,

        "stop_times.txt",

    ):

        try:

            trip_id = _text(row.get("trip_id"))

            stop_id = _text(row.get("stop_id"))

            stop_sequence = _int(row.get("stop_sequence"))

            if trip_id not in trip_ids:

                raise ValueError("unknown trip_id")

            if stop_id not in stop_ids:

                raise ValueError("unknown stop_id")

            if stop_sequence is None or stop_sequence < 0:

                raise ValueError("invalid stop_sequence")

            identity = (trip_id, stop_sequence)

            if identity in seen:

                raise ValueError(

                    "duplicate trip_id + stop_sequence"

                )

            arrival_seconds = _gtfs_time_seconds(

                row.get("arrival_time")

            )

            departure_seconds = _gtfs_time_seconds(

                row.get("departure_time")

            )

            seen.add(identity)

            rows.append(

                (

                    feed_id,

                    trip_id,

                    stop_sequence,

                    stop_id,

                    arrival_seconds,

                    departure_seconds,

                    _text(row.get("stop_headsign")),

                    _int(row.get("pickup_type")),

                    _int(row.get("drop_off_type")),

                    _int(row.get("continuous_pickup")),

                    _int(row.get("continuous_drop_off")),

                    _int(row.get("timepoint")),

                )

            )

            count += 1

            if len(rows) >= 10_000:

                _copy_rows(

                    connection,

                    copy_sql,

                    rows,

                )

                rows.clear()

        except Exception as exc:

            _quarantine(

                connection,

                feed_id,

                "stop_times",

                row_number,

                "INVALID_ROW",

                str(exc),

                row,

            )

    _copy_rows(

        connection,

        copy_sql,

        rows,

    )

    return count

def _load_fares(

    connection: Connection,

    archive: zipfile.ZipFile,

    feed_id: UUID,

) -> tuple[int, set[str]]:

    if "fare_attributes.txt" not in archive.namelist():

        return 0, set()

    rows = []

    fare_ids: set[str] = set()

    count = 0

    for row_number, row in _zip_rows(

        archive,

        "fare_attributes.txt",

    ):

        try:

            fare_id = _text(row.get("fare_id"))

            price_text = _text(row.get("price"))

            currency = _text(row.get("currency_type"))

            if not fare_id:

                raise ValueError("fare_id is required")

            if fare_id in fare_ids:

                raise ValueError("duplicate fare_id")

            if price_text is None:

                raise ValueError("price is required")

            price = float(price_text)

            if price < 0:

                raise ValueError("negative fare price")

            if not currency or len(currency) != 3:

                raise ValueError("invalid currency_type")

            fare_ids.add(fare_id)

            rows.append(

                (

                    feed_id,

                    fare_id,

                    price,

                    currency.upper(),

                    _int(row.get("payment_method")),

                    _int(row.get("transfers")),

                    _text(row.get("agency_id")),

                    _int(row.get("transfer_duration")),

                )

            )

            count += 1

        except Exception as exc:

            _quarantine(

                connection,

                feed_id,

                "fare_attributes",

                row_number,

                "INVALID_ROW",

                str(exc),

                row,

            )

    _copy_rows(

        connection,

        """

        COPY public.transit_fares (

            feed_id,

            fare_id,

            price,

            currency_type,

            payment_method,

            transfers,

            agency_id,

            transfer_duration

        )

        FROM STDIN WITH (

            FORMAT CSV,

            NULL ''

        )

        """,

        rows,

    )

    return count, fare_ids

def _load_fare_rules(

    connection: Connection,

    archive: zipfile.ZipFile,

    feed_id: UUID,

    fare_ids: set[str],

) -> int:

    if "fare_rules.txt" not in archive.namelist():

        return 0

    rows = []

    seen: set[tuple] = set()

    count = 0

    for row_number, row in _zip_rows(

        archive,

        "fare_rules.txt",

    ):

        try:

            fare_id = _text(row.get("fare_id"))

            if fare_id not in fare_ids:

                raise ValueError("unknown fare_id")

            route_id = _text(row.get("route_id"))

            origin_id = _text(row.get("origin_id"))

            destination_id = _text(

                row.get("destination_id")

            )

            contains_id = _text(row.get("contains_id"))

            identity = (

                fare_id,

                route_id,

                origin_id,

                destination_id,

                contains_id,

            )

            if identity in seen:

                raise ValueError("duplicate fare rule")

            seen.add(identity)

            rows.append(

                (

                    feed_id,

                    fare_id,

                    route_id,

                    origin_id,

                    destination_id,

                    contains_id,

                )

            )

            count += 1

        except Exception as exc:

            _quarantine(

                connection,

                feed_id,

                "fare_rules",

                row_number,

                "INVALID_ROW",

                str(exc),

                row,

            )

    _copy_rows(

        connection,

        """

        COPY public.transit_fare_rules (

            feed_id,

            fare_id,

            route_id,

            origin_id,

            destination_id,

            contains_id

        )

        FROM STDIN WITH (

            FORMAT CSV,

            NULL ''

        )

        """,

        rows,

    )

    return count

def _load_feed_info(

    connection: Connection,

    archive: zipfile.ZipFile,

    feed_id: UUID,

) -> None:

    if "feed_info.txt" not in archive.namelist():

        return

    rows = list(

        _zip_rows(

            archive,

            "feed_info.txt",

        )

    )

    if not rows:

        return

    _, row = rows[0]

    start_date = _date(row.get("feed_start_date"))

    end_date = _date(row.get("feed_end_date"))

    connection.execute(

        """

        UPDATE public.transit_feeds

        SET

            publisher_name = %s,

            publisher_url = %s,

            feed_lang = %s,

            default_lang = %s,

            contact_email = %s,

            contact_url = %s,

            feed_version = %s,

            valid_from = %s,

            valid_until = %s

        WHERE id = %s

        """,

        (

            _text(row.get("feed_publisher_name")),

            _text(row.get("feed_publisher_url")),

            _text(row.get("feed_lang")),

            _text(row.get("default_lang")),

            _text(row.get("feed_contact_email")),

            _text(row.get("feed_contact_url")),

            _text(row.get("feed_version")),

            start_date,

            end_date,

            feed_id,

        ),

    )

def ingest_gtfs_file(

    provider_id: str,

    file_path: str,

) -> UUID:

    provider = get_provider(provider_id)

    path = Path(file_path)

    if not path.exists():

        raise FileNotFoundError(path)

    content_sha256 = _sha256(path)

    with zipfile.ZipFile(path, "r") as archive:

        filenames = set(archive.namelist())

        missing = REQUIRED_FILES - filenames

        if missing:

            raise ValueError(

                f"Missing required GTFS files: {sorted(missing)}"

            )

        with get_connection() as connection:

            existing = connection.execute(

                """

                SELECT id

                FROM public.transit_feeds

                WHERE provider_id = %s

                  AND content_sha256 = %s

                """,

                (

                    provider.provider_id,

                    content_sha256,

                ),

            ).fetchone()

            if existing:

                return existing[0]

            feed_id = connection.execute(

                """

                INSERT INTO public.transit_feeds (

                    provider_id,

                    provider_name,

                    source_url,

                    content_sha256,

                    status

                )

                VALUES (

                    %s,

                    %s,

                    %s,

                    %s,

                    'STAGING'

                )

                RETURNING id

                """,

                (

                    provider.provider_id,

                    provider.name,

                    provider.source_url,

                    content_sha256,

                ),

            ).fetchone()[0]

            _load_feed_info(

                connection,

                archive,

                feed_id,

            )

            agency_count, agency_ids = _load_agencies(

                connection,

                archive,

                feed_id,

            )

            calendar_count, service_ids = _load_calendar(

                connection,

                archive,

                feed_id,

            )

            stop_count, stop_ids = _load_stops(

                connection,

                archive,

                feed_id,

            )

            route_count, route_ids = _load_routes(

                connection,

                archive,

                feed_id,

                agency_ids,

            )

            trip_count, trip_ids = _load_trips(

                connection,

                archive,

                feed_id,

                route_ids,

                service_ids,

            )

            stop_time_count = _load_stop_times(

                connection,

                archive,

                feed_id,

                trip_ids,

                stop_ids,

            )

            fare_count, fare_ids = _load_fares(

                connection,

                archive,

                feed_id,

            )

            fare_rule_count = _load_fare_rules(

                connection,

                archive,

                feed_id,

                fare_ids,

            )

            row_counts = {

                "agency": agency_count,

                "calendar": calendar_count,

                "stops": stop_count,

                "routes": route_count,

                "trips": trip_count,

                "stop_times": stop_time_count,

                "fare_attributes": fare_count,

                "fare_rules": fare_rule_count,

            }

            connection.execute(

                """

                UPDATE public.transit_feeds

                SET

                    row_counts = %s::jsonb,

                    status = 'LOADED'

                WHERE id = %s

                """,

                (

                    json.dumps(row_counts),

                    feed_id,

                ),

            )

            # Validate the new feed before touching the current ACTIVE feed.
            # Any failure raises inside the transaction, rolling back this load
            # and preserving the previously active feed.
            validation = validate_feed(connection, feed_id)
            if not validation["passed"]:
                raise RuntimeError(
                    "Transit feed validation failed: "
                    + "; ".join(validation["errors"])
                )

            # Demote the currently active feed only after validation passes.

            connection.execute(

                """

                UPDATE public.transit_feeds

                SET status = 'LOADED'

                WHERE provider_id = %s

                  AND status = 'ACTIVE'

                  AND id <> %s

                """,

                (

                    provider.provider_id,

                    feed_id,

                ),

            )

            # Activate the newly loaded feed.

            connection.execute(

                """

                UPDATE public.transit_feeds

                SET

                    status = 'ACTIVE',

                    activated_at = now()

                WHERE id = %s

                """,

                (feed_id,),

            )

            return feed_id