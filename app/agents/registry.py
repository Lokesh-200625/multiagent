"""Agent dispatch registry - orchestration by data, not hardcoded branches.

Adding a new agent/category later = ONE entry in AGENT_REGISTRY here
(+ one label in brain.py's schema enum). No orchestration logic changes,
no reply strings, no routing if/else chains anywhere.

Agent contract: fn(subquery, context) -> {status, evidence, ...}
  - never raises for expected failures (return ERROR step instead)
  - may raise TravelResolverClarificationError (runner turns it into
    a composer-phrased clarification)
"""
from __future__ import annotations

from typing import Any, Callable

from app.agents import accommodation as _accommodation
from app.agents import budget as _budget
from app.agents import temple as _temple
from app.agents import travel as _travel

AgentFn = Callable[[Any, dict], dict]


def _run_temple(subquery: Any, context: dict) -> dict:
    return _temple.run(
        subquery.question,
        temple_ids=context.get("temple_ids") or None,
    )


def _run_travel(subquery: Any, context: dict) -> dict:
    return _travel.run(
        {
            "id": subquery.id,
            "category": subquery.category,
            "question": subquery.question,
            "origin": subquery.origin,
            "destination": subquery.destination,
            "mode": subquery.mode or "driving",
        }
    )


def _mentions(subquery: Any) -> list[str]:
    return [
        str(getattr(entity, "mention", entity) or "")
        for entity in (getattr(subquery, "entities", None) or [])
        if str(getattr(entity, "mention", entity) or "").strip()
    ]


def _run_accommodation(subquery: Any, context: dict) -> dict:
    return _accommodation.run(
        question=subquery.question,
        entities=_mentions(subquery),
        temple_ids=context.get("temple_ids") or None,
        kind="hotel",
    )


def _run_restaurant(subquery: Any, context: dict) -> dict:
    return _accommodation.run(
        question=subquery.question,
        entities=_mentions(subquery),
        temple_ids=context.get("temple_ids") or None,
        kind="restaurant",
    )


def _run_budget(subquery: Any, context: dict) -> dict:
    return _budget.run(
        question=subquery.question,
        entities=_mentions(subquery),
        temple_ids=context.get("temple_ids") or None,
        origin=subquery.origin,
        destination=subquery.destination,
        mode=subquery.mode,
    )


AGENT_REGISTRY: dict[str, AgentFn] = {
    "temple_info": _run_temple,
    "travel_route": _run_travel,
    "travel_nearby": _run_travel,
    "accommodation": _run_accommodation,
    "restaurant": _run_restaurant,
    "budget": _run_budget,
}

# Categories that need registry entity-id resolution before running.
REGISTRY_RESOLVED_CATEGORIES = {
    "temple_info",
    "accommodation",
    "restaurant",
    "budget",
}


def get_agent(category: str) -> AgentFn:
    agent = AGENT_REGISTRY.get(category)
    if agent is None:
        raise KeyError(category)
    return agent