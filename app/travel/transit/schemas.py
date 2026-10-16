from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TransitMode = Literal[
    "bus",
    "metro",
    "rail",
    "tram",
    "ferry",
    "unknown",
]


class TransitAgency(BaseModel):
    agency_id: str
    name: str
    source_url: str | None = None
    timezone: str | None = None
    language: str | None = None
    fare_url: str | None = None
    email: str | None = None
    phone: str | None = None


class TransitFeedInfo(BaseModel):
    publisher_name: str | None = None
    publisher_url: str | None = None
    contact_email: str | None = None
    contact_url: str | None = None
    language: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    version: str | None = None


class TransitCalendar(BaseModel):
    service_id: str
    monday: bool
    tuesday: bool
    wednesday: bool
    thursday: bool
    friday: bool
    saturday: bool
    sunday: bool
    start_date: str
    end_date: str


class TransitStop(BaseModel):
    stop_id: str
    name: str
    latitude: float
    longitude: float
    agency_id: str
    zone_id: str | None = None
    parent_station_id: str | None = None
    platform_code: str | None = None
    location_type: int = 0
    metadata: dict[str, str] = Field(default_factory=dict)


class TransitRoute(BaseModel):
    route_id: str
    agency_id: str
    short_name: str | None = None
    long_name: str | None = None
    mode: TransitMode = "unknown"
    color: str | None = None
    text_color: str | None = None
    sort_order: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class TransitTrip(BaseModel):
    trip_id: str
    route_id: str
    service_id: str
    trip_headsign: str | None = None
    trip_short_name: str | None = None
    direction_id: int | None = None
    block_id: str | None = None
    shape_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class TransitStopTime(BaseModel):
    trip_id: str
    stop_id: str
    arrival_time: str
    departure_time: str
    stop_sequence: int
    pickup_type: int = 0
    drop_off_type: int = 0
    timepoint: int | None = None
    shape_dist_traveled: float | None = None


class TransitShapePoint(BaseModel):
    shape_id: str
    latitude: float
    longitude: float
    sequence: int
    distance_traveled: float | None = None


class TransitFare(BaseModel):
    fare_id: str
    price: float
    currency: str
    payment_method: int | None = None
    transfers: int | None = None
    agency_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class TransitFareRule(BaseModel):
    fare_id: str
    origin_id: str | None = None
    destination_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class TransitFeedMetadata(BaseModel):
    provider_id: str
    feed_version: str | None = None
    fetched_at: datetime
    valid_from: str | None = None
    valid_until: str | None = None
    source_url: str
    static: bool = True
    attribution: str | None = None


class TransitDataset(BaseModel):
    metadata: TransitFeedMetadata

    agencies: list[TransitAgency] = Field(default_factory=list)
    feed_info: TransitFeedInfo | None = None

    stops: list[TransitStop] = Field(default_factory=list)
    routes: list[TransitRoute] = Field(default_factory=list)
    calendars: list[TransitCalendar] = Field(default_factory=list)
    trips: list[TransitTrip] = Field(default_factory=list)
    stop_times: list[TransitStopTime] = Field(default_factory=list)
    shapes: list[TransitShapePoint] = Field(default_factory=list)

    fares: list[TransitFare] = Field(default_factory=list)
    fare_rules: list[TransitFareRule] = Field(default_factory=list)