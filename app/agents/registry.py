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


AGENT_REGISTRY: dict[str, AgentFn] = {
    "temple_info": _run_temple,
    "travel_route": _run_travel,
    "travel_nearby": _run_travel,
}

# Categories that need registry entity-id resolution before running.
REGISTRY_RESOLVED_CATEGORIES = {"temple_info"}


def get_agent(category: str) -> AgentFn:
    agent = AGENT_REGISTRY.get(category)
    if agent is None:
        raise KeyError(category)
    return agent