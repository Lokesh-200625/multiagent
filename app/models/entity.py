from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )


class EntityMetadata(StrictModel):
    source: str | None = None
    language: str | None = None
    alias_match: bool = False


class ResolvedEntity(StrictModel):
    mention: str
    entity_type: str
    canonical_name: str | None = None
    canonical_id: str | None = None

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    status: str

    metadata: EntityMetadata = Field(
        default_factory=EntityMetadata
    )


class EntityResolutionResult(StrictModel):
    entities: list[ResolvedEntity] = Field(
        default_factory=list
    )

    unresolved_mentions: list[str] = Field(
        default_factory=list
    )

    needs_clarification: bool = False

    clarification_question: str | None = None