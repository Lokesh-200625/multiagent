from app.models.plan import (
    ExecutionPlan,
    PlanEntity,
    PlanStep,
)
from app.models.resolved_query import (
    ResolvedQuery,
)


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

        steps.append(
            PlanStep(
                step_id=subquery.query_id,
                domain=subquery.domain,
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

    # IMPORTANT:
    #
    # Do NOT validate dependencies here.
    #
    # A dependency may belong to a previous session turn.
    # Dependency validation happens after session plans are merged.

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

    # Only preserve genuine entity blocks.
    #
    # Dependency validation succeeded, therefore steps
    # which are not explicitly blocked can execute.

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