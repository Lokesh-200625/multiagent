from __future__ import annotations

import math
from typing import Any


def _finite_non_negative(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def normalize_option(
    *,
    mode: str,
    duration_minutes: Any,
    estimated_cost_inr: Any = None,
    provider: str | None = None,
    available: bool = True,
    reason: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    duration = _finite_non_negative(duration_minutes)
    cost = _finite_non_negative(estimated_cost_inr)

    option = {
        "mode": mode,
        "provider": provider,
        "duration_minutes": duration,
        "estimated_cost_inr": cost,
        "available": bool(available and duration is not None),
        "cost_status": "AVAILABLE" if cost is not None else "UNAVAILABLE",
        "reason": reason,
    }
    option.update(details)
    return option


def rank_options(
    options: list[dict[str, Any]],
    preference: str,
) -> dict[str, Any]:
    """Rank only options with the evidence required by the preference."""
    normalized_preference = preference.strip().lower()
    if normalized_preference not in {"cheapest", "fastest", "balanced"}:
        raise ValueError("preference must be cheapest, fastest, or balanced")

    available = [
        option
        for option in options
        if option.get("available") is True
        and _finite_non_negative(option.get("duration_minutes")) is not None
    ]

    if normalized_preference == "cheapest":
        available = [
            option
            for option in available
            if _finite_non_negative(option.get("estimated_cost_inr"))
            is not None
        ]
        key = lambda option: float(option["estimated_cost_inr"])
    elif normalized_preference == "fastest":
        key = lambda option: float(option["duration_minutes"])
    else:
        available = [
            option
            for option in available
            if _finite_non_negative(option.get("estimated_cost_inr"))
            is not None
        ]
        if not available:
            return {
                "status": "UNAVAILABLE",
                "preference": normalized_preference,
                "selected": None,
                "reason": "balanced ranking requires comparable cost and duration",
            }
        max_duration = max(float(item["duration_minutes"]) for item in available)
        max_cost = max(float(item["estimated_cost_inr"]) for item in available)
        key = lambda option: (
            (float(option["duration_minutes"]) / max_duration if max_duration else 0)
            + (float(option["estimated_cost_inr"]) / max_cost if max_cost else 0)
        )

    if not available:
        return {
            "status": "UNAVAILABLE",
            "preference": normalized_preference,
            "selected": None,
            "reason": (
                "no options contain the evidence required for this ranking"
            ),
        }

    return {
        "status": "COMPLETED",
        "preference": normalized_preference,
        "selected": min(available, key=key),
        "reason": None,
    }
