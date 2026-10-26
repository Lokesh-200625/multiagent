from pydantic import BaseModel, ConfigDict, Field

from app.models.understanding import QueryUnderstanding


class ResolvedEntityCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    entity_type: str
    canonical_name: str


class ResolvedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention: str
    entity_type: str

    canonical_id: str | None = None
    canonical_name: str | None = None

    status: str

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    match_type: str | None = None

    reference_step_id: str | None = None

    candidates: list[ResolvedEntityCandidate] = Field(
        default_factory=list,
    )


class ResolvedSubQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    domain: str
    intent: str
    question: str

    entities: list[ResolvedEntity] = Field(
        default_factory=list,
    )

    constraints: list[dict] = Field(
        default_factory=list,
    )

    preferences: list[dict] = Field(
        default_factory=list,
    )

    dates_times: list[dict] = Field(
        default_factory=list,
    )

    locations: list[dict] = Field(
        default_factory=list,
    )

    required_capabilities: list[str] = Field(
        default_factory=list,
    )

    dependencies: list[str] = Field(
        default_factory=list,
    )


class ResolvedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    understanding: QueryUnderstanding

    entities: list[ResolvedEntity] = Field(
        default_factory=list,
    )

    subqueries: list[ResolvedSubQuery] = Field(
        default_factory=list,
    )

    unresolved_mentions: list[str] = Field(
        default_factory=list,
    )

    ambiguous_mentions: list[str] = Field(
        default_factory=list,
    )

    requires_clarification: bool = False

    clarification_question: str | None = None