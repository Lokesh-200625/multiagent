from app.models.resolved_query import (
    ResolvedEntity,
    ResolvedEntityCandidate,
    ResolvedQuery,
    ResolvedSubQuery,
)
from app.models.understanding import (
    QueryEntity,
    QueryUnderstanding,
)
from app.services.entity_registry import EntityRegistry


registry = EntityRegistry()


REFERENCE_ENTITY_TYPES = {
    "accommodation": {
        "accommodation",
        "hotel",
        "lodging",
    },
    "restaurant": {
        "restaurant",
        "food",
        "dining",
        "place",
    },
    "temple": {
        "temple",
        "place",
    },
    "travel": {
        "location",
        "place",
    },
}


def _normalize_candidates(
    candidates,
    entity_type: str,
) -> list[ResolvedEntityCandidate]:

    normalized = []

    for candidate in candidates:

        if isinstance(candidate, str):
            normalized.append(
                ResolvedEntityCandidate(
                    entity_id=candidate,
                    entity_type=entity_type,
                    canonical_name="",
                )
            )
            continue

        normalized.append(
            ResolvedEntityCandidate(
                entity_id=candidate["entity_id"],
                entity_type=candidate["entity_type"],
                canonical_name=candidate["canonical_name"],
            )
        )

    return normalized


def _domain_matches_entity_type(
    domain: str,
    entity_type: str,
) -> bool:

    domain = domain.lower().strip()
    entity_type = entity_type.lower().strip()

    allowed_types = (
        REFERENCE_ENTITY_TYPES.get(
            domain,
            set(),
        )
    )

    return (
        entity_type in allowed_types
        or domain == entity_type
    )


def _find_dependency_reference(
    entity: QueryEntity,
    dependencies: list[str],
    previous_steps: list[dict],
) -> str | None:

    if not dependencies:
        return None

    for dependency_id in dependencies:

        for step in previous_steps:

            if step.get("step_id") != dependency_id:
                continue

            domain = step.get(
                "domain",
                "",
            )

            if _domain_matches_entity_type(
                domain=domain,
                entity_type=entity.entity_type,
            ):
                return dependency_id

    return None


def _resolve_entity(
    entity: QueryEntity,
    context: dict | None = None,
) -> ResolvedEntity:

    context = context or {}

    reference_step_id = context.get(
        "reference_step_id"
    )

    if reference_step_id:

        return ResolvedEntity(
            mention=entity.mention,
            entity_type=entity.entity_type,
            canonical_id=None,
            canonical_name=None,
            status="RESOLVED_REFERENCE",
            confidence=1.0,
            match_type="dependency_reference",
            reference_step_id=reference_step_id,
            candidates=[],
        )

    result = registry.resolve(
        mention=entity.mention,
        entity_type=entity.entity_type,
        context=context,
    )

    resolved_entity = result.get(
        "entity"
    ) or {}

    canonical_id = result.get(
        "entity_id"
    )

    canonical_name = resolved_entity.get(
        "canonical_name"
    )

    status = result.get(
        "status",
        "UNRESOLVED",
    )

    match_type = result.get(
        "match_type"
    )

    confidence = result.get(
        "match_score",
        0.0,
    )

    # IMPORTANT:
    #
    # The LLM entity_type describes the user's semantic mention.
    # Once deterministic resolution succeeds, downstream planning
    # must use the canonical registry entity type.
    #
    # Example:
    #
    #   user mention: Ramadasu
    #   LLM type:     person
    #   registry:     T0002
    #   canonical:    temple
    #
    # Therefore the resolved entity becomes:
    #
    #   entity_type = temple
    #
    # This allows LangGraph to route the step to temple_agent.
    resolved_entity_type = entity.entity_type

    if (
        status == "RESOLVED"
        and resolved_entity
        and resolved_entity.get("entity_type")
    ):
        resolved_entity_type = str(
            resolved_entity["entity_type"]
        )

    return ResolvedEntity(
        mention=entity.mention,
        entity_type=resolved_entity_type,
        canonical_id=canonical_id,
        canonical_name=canonical_name,
        status=status,
        confidence=confidence,
        match_type=match_type,
        candidates=_normalize_candidates(
            result.get(
                "candidates",
                [],
            ),
            resolved_entity_type,
        ),
    )


def _build_context(
    understanding: QueryUnderstanding,
    session_context: dict | None = None,
) -> dict:

    context = {
        "entities": [
            {
                "mention": entity.mention,
                "entity_type": entity.entity_type,
                "role": entity.role,
            }
            for entity in understanding.entities
        ],
        "locations": [
            location.model_dump()
            for location in understanding.locations
        ],
    }

    if session_context:
        context.update(
            session_context
        )

    return context


def resolve_understanding(
    understanding: QueryUnderstanding,
    session_context: dict | None = None,
) -> ResolvedQuery:

    session_context = (
        session_context or {}
    )

    base_context = _build_context(
        understanding,
        session_context,
    )

    previous_steps = session_context.get(
        "previous_steps",
        [],
    )

    resolved_entities = []
    unresolved_mentions = []
    ambiguous_mentions = []

    for entity in understanding.entities:

        resolved = _resolve_entity(
            entity,
            base_context,
        )

        resolved_entities.append(
            resolved
        )

        if resolved.status == "UNRESOLVED":
            unresolved_mentions.append(
                resolved.mention
            )

        elif resolved.status == "AMBIGUOUS":
            ambiguous_mentions.append(
                resolved.mention
            )

    resolved_subqueries = []

    for subquery in understanding.subqueries:

        subquery_entities = []
        subquery_unresolved = []
        subquery_ambiguous = []

        for entity in subquery.entities:

            dependency_reference = (
                _find_dependency_reference(
                    entity=entity,
                    dependencies=(
                        subquery.dependencies
                    ),
                    previous_steps=previous_steps,
                )
            )

            if dependency_reference:

                resolved = _resolve_entity(
                    entity,
                    {
                        **base_context,
                        "reference_step_id": (
                            dependency_reference
                        ),
                    },
                )

            else:

                resolved = _resolve_entity(
                    entity,
                    base_context,
                )

            subquery_entities.append(
                resolved
            )

            if resolved.status == "UNRESOLVED":
                subquery_unresolved.append(
                    resolved.mention
                )

            elif resolved.status == "AMBIGUOUS":
                subquery_ambiguous.append(
                    resolved.mention
                )

        unresolved_mentions.extend(
            subquery_unresolved
        )

        ambiguous_mentions.extend(
            subquery_ambiguous
        )

        resolved_subqueries.append(
            ResolvedSubQuery(
                query_id=subquery.query_id,
                domain=subquery.domain,
                intent=subquery.intent,
                question=subquery.question,
                entities=subquery_entities,
                constraints=[
                    item.model_dump()
                    for item in subquery.constraints
                ],
                preferences=[
                    item.model_dump()
                    for item in subquery.preferences
                ],
                dates_times=[
                    item.model_dump()
                    for item in subquery.dates_times
                ],
                locations=[
                    item.model_dump()
                    for item in subquery.locations
                ],
                required_capabilities=(
                    subquery.required_capabilities
                ),
                dependencies=subquery.dependencies,
            )
        )

    unresolved_mentions = list(
        dict.fromkeys(
            unresolved_mentions
        )
    )

    ambiguous_mentions = list(
        dict.fromkeys(
            ambiguous_mentions
        )
    )

    requires_clarification = bool(
        unresolved_mentions
        or ambiguous_mentions
        or understanding.requires_clarification
    )

    clarification_question = (
        understanding.clarification_question
    )

    if not clarification_question:

        if ambiguous_mentions:

            clarification_question = (
                "I need clarification for: "
                + ", ".join(
                    ambiguous_mentions
                )
            )

        elif unresolved_mentions:

            clarification_question = (
                "I need more information about: "
                + ", ".join(
                    unresolved_mentions
                )
            )

    return ResolvedQuery(
        understanding=understanding,
        entities=resolved_entities,
        subqueries=resolved_subqueries,
        unresolved_mentions=unresolved_mentions,
        ambiguous_mentions=ambiguous_mentions,
        requires_clarification=(
            requires_clarification
        ),
        clarification_question=(
            clarification_question
        ),
    )