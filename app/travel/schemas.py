from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


TravelStatus = Literal[
    "COMPLETED",
    "PARTIAL",
    "FAILED",
    "NEEDS_CLARIFICATION",
]


class Coordinate(BaseModel):
    latitude: float
    longitude: float


class PlaceCandidate(BaseModel):
    display_name: str
    latitude: float
    longitude: float
    source: str
    place_type: str | None = None
    importance: float | None = None


class ResolvedPlace(BaseModel):
    query: str
    display_name: str
    coordinates: Coordinate
    source: str
    canonical_entity_id: str | None = None
    canonical_entity_type: str | None = None
    confidence: float | None = None


class TravelFact(BaseModel):
    key: str
    value: Any
    unit: str | None = None

    provider: str
    source_tier: str

    fetched_at: datetime

    confidence: float | None = None
    configurable: bool = False

    approximate: bool = False
    calculation_method: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderError(BaseModel):
    provider: str
    operation: str
    error_type: str
    message: str
    retryable: bool = False


class TravelEvidence(BaseModel):
    fact_key: str
    provider: str
    source_tier: str
    fetched_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TravelPlanStep(BaseModel):
    step_id: str
    question: str
    intent: str

    capabilities: list[str] = Field(default_factory=list)

    origin: Any | None = None
    destination: Any | None = None

    mode: str = "driving"

    constraints: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)

    dependency_results: dict[str, Any] = Field(default_factory=dict)


class TravelResult(BaseModel):
    status: TravelStatus

    facts: list[TravelFact] = Field(default_factory=list)
    evidence: list[TravelEvidence] = Field(default_factory=list)

    errors: list[ProviderError] = Field(default_factory=list)

    clarification_question: str | None = None
    candidates: list[PlaceCandidate] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)