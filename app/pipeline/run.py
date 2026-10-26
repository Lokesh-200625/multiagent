"""Stage 3 - executes brain subqueries: dependency-ordered, independent
subqueries in parallel. Each step routes to exactly one agent via the
agent registry (app/agents/registry.py)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.agents.registry import REGISTRY_RESOLVED_CATEGORIES, get_agent
from app.services.entity_registry import get_entity_registry
from app.travel.resolver import TravelResolverClarificationError

logger = logging.getLogger(__name__)

_TEMPLE_ENTITY_TYPES = {
    "temple",
    "person",
    "festival",
    "event",
    "landmark",
    "place",
}


def _resolve_temple_ids(subquery: Any) -> list[str]:
    """Registry-first: person/festival mentions may map to canonical temple ids."""
    ids: set[str] = set()

    for entity in subquery.entities:
        if entity.entity_type not in _TEMPLE_ENTITY_TYPES:
            continue
        try:
            result = get_entity_registry().resolve(
                mention=entity.mention,
                entity_type=entity.entity_type,
                context={},
            )
        except Exception:  # noqa: BLE001
            logger.warning("registry resolve failed for %s", entity.mention)
            continue

        if result.get("status") == "RESOLVED" and result.get("entity_id"):
            ids.add(str(result["entity_id"]))

    return sorted(ids)


def _run_one(subquery: Any) -> dict:
    base = {
        "step_id": subquery.id,
        "category": subquery.category,
        "question": subquery.question,
    }

    try:
        agent = get_agent(subquery.category)

        context: dict = {}
        if subquery.category in REGISTRY_RESOLVED_CATEGORIES:
            context["temple_ids"] = _resolve_temple_ids(subquery)

        outcome = agent(subquery, context)

        return {**base, **outcome}

    except KeyError:
        return {
            **base,
            "status": "ERROR",
            "error": f"no_agent_registered:{subquery.category}",
            "evidence": [],
        }
    except TravelResolverClarificationError as exc:
        return {
            **base,
            "status": "NEEDS_CLARIFICATION",
            "query": exc.query,
            "candidates": [
                candidate.model_dump(mode="json")
                for candidate in exc.candidates
            ],
            "evidence": [],
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("subquery %s failed", subquery.id)
        return {
            **base,
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "evidence": [],
        }


def execute(subqueries: list) -> list[dict]:
    """Run subqueries in dependency layers; parallel within a layer.
    Returns results in the original subquery order."""
    results: dict[str, dict] = {}
    pending = list(subqueries)
    safety_counter = 0

    while pending:
        safety_counter += 1
        if safety_counter > 100:  # pathological guard
            break

        ready = [
            sq
            for sq in pending
            if all(dep in results for dep in sq.depends_on)
        ]
        if not ready:
            # dependency cycle or unknown id - run the rest anyway
            ready = [pending[0]]

        with ThreadPoolExecutor(
            max_workers=min(4, max(1, len(ready)))
        ) as pool:
            futures = {pool.submit(_run_one, sq): sq for sq in ready}
            for future, sq in futures.items():
                try:
                    results[sq.id] = future.result()
                except Exception as exc:  # noqa: BLE001 - should not happen
                    results[sq.id] = {
                        "step_id": sq.id,
                        "category": sq.category,
                        "question": sq.question,
                        "status": "ERROR",
                        "error": f"{type(exc).__name__}: {exc}",
                        "evidence": [],
                    }

        pending = [sq for sq in pending if sq.id not in results]

    return [results[sq.id] for sq in subqueries if sq.id in results]