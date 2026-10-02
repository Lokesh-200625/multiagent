from typing import Any

from pydantic import BaseModel, Field

from app.models.resolved_query import ResolvedEntity


class TripState(BaseModel):
    session_id: str

    user_query: str = ""

    intent: str = "unknown"

    intent_confidence: float = 0.0

    entities: dict[str, Any] = Field(
        default_factory=dict
    )

    resolved_entities: list[ResolvedEntity] = Field(
        default_factory=list
    )

    unresolved_entities: list[str] = Field(
        default_factory=list
    )

    constraints: dict[str, Any] = Field(
        default_factory=dict
    )

    preferences: dict[str, Any] = Field(
        default_factory=dict
    )

    dates_times: list[dict[str, Any]] = Field(
        default_factory=list
    )

    locations: list[dict[str, Any]] = Field(
        default_factory=list
    )

    group: dict[str, Any] | None = None

    required_capabilities: list[str] = Field(
        default_factory=list
    )

    understanding: dict[str, Any] = Field(
        default_factory=dict
    )

    resolved_query: dict[str, Any] = Field(
        default_factory=dict
    )

    execution_plan: dict[str, Any] = Field(
        default_factory=dict
    )

    temple: dict[str, Any] | None = None

    transport: dict[str, Any] | None = None

    hotels: list[dict[str, Any]] = Field(
        default_factory=list
    )

    places: list[dict[str, Any]] = Field(
        default_factory=list
    )

    evidence: list[dict[str, Any]] = Field(
        default_factory=list
    )

    itinerary: list[dict[str, Any]] = Field(
        default_factory=list
    )

    validation: dict[str, Any] | None = None

    last_agent: str | None = None

    replan_count: int = 0