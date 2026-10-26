from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )


class ChatRequest(StrictModel):
    message: str = Field(min_length=1)
    session_id: str | None = None


class ExtractedEntity(StrictModel):
    name: str
    value: str
    entity_type: str


class ExtractedConstraint(StrictModel):
    name: str
    value: str
    constraint_type: str


class IntentResult(StrictModel):
    intent: str

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    required_capabilities: list[str] = Field(
        default_factory=list
    )

    entities: list[ExtractedEntity] = Field(
        default_factory=list
    )

    constraints: list[ExtractedConstraint] = Field(
        default_factory=list
    )

    needs_clarification: bool = False

    clarification_question: str | None = None