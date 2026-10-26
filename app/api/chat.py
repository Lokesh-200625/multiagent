"""POST /chat - thin entry point: safety -> brain -> agents -> composer."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.models.query import ChatRequest
from app.pipeline import safety
from app.pipeline.brain import DIRECT_CATEGORIES, understand
from app.pipeline.compose import answer as compose_answer
from app.pipeline.compose import clarify as compose_clarify
from app.pipeline.run import execute
from app.pipeline.validator import (
    collect_evidence,
    find_missing_numbers,
    render_structured,
)
from app.services.llm import LLMUnavailable
from app.services.session import get_session, save_session

router = APIRouter()

MAX_FEEDBACK_ROUNDS = 1

DIRECT_STATUS = "DIRECT"


def _append_turn(history: list, user: str, reply: str, category: str) -> list:
    history = list(history or [])
    history.append(
        {
            "user": str(user)[:500],
            "reply": str(reply)[:500],
            "category": category,
        }
    )
    return history[-10:]


def _persist(session_id: str, history: list) -> None:
    save_session(
        session_id,
        {"session_id": session_id, "history": history},
    )


def _response(
    *,
    session_id: str,
    category: str,
    reply: str,
    status: str,
    clarification: str | None = None,
    evidence: list | None = None,
) -> dict:
    return {
        "session_id": session_id,
        "category": category,
        "intent": category,  # backward-compat alias for old clients
        "reply": reply,
        "clarification": clarification,
        "status": status,
        "evidence": evidence or [],
    }


def _emergency_reply(session_id: str, history: list, user: str) -> dict:
    policy = safety.response()
    _persist(
        session_id,
        _append_turn(history, user, policy["reply"], "emergency"),
    )
    return _response(
        session_id=session_id,
        category="emergency",
        reply=policy["reply"],
        status="EMERGENCY",
    )


@router.post("/chat")
def chat(request: ChatRequest) -> dict:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    session_id = request.session_id or str(uuid.uuid4())
    existing = get_session(session_id) or {}
    history = existing.get("history", [])

    # ---- STAGE 0: safety gate (no LLM) ----
    if safety.detect(message):
        return _emergency_reply(session_id, history, message)

    # ---- STAGE 1: brain (LLM #1) ----
    try:
        brain = understand(message=message, history=history[-3:])
    except LLMUnavailable as exc:
        _persist(
            session_id,
            _append_turn(history, message, "", "unavailable"),
        )
        raise HTTPException(
            status_code=503,
            detail="Query understanding service is temporarily unavailable.",
        ) from exc

    category = brain.category

    if category == "emergency":
        reply = (brain.direct_reply or "").strip() or safety.response()["reply"]
        _persist(
            session_id,
            _append_turn(history, message, reply, category),
        )
        return _response(
            session_id=session_id,
            category=category,
            reply=reply,
            status="EMERGENCY",
        )

    # ---- direct categories answered by the brain itself ----
    if category in DIRECT_CATEGORIES:
        reply = brain.direct_reply or ""
        _persist(
            session_id,
            _append_turn(history, message, reply, category),
        )
        return _response(
            session_id=session_id,
            category=category,
            reply=reply,
            status=DIRECT_STATUS,
        )

    if not brain.subqueries:
        raise HTTPException(
            status_code=503,
            detail="Could not understand the request. Please try again.",
        )

    # ---- STAGE 3: agents ----
    results = execute(brain.subqueries)

    # ---- clarification from ambiguous places ----
    clarification_step = next(
        (
            r
            for r in results
            if r.get("status") == "NEEDS_CLARIFICATION"
        ),
        None,
    )
    if clarification_step is not None:
        try:
            question = compose_clarify(
                query=str(clarification_step.get("query") or ""),
                candidates=clarification_step.get("candidates", []),
                language=brain.language,
            )
        except LLMUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail="Query understanding service is temporarily unavailable.",
            ) from exc

        _persist(
            session_id,
            _append_turn(history, message, question, category),
        )
        return _response(
            session_id=session_id,
            category=category,
            reply=question,
            status="NEEDS_CLARIFICATION",
            clarification=question,
            evidence=[],
        )

    # ---- STAGE 4: composer (LLM #2) + deterministic number validation ----
    try:
        reply = compose_answer(
            question=brain.rewritten_query,
            language=brain.language,
            results=results,
        )

        missing = find_missing_numbers(reply, results)
        rounds = 0
        while missing and rounds < MAX_FEEDBACK_ROUNDS:
            rounds += 1
            reply = compose_answer(
                question=brain.rewritten_query,
                language=brain.language,
                results=results,
                feedback=missing,
            )
            missing = find_missing_numbers(reply, results)

        status = "COMPLETED"
        if missing:
            reply = render_structured(results)
            status = "DEGRADED"

    except LLMUnavailable:
        reply = render_structured(results)
        status = "DEGRADED"
    except Exception:
        reply = render_structured(results)
        status = "DEGRADED"

    # ---- STAGE 5: persist + respond ----
    _persist(
        session_id,
        _append_turn(history, message, reply, category),
    )

    return _response(
        session_id=session_id,
        category=category,
        reply=reply,
        status=status,
        evidence=collect_evidence(results),
    )
