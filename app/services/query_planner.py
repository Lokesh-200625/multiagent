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


def _entity_to_plan_entity(
    entity,
) -> PlanEntity:

    return PlanEntity(
        entity_id=entity.canonical_id,
        entity_type=entity.entity_type,
        canonical_name=entity.canonical_name,
        mention=entity.mention,
    )


def _entity_blocks_step(
    entity,
) -> bool:

    return entity.status in {
        "AMBIGUOUS",
        "UNRESOLVED",
    }


def _resolve_execution_domain(
    domain: str,
    entities,
) -> str:

    """
    Convert semantic LLM domains into an executable domain when
    deterministic entity resolution gives us a concrete canonical
    entity type.

    Example:

        LLM:
            domain = person
            entity = Ramadasu / person

        Registry:
            Ramadasu -> T0002 / temple

        Execution:
            domain = temple

    This is necessary because LangGraph routes by executable
    capability/domain, not by the original semantic label.
    """

    normalized_domain = (
        str(domain or "")
        .strip()
        .lower()
    )

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


def build_execution_plan(
    resolved_query: ResolvedQuery,
) -> ExecutionPlan:

    steps: list[PlanStep] = []

    blocked_mentions: list[str] = []

    for subquery in resolved_query.subqueries:

        step_status = "READY"

        plan_entities = []

        for entity in subquery.entities:

            plan_entities.append(
                _entity_to_plan_entity(entity)
            )

            if _entity_blocks_step(entity):

                step_status = "BLOCKED"

                blocked_mentions.append(
                    entity.mention
                )

        execution_domain = _resolve_execution_domain(
            domain=subquery.domain,
            entities=subquery.entities,
        )

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
                + ", ".join(
                    dict.fromkeys(
                        blocked_mentions
                    )
                )
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

    steps = execution_plan.get(
        "steps",
        [],
    )

    known_ids = {
        step.get("step_id")
        for step in steps
        if step.get("step_id")
    }

    unknown_dependencies = []

    for step in steps:

        step_id = step.get(
            "step_id"
        )

        for dependency in step.get(
            "depends_on",
            [],
        ):

            if dependency not in known_ids:

                step["status"] = "BLOCKED"

                unknown_dependencies.append(
                    f"{step_id} depends on "
                    f"unknown step {dependency}"
                )

    if unknown_dependencies:

        execution_plan[
            "requires_clarification"
        ] = True

        execution_plan[
            "clarification_question"
        ] = (
            "I need more information about: "
            + "; ".join(
                dict.fromkeys(
                    unknown_dependencies
                )
            )
        )

        execution_plan[
            "status"
        ] = "BLOCKED"

        return execution_plan

    for step in steps:

        if step.get("status") != "BLOCKED":

            step["status"] = "READY"

    execution_plan[
        "requires_clarification"
    ] = False

    execution_plan[
        "clarification_question"
    ] = None

    execution_plan[
        "status"
    ] = "READY"

    return execution_plan