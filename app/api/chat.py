import re
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
from app.services.safety import (
    detect_emergency,
    emergency_response,
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
        step_id = step.get("step_id")

        if not step_id:
            continue

        if step_id in current_by_id:
            continue

        merged_steps.append(step)

    merged_steps.extend(current_steps)

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


def _get_completed_evidence(
    graph_result: dict,
) -> list[dict]:
    results = graph_result.get(
        "step_results",
        {},
    )

    evidence = []

    for result in results.values():
        if result.get("status") != "COMPLETED":
            continue

        result_evidence = result.get(
            "evidence",
            [],
        )

        if isinstance(result_evidence, list):
            evidence.extend(
                item
                for item in result_evidence
                if isinstance(item, dict)
            )

    return evidence


def _clean_text(text: str) -> str:
    text = str(text or "").strip()

    if not text:
        return ""

    text = re.sub(
        r"^(Introduction|Content|History|Summary|Overview):\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = text.replace(
        "\nHistory:",
        "",
    ).strip()

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(
            r"[a-z0-9]+",
            str(text or "").lower(),
        )
        if len(token) > 2
    }


def _get_current_question(
    execution_plan: dict,
) -> str:
    steps = execution_plan.get(
        "steps",
        [],
    )

    if not isinstance(steps, list) or not steps:
        return ""

    # merge_execution_plans appends the current request's
    # steps after previous session steps.
    current_step = steps[-1]

    if not isinstance(current_step, dict):
        return ""

    return str(
        current_step.get(
            "question",
            "",
        )
        or current_step.get(
            "query",
            "",
        )
        or ""
    )


def _has_any_term(
    text: str,
    terms: set[str],
) -> bool:
    lowered = str(text or "").lower()

    return any(
        re.search(
            rf"\b{re.escape(term)}\b",
            lowered,
        )
        for term in terms
    )


def _evidence_relevance_score(
    evidence_item: dict,
    question: str,
) -> float:
    if not isinstance(evidence_item, dict):
        return float("-inf")

    question_text = str(
        question or ""
    ).lower()

    question_tokens = _tokenize(
        question_text
    )

    evidence_text = " ".join(
        str(
            evidence_item.get(
                field,
                "",
            )
        )
        for field in (
            "text",
            "name",
            "content_type",
            "field_name",
            "section",
        )
    )

    evidence_tokens = _tokenize(
        evidence_text
    )

    if not question_tokens:
        return 0.0

    overlap = question_tokens & evidence_tokens

    score = float(
        min(
            len(overlap),
            12,
        ) * 4
    )

    # Exact multi-word phrase match is strong evidence
    # that the document addresses the actual question.
    normalized_question = re.sub(
        r"\s+",
        " ",
        question_text,
    ).strip()

    normalized_evidence = re.sub(
        r"\s+",
        " ",
        evidence_text.lower(),
    ).strip()

    if (
        normalized_question
        and normalized_question in normalized_evidence
    ):
        score += 30

    # Timing-related questions must prefer timing/visiting/
    # darshan evidence over unrelated FAQ content.
    timing_terms = {
        "timing",
        "timings",
        "time",
        "hours",
        "hour",
        "darshan",
        "opening",
        "opens",
        "close",
        "closes",
        "visiting",
        "visit",
    }

    evidence_timing_terms = {
        "timing",
        "timings",
        "time",
        "hours",
        "hour",
        "darshan",
        "opening",
        "opens",
        "close",
        "closes",
        "visiting",
        "visit",
    }

    if _has_any_term(
        question_text,
        timing_terms,
    ) and _has_any_term(
        evidence_text,
        evidence_timing_terms,
    ):
        score += 40

    # Festival/event questions should prefer festival/event
    # evidence over generic temple descriptions.
    festival_terms = {
        "festival",
        "festivals",
        "jathara",
        "jatara",
        "event",
        "events",
        "celebration",
        "celebrations",
    }

    if _has_any_term(
        question_text,
        festival_terms,
    ) and _has_any_term(
        evidence_text,
        festival_terms,
    ):
        score += 35

    # Location/access questions.
    access_terms = {
        "location",
        "located",
        "address",
        "reach",
        "reaching",
        "route",
        "directions",
        "how",
        "parking",
    }

    if _has_any_term(
        question_text,
        access_terms,
    ) and _has_any_term(
        evidence_text,
        access_terms,
    ):
        score += 30

    # General factual descriptions still get a small boost,
    # but content type is deliberately NOT the primary ranking.
    content_priority = {
        "overview": 5,
        "summary": 4,
        "religious_significance": 3,
        "spiritual_significance": 3,
        "history": 2,
        "sthalla_puranam": 2,
        "sthala_puranam": 2,
        "faq": 1,
        "audio": 0,
    }

    content_type = str(
        evidence_item.get(
            "content_type",
            "",
        )
    ).lower()

    score += content_priority.get(
        content_type,
        0,
    )

    return score


def _select_best_evidence(
    evidence: list[dict],
    question: str = "",
) -> dict | None:
    if not evidence:
        return None

    ranked = sorted(
        enumerate(evidence),
        key=lambda item: (
            _evidence_relevance_score(
                evidence_item=item[1],
                question=question,
            ),
            -item[0],
        ),
        reverse=True,
    )

    return ranked[0][1]


def _extract_answer_sentence(
    text: str,
) -> str:
    text = _clean_text(text)

    if not text:
        return ""

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    sentences = [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
    ]

    if not sentences:
        return text

    return sentences[0]


def _build_grounded_reply(
    evidence: list[dict],
    question: str = "",
) -> str:
    if not evidence:
        return (
            "I couldn't find enough verified evidence "
            "to answer that safely."
        )

    best_evidence = _select_best_evidence(
        evidence=evidence,
        question=question,
    )

    if not best_evidence:
        return (
            "I couldn't find enough verified evidence "
            "to answer that safely."
        )

    text = _clean_text(
        best_evidence.get(
            "text",
            "",
        )
    )

    if not text:
        return (
            "I found evidence for this request, "
            "but it did not contain usable answer text."
        )

    answer = _extract_answer_sentence(
        text
    )

    if not answer:
        return (
            "I found evidence for this request, "
            "but it did not contain usable answer text."
        )

    return answer


def _build_reply(
    execution_plan: dict,
    graph_result: dict,
) -> str:
    if execution_plan.get(
        "requires_clarification",
        False,
    ):
        return execution_plan.get(
            "clarification_question"
        ) or "I need a little more information."

    status = graph_result.get(
        "status",
        execution_plan.get(
            "status",
            "READY",
        ),
    )

    if status == "FAILED":
        return (
            "I couldn't complete the request safely."
        )

    if status == "ERROR":
        return (
            "I couldn't complete the request "
            "because an agent encountered an error."
        )

    if status == "LLM_UNAVAILABLE":
        return (
            "The query understanding service is "
            "temporarily unavailable. Please try again."
        )

    evidence = _get_completed_evidence(
        graph_result
    )

    if evidence:
        question = _get_current_question(
            execution_plan
        )

        return _build_grounded_reply(
            evidence=evidence,
            question=question,
        )

    return (
        "I couldn't find enough verified evidence "
        "to answer that safely."
    )


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

    state.user_query = request.message

    # ---------------------------------------------------------
    # SAFETY GATE
    # ---------------------------------------------------------

    if detect_emergency(
        request.message
    ):
        response = emergency_response()

        state.intent = "emergency"
        state.intent_confidence = 1.0

        state.validation = {
            "status": "EMERGENCY",
            "llm_called": False,
        }

        save_session(
            session_id,
            state.model_dump(),
        )

        return {
            "session_id": session_id,
            "intent": "emergency",
            "reply": response["reply"],
            "clarification": None,
            "status": "EMERGENCY",
        }

    # ---------------------------------------------------------
    # SESSION CONTEXT
    # ---------------------------------------------------------

    previous_state = state.model_dump()

    previous_execution_plan = (
        state.execution_plan or {}
    )

    previous_step_results = (
        previous_execution_plan.get(
            "step_results",
            {},
        )
    )

    # ---------------------------------------------------------
    # QUERY UNDERSTANDING
    # ---------------------------------------------------------

    try:
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

    except RuntimeError as exc:
        if str(exc).startswith(
            "LLM_UNAVAILABLE:"
        ):
            state.validation = {
                "status": "LLM_UNAVAILABLE",
                "llm_called": True,
                "error": str(exc),
            }

            save_session(
                session_id,
                state.model_dump(),
            )

            raise HTTPException(
                status_code=503,
                detail=(
                    "Query understanding service "
                    "is temporarily unavailable."
                ),
            ) from exc

        raise

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
    # ENTITY RESOLUTION
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
    # EXECUTION PLAN
    # ---------------------------------------------------------

    current_execution_plan = (
        build_execution_plan(
            resolved_query
        )
    )

    current_plan_dict = (
        current_execution_plan.model_dump()
    )

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

    execution_plan_dict = (
        validate_execution_plan(
            execution_plan_dict
        )
    )

    execution_plan_dict[
        "step_results"
    ] = previous_step_results

    state.execution_plan = (
        execution_plan_dict
    )

    # ---------------------------------------------------------
    # LANGGRAPH
    # ---------------------------------------------------------

    graph_input = {
        "session_id": session_id,
        "execution_plan": execution_plan_dict,
        "current_step_id": None,
        "step_results": previous_step_results,
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
    # PERSIST FULL INTERNAL STATE
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

    save_session(
        session_id,
        state.model_dump(),
    )

    # ---------------------------------------------------------
    # PUBLIC API RESPONSE
    # ---------------------------------------------------------

    status = graph_result.get(
        "status",
        execution_plan_dict.get(
            "status",
            "READY",
        ),
    )

    evidence = _get_completed_evidence(
        graph_result
    )

    return {
        "session_id": session_id,
        "intent": state.intent,
        "reply": _build_reply(
            execution_plan=execution_plan_dict,
            graph_result=graph_result,
        ),
        "clarification": (
            execution_plan_dict.get(
                "clarification_question"
            )
            if execution_plan_dict.get(
                "requires_clarification",
                False,
            )
            else None
        ),
        "status": status,
        "evidence": evidence,
    }