from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )


class QueryEntity(StrictModel):
    mention: str
    entity_type: str
    role: str | None = None


class QueryConstraint(StrictModel):
    name: str
    value: str
    constraint_type: str
    hard: bool = True


class QueryPreference(StrictModel):
    name: str
    value: str


class QueryDateTime(StrictModel):
    raw: str
    kind: str
    relative: bool = False


class QueryLocation(StrictModel):
    mention: str
    role: str


class QueryGroup(StrictModel):
    adults: int | None = None
    children: int | None = None
    seniors: int | None = None
    count: int | None = None


class SubQuery(StrictModel):
    query_id: str
    domain: str
    intent: str
    question: str

    entities: list[QueryEntity] = Field(
        default_factory=list
    )

    constraints: list[QueryConstraint] = Field(
        default_factory=list
    )

    preferences: list[QueryPreference] = Field(
        default_factory=list
    )

    dates_times: list[QueryDateTime] = Field(
        default_factory=list
    )

    locations: list[QueryLocation] = Field(
        default_factory=list
    )

    group: QueryGroup | None = None

    required_capabilities: list[str] = Field(
        default_factory=list
    )

    dependencies: list[str] = Field(
        default_factory=list
    )


class QueryDependency(StrictModel):
    query_id: str

    depends_on: list[str] = Field(
        default_factory=list
    )

    reason: str | None = None


class QueryUnderstanding(StrictModel):
    intent: str

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    language: str

    requires_clarification: bool = False

    clarification_question: str | None = None

    entities: list[QueryEntity] = Field(
        default_factory=list
    )

    constraints: list[QueryConstraint] = Field(
        default_factory=list
    )

    preferences: list[QueryPreference] = Field(
        default_factory=list
    )

    dates_times: list[QueryDateTime] = Field(
        default_factory=list
    )

    locations: list[QueryLocation] = Field(
        default_factory=list
    )

    group: QueryGroup | None = None

    required_capabilities: list[str] = Field(
        default_factory=list
    )

    subqueries: list[SubQuery] = Field(
        default_factory=list
    )

    dependencies: list[QueryDependency] = Field(
        default_factory=list
    )