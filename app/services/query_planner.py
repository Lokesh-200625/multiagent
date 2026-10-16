
from app.models.plan import (
    ExecutionPlan,
    PlanEntity,
    PlanStep,
)
from app.models.resolved_query import (
    ResolvedQuery,
)


SUPPORTED_EXECUTION_DOMAINS = {
    "temple",
    "accommodation",
    "restaurant",
    "travel",
}


def _entity_to_plan_entity(entity) -> PlanEntity:
    return PlanEntity(
        entity_id=entity.canonical_id,
        entity_type=entity.entity_type,
        canonical_name=entity.canonical_name,
        mention=entity.mention,
    )


def _resolve_execution_domain(
    domain: str,
    entities,
    intent: str = "",
    required_capabilities=None,
) -> str:
    normalized_domain = str(domain or "").strip().lower()
    normalized_intent = str(intent or "").strip().lower()

    capabilities = {
        str(capability).strip().lower()
        for capability in (required_capabilities or [])
    }

    # Route intent/capabilities take priority over entity types.
    if (
        normalized_intent in {
            "route_search",
            "distance_calculation",
        }
        or "route_search" in capabilities
        or "distance_calculation" in capabilities
    ):
        return "travel"

    # Preserve a supported domain for non-route queries.
    if normalized_domain in SUPPORTED_EXECUTION_DOMAINS:
        return normalized_domain

    resolved_types = {
        str(entity.entity_type).strip().lower()
        for entity in entities
        if (
            entity.status == "RESOLVED"
            and entity.canonical_id
            and entity.entity_type
        )
    }

    if len(resolved_types) == 1:
        resolved_type = next(iter(resolved_types))
        if resolved_type in SUPPORTED_EXECUTION_DOMAINS:
            return resolved_type

    return normalized_domain


def _entity_blocks_step(
    entity,
    domain: str | None = None,
) -> bool:
    normalized_domain = str(domain or "").strip().lower()

    # Travel locations are resolved by the travel-specific resolver.
    # An unresolved registry match alone should not block travel.
    if normalized_domain == "travel":
        return entity.status == "AMBIGUOUS"

    return entity.status in {
        "AMBIGUOUS",
        "UNRESOLVED",
    }


def build_execution_plan(
    resolved_query: ResolvedQuery,
) -> ExecutionPlan:
    steps: list[PlanStep] = []
    blocked_mentions: list[str] = []

    for subquery in resolved_query.subqueries:
        # Resolve the executable domain BEFORE checking entity status.
        execution_domain = _resolve_execution_domain(
            domain=subquery.domain,
            entities=subquery.entities,
            intent=subquery.intent,
            required_capabilities=subquery.required_capabilities,
        )

        step_status = "READY"
        plan_entities = []

        for entity in subquery.entities:
            plan_entities.append(
                _entity_to_plan_entity(entity)
            )

            if _entity_blocks_step(entity, execution_domain):
                step_status = "BLOCKED"
                blocked_mentions.append(entity.mention)

        steps.append(
            PlanStep(
                step_id=subquery.query_id,
                domain=execution_domain,
                intent=subquery.intent,
                question=subquery.question,
                entities=plan_entities,
                constraints=subquery.constraints,
                preferences=subquery.preferences,
                dates_times=subquery.dates_times,
                locations=subquery.locations,
                required_capabilities=(
                    subquery.required_capabilities
                ),
                depends_on=subquery.dependencies,
                status=step_status,
            )
        )

    if blocked_mentions:
        return ExecutionPlan(
            intent=resolved_query.understanding.intent,
            steps=steps,
            requires_clarification=True,
            clarification_question=(
                "I need clarification for: "
                + ", ".join(dict.fromkeys(blocked_mentions))
            ),
            status="BLOCKED",
        )

    return ExecutionPlan(
        intent=resolved_query.understanding.intent,
        steps=steps,
        requires_clarification=False,
        clarification_question=None,
        status="READY",
    )


def validate_execution_plan(
    execution_plan: dict,
) -> dict:
    steps = execution_plan.get("steps", [])

    known_ids = {
        step.get("step_id")
        for step in steps
        if step.get("step_id")
    }

    validation_errors = []

    for step in steps:
        step_id = step.get("step_id")

        for dependency in step.get("depends_on", []):
            if dependency not in known_ids:
                step["status"] = "BLOCKED"
                validation_errors.append(
                    f"{step_id} depends on unknown step {dependency}"
                )

    blocked_steps = [
        step for step in steps
        if step.get("status") == "BLOCKED"
    ]

    if validation_errors or blocked_steps:
        execution_plan["requires_clarification"] = True

        if validation_errors:
            question = (
                "I need more information about: "
                + "; ".join(dict.fromkeys(validation_errors))
            )
        else:
            blocked_mentions = list(dict.fromkeys(
                entity.get("mention", "")
                for step in blocked_steps
                for entity in step.get("entities", [])
                if entity.get("mention")
            ))

            question = (
                "I need clarification for: "
                + ", ".join(blocked_mentions)
                if blocked_mentions
                else "A required execution step is blocked."
            )

        execution_plan["clarification_question"] = question
        execution_plan["status"] = "BLOCKED"
        return execution_plan

    for step in steps:
        step["status"] = "READY"

    execution_plan["requires_clarification"] = False
    execution_plan["clarification_question"] = None
    execution_plan["status"] = "READY"

    return execution_plan
