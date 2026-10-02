import uuid

from fastapi import APIRouter, HTTPException

from app.graph.workflow import workflow
from app.models.query import ChatRequest
from app.models.state import TripState
from app.services.intent import understand_query
from app.services.query_planner import (
    build_execution_plan,
    validate_execution_plan,
)
from app.services.query_resolution import (
    resolve_understanding,
)
from app.services.session import (
    get_session,
    save_session,
)


router = APIRouter()


def merge_execution_plans(
    previous_plan: dict,
    current_plan: dict,
) -> dict:
    """
    Merge previous session steps with current-turn steps.

    Current-turn steps replace stale steps with the same ID.
    """

    previous_steps = previous_plan.get(
        "steps",
        [],
    )

    current_steps = current_plan.get(
        "steps",
        [],
    )

    current_by_id = {
        step.get("step_id"): step
        for step in current_steps
        if step.get("step_id")
    }

    merged_steps = []

    for step in previous_steps:

        step_id = step.get(
            "step_id"
        )

        if not step_id:
            continue

        if step_id in current_by_id:
            continue

        merged_steps.append(step)

    merged_steps.extend(
        current_steps
    )

    return {
        "intent": current_plan.get(
            "intent",
            previous_plan.get(
                "intent",
                "unknown",
            ),
        ),
        "steps": merged_steps,
        "requires_clarification": (
            current_plan.get(
                "requires_clarification",
                False,
            )
        ),
        "clarification_question": (
            current_plan.get(
                "clarification_question"
            )
        ),
        "status": (
            current_plan.get(
                "status",
                "READY",
            )
        ),
    }


@router.post("/chat")
def chat(request: ChatRequest):

    if not request.message.strip():

        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty.",
        )

    session_id = (
        request.session_id
        or str(uuid.uuid4())
    )

    # ---------------------------------------------------------
    # 1. LOAD SESSION
    # ---------------------------------------------------------

    existing_state = get_session(
        session_id
    )

    if existing_state:

        state = TripState(
            **existing_state
        )

    else:

        state = TripState(
            session_id=session_id
        )

    previous_state = state.model_dump()

    previous_execution_plan = (
        state.execution_plan
        or {}
    )

    previous_step_results = (
        previous_execution_plan.get(
            "step_results",
            {},
        )
    )

    # ---------------------------------------------------------
    # 2. ONE LLM QUERY UNDERSTANDING CALL
    # ---------------------------------------------------------

    understanding = understand_query(
        message=request.message,
        conversation_state={
            **previous_state,
            "previous_execution_plan": (
                previous_execution_plan
            ),
            "previous_step_results": (
                previous_step_results
            ),
        },
    )

    state.user_query = request.message

    state.intent = understanding.intent

    state.intent_confidence = (
        understanding.confidence
    )

    state.understanding = (
        understanding.model_dump()
    )

    state.entities = {
        entity.mention: entity.model_dump()
        for entity in understanding.entities
    }

    state.constraints = {
        constraint.name: constraint.model_dump()
        for constraint in understanding.constraints
    }

    state.preferences = {
        preference.name: preference.model_dump()
        for preference in understanding.preferences
    }

    state.dates_times = [
        item.model_dump()
        for item in understanding.dates_times
    ]

    state.locations = [
        item.model_dump()
        for item in understanding.locations
    ]

    state.group = (
        understanding.group.model_dump()
        if understanding.group
        else None
    )

    state.required_capabilities = (
        understanding.required_capabilities
    )

    # ---------------------------------------------------------
    # 3. DETERMINISTIC ENTITY RESOLUTION
    # ---------------------------------------------------------

    resolved_query = resolve_understanding(
        understanding=understanding,
        session_context={
            "previous_steps": (
                previous_execution_plan.get(
                    "steps",
                    [],
                )
            ),
        },
    )

    state.resolved_query = (
        resolved_query.model_dump()
    )

    state.resolved_entities = [
        entity
        for entity in resolved_query.entities
        if entity.status == "RESOLVED"
    ]

    state.unresolved_entities = (
        resolved_query.unresolved_mentions
    )

    # ---------------------------------------------------------
    # 4. BUILD CURRENT-TURN PLAN
    # ---------------------------------------------------------

    current_execution_plan = (
        build_execution_plan(
            resolved_query
        )
    )

    current_plan_dict = (
        current_execution_plan.model_dump()
    )

    # ---------------------------------------------------------
    # 5. MERGE SESSION PLAN
    # ---------------------------------------------------------

    if previous_execution_plan.get(
        "steps"
    ):

        execution_plan_dict = (
            merge_execution_plans(
                previous_plan=(
                    previous_execution_plan
                ),
                current_plan=(
                    current_plan_dict
                ),
            )
        )

    else:

        execution_plan_dict = (
            current_plan_dict
        )

    # ---------------------------------------------------------
    # 6. VALIDATE AFTER MERGE
    #
    # THIS IS THE IMPORTANT FIX.
    #
    # Now q2 -> subq1 can see subq1 because both steps
    # exist in the complete execution plan.
    # ---------------------------------------------------------

    execution_plan_dict = (
        validate_execution_plan(
            execution_plan_dict
        )
    )

    # Preserve previous results.

    execution_plan_dict[
        "step_results"
    ] = previous_step_results

    state.execution_plan = (
        execution_plan_dict
    )

    # ---------------------------------------------------------
    # 7. LANGGRAPH
    # ---------------------------------------------------------

    graph_input = {
        "session_id": session_id,

        "execution_plan": (
            execution_plan_dict
        ),

        "current_step_index": 0,

        "current_step_id": None,

        "step_results": (
            previous_step_results
        ),

        "status": "PENDING",

        "error": None,

        "clarification_required": (
            execution_plan_dict.get(
                "requires_clarification",
                False,
            )
        ),

        "clarification_question": (
            execution_plan_dict.get(
                "clarification_question"
            )
        ),
    }

    graph_result = workflow.invoke(
        graph_input
    )

    # ---------------------------------------------------------
    # 8. STORE GRAPH RESULT
    # ---------------------------------------------------------

    state.execution_plan = (
        graph_result.get(
            "execution_plan",
            execution_plan_dict,
        )
    )

    state.execution_plan[
        "step_results"
    ] = graph_result.get(
        "step_results",
        previous_step_results,
    )

    state.last_agent = None

    state.validation = {
        "graph_status": graph_result.get(
            "status"
        ),
        "graph_error": graph_result.get(
            "error"
        ),
    }

    # ---------------------------------------------------------
    # 9. SAVE SESSION
    # ---------------------------------------------------------

    save_session(
        session_id,
        state.model_dump(),
    )

    # ---------------------------------------------------------
    # 10. RESPONSE
    # ---------------------------------------------------------

    return {
        "session_id": session_id,

        "execution_plan": (
            state.execution_plan
        ),

        "graph": graph_result,

        "state": state.model_dump(),
    }