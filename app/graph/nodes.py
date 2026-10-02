from app.graph.state import GraphState


def initialize_graph(
    state: GraphState,
) -> GraphState:

    plan = state.get(
        "execution_plan",
        {},
    )

    steps = plan.get(
        "steps",
        [],
    )

    existing_results = dict(
        state.get(
            "step_results",
            {},
        )
    )

    if not steps:
        return {
            **state,
            "current_step_index": 0,
            "current_step_id": None,
            "current_step": None,
            "dependency_results": {},
            "step_results": existing_results,
            "status": "COMPLETED",
        }

    return {
        **state,
        "current_step_index": 0,
        "current_step_id": None,
        "current_step": None,
        "dependency_results": {},
        "step_results": existing_results,
        "status": "RUNNING",
        "error": None,
        "clarification_required": False,
        "clarification_question": None,
    }


def supervisor(
    state: GraphState,
) -> GraphState:

    plan = state.get(
        "execution_plan",
        {},
    )

    steps = plan.get(
        "steps",
        [],
    )

    results = state.get(
        "step_results",
        {},
    )

    if not steps:
        return {
            **state,
            "current_step_id": None,
            "current_step": None,
            "status": "COMPLETED",
        }

    completed_ids = {
        step_id
        for step_id, result in results.items()
        if result.get("status") in {
            "COMPLETED",
            "STUB",
        }
    }

    for step in steps:

        step_id = step.get(
            "step_id"
        )

        if step_id in completed_ids:
            continue

        if step.get("status") == "BLOCKED":
            continue

        dependencies = step.get(
            "depends_on",
            [],
        )

        if not all(
            dependency in completed_ids
            for dependency in dependencies
        ):
            continue

        dependency_results = {
            dependency: results[dependency]
            for dependency in dependencies
            if dependency in results
        }

        return {
            **state,
            "current_step_id": step_id,
            "current_step": step,
            "dependency_results": dependency_results,
            "status": "RUNNING",
            "clarification_required": False,
            "clarification_question": None,
        }

    remaining_steps = [
        step
        for step in steps
        if step.get("step_id")
        not in completed_ids
    ]

    if not remaining_steps:

        return {
            **state,
            "current_step_id": None,
            "current_step": None,
            "dependency_results": {},
            "status": "COMPLETED",
        }

    return {
        **state,
        "current_step_id": None,
        "current_step": None,
        "status": "BLOCKED",
        "clarification_required": True,
        "clarification_question": (
            "Execution is blocked because "
            "required dependencies have not completed."
        ),
    }


def route_step(
    state: GraphState,
) -> str:

    current_step = state.get(
        "current_step"
    )

    if not current_step:

        if state.get("status") == "COMPLETED":
            return "finish"

        if state.get("status") == "BLOCKED":
            return "blocked"

        return "finish"

    if current_step.get("status") == "BLOCKED":
        return "blocked"

    domain = current_step.get(
        "domain",
        "",
    ).lower()

    if domain == "temple":
        return "temple"

    if domain in {
        "accommodation",
        "hotel",
    }:
        return "accommodation"

    if domain in {
        "restaurant",
        "food",
    }:
        return "restaurant"

    if domain in {
        "travel",
        "transport",
    }:
        return "travel"

    return "unknown"


def temple_agent(
    state: GraphState,
) -> GraphState:

    return _complete_step(
        state,
        "temple",
    )


def accommodation_agent(
    state: GraphState,
) -> GraphState:

    return _complete_step(
        state,
        "accommodation",
    )


def restaurant_agent(
    state: GraphState,
) -> GraphState:

    return _complete_step(
        state,
        "restaurant",
    )


def travel_agent(
    state: GraphState,
) -> GraphState:

    return _complete_step(
        state,
        "travel",
    )


def unknown_agent(
    state: GraphState,
) -> GraphState:

    return {
        **state,
        "status": "ERROR",
        "error": (
            "No agent available for the "
            "current execution-plan domain."
        ),
    }


def _complete_step(
    state: GraphState,
    agent: str,
) -> GraphState:

    current_step = state.get(
        "current_step"
    )

    current_step_id = state.get(
        "current_step_id"
    )

    if not current_step or not current_step_id:

        return {
            **state,
            "status": "ERROR",
            "error": (
                "No current step was selected "
                "by the supervisor."
            ),
        }

    results = dict(
        state.get(
            "step_results",
            {},
        )
    )

    dependency_results = dict(
        state.get(
            "dependency_results",
            {},
        )
    )

    results[current_step_id] = {
        "agent": agent,
        "status": "STUB",
        "message": (
            f"{agent} agent executed "
            f"step {current_step_id}."
        ),
        "step": current_step,
        "dependency_results": dependency_results,
    }

    return {
        **state,
        "step_results": results,
        "status": "RUNNING",
    }


def blocked_node(
    state: GraphState,
) -> GraphState:

    return {
        **state,
        "status": "BLOCKED",
    }


def finish_node(
    state: GraphState,
) -> GraphState:

    return {
        **state,
        "current_step_id": None,
        "current_step": None,
        "dependency_results": {},
        "status": "COMPLETED",
    }