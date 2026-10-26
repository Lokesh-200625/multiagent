from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )


class PlanEntity(StrictModel):
    entity_id: str | None = None
    entity_type: str
    canonical_name: str | None = None
    mention: str


class PlanStep(StrictModel):
    step_id: str
    domain: str
    intent: str

    question: str

    entities: list[PlanEntity] = Field(
        default_factory=list
    )

    constraints: list[dict] = Field(
        default_factory=list
    )

    preferences: list[dict] = Field(
        default_factory=list
    )

    dates_times: list[dict] = Field(
        default_factory=list
    )

    locations: list[dict] = Field(
        default_factory=list
    )

    required_capabilities: list[str] = Field(
        default_factory=list
    )

    depends_on: list[str] = Field(
        default_factory=list
    )

    status: str = "READY"


class ExecutionPlan(StrictModel):
    intent: str

    steps: list[PlanStep] = Field(
        default_factory=list
    )

    requires_clarification: bool = False

    clarification_question: str | None = None

    status: str = "READY"