from typing import Any

from typing_extensions import TypedDict


class GraphState(TypedDict, total=False):
    session_id: str

    execution_plan: dict[str, Any]

    current_step_index: int

    current_step_id: str | None

    current_step: dict[str, Any] | None

    step_results: dict[str, Any]

    dependency_results: dict[str, Any]

    status: str

    error: str | None

    clarification_required: bool

    clarification_question: str | None