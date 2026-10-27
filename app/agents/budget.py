"""Budget agent - estimated trip budget with explicit RANGES.

Calculation contract (nothing invented, nothing hardcoded):
  base numbers ONLY from: route cost calc (env fuel price / mileage x km),
  hotel price_range fields (dataset), restaurant average-cost fields
  (dataset), temple seva/ticket charges (dataset).
  multipliers ONLY from: numbers the user stated (days/nights/persons/rooms)
  or config (meals per day), each recorded as `assumption` evidence the
  composer must restate. All arithmetic happens HERE so the validator can
  check every quoted number against evidence.
  TOTAL = transport + stay + food (only components the user has enabled and
  we could price). Temple sevas are optional and are shown as an itemized
  range but never summed into the total. Unknown costs are reported as
  missing - never silently treated as zero.
  Transport modes: driving -> fuel-only estimate (excludes tolls, parking,
  driver charges); transit -> NO car-fuel formula, fare unknown -> honest
  note + missing entry; walking -> no transport cost (stated).
  The run() also returns a machine-readable `budget` block (trip_parameters,
  components, estimated_total, missing_information, assumption_keys,
  is_complete, clarification_required) for API consumers.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.agents import travel as _travel
from app.core.config import settings
from app.travel.resolver import TravelResolverClarificationError

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLE_DIR = PROJECT_ROOT / "data" / "temples"

_MONEY_RE = re.compile(r"₹\s?([0-9][0-9,]*(?:\.[0-9]+)?)")

_DAYS_RE = re.compile(r"\b(\d+)\s*-?\s*days?\b", re.I)
_NIGHTS_RE = re.compile(r"\b(\d+)\s*-?\s*nights?\b", re.I)
_PERSONS_RE = re.compile(
    r"\b(?:family\s+of|group\s+of)\s*(\d+)\b"
    r"|\b(?:for|of|with)\s*(\d+)\s*(?:people|persons|adults|members|pax)\b",
    re.I,
)
_PERSONS_BARE_RE = re.compile(r"\b(\d+)\s*(?:people|persons|adults|members)\b", re.I)
_ROOMS_RE = re.compile(r"\b(\d+)\s*-?\s*rooms?\b", re.I)
_ROUNTRIP_RE = re.compile(r"\bround\s*-?\s*trip\b|\bto\s+and\s+fro\b|\bto-and-fro\b", re.I)
_ONEWAY_RE = re.compile(r"\bone\s*-?\s*way\b|\boneway\b", re.I)
_NO_STAY_RE = re.compile(
    r"\b(?:without|no|skip|exclude|not\s+tak(?:ing|e))\s+(?:a\s+|an\s+)?"
    r"(?:hotel|stay|room|accommodation)\b",
    re.I,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------
# deterministic parameter extraction (no LLM here)
# ---------------------------------------------------------------------------

def _extract_params(question: str) -> dict[str, Any]:
    text = question or ""
    days = None
    nights = None
    nights_from_days = False

    m = _NIGHTS_RE.search(text)
    if m:
        nights = int(m.group(1))
    m = _DAYS_RE.search(text)
    if m:
        days = int(m.group(1))
    if nights is None and days is not None:
        nights = max(days - 1, 0)
        nights_from_days = True

    persons = None
    m = _PERSONS_RE.search(text)
    if m:
        persons = int(m.group(1) or m.group(2))
    if persons is None:
        m = _PERSONS_BARE_RE.search(text)
        if m:
            persons = int(m.group(1))

    rooms = None
    m = _ROOMS_RE.search(text)
    if m:
        rooms = max(int(m.group(1)), 1)

    return {
        "days": days,
        "nights": nights,
        "nights_from_days": nights_from_days,
        "persons": persons,
        "rooms": rooms,
        "roundtrip": bool(_ROUNTRIP_RE.search(text)) and not _ONEWAY_RE.search(text),
        "oneway": bool(_ONEWAY_RE.search(text)),
        "stay_excluded": bool(_NO_STAY_RE.search(text)),
    }


# ---------------------------------------------------------------------------
# money parsing (dataset strings -> integer rupee ranges)
# ---------------------------------------------------------------------------

def _money_values(text: Any) -> list[int]:
    if text is None:
        return []
    values: list[int] = []
    for raw in _MONEY_RE.findall(str(text)):
        try:
            values.append(int(float(raw.replace(",", ""))))
        except ValueError:
            continue
    return values


def _parse_price_range(text: Any) -> tuple[int, int] | None:
    """'₹700–₹1,000 per night (Approx.)' -> (700, 1000); '₹100–200' -> (100, 200)"""
    if text is None:
        return None
    raw = str(text)
    values = _money_values(raw)
    if values:
        # capture a bare right-side range value: "₹100–200" -> 100, 200
        bare = [
            int(n.replace(",", ""))
            for n in re.findall(r"[–—\-]\s*(\d[\d,]*(?:\.\d+)?)\b", raw)
        ]
        values.extend(bare)
        return min(values), max(values)
    # fall back for forms like "700-1000" without the ₹ glyph
    numbers = [
        int(n.replace(",", ""))
        for n in re.findall(r"\b(\d{1,3}(?:,\d{3})+|\d+)\b", raw)
    ]
    if numbers:
        return min(numbers), max(numbers)
    return None


def _item(
    component: str,
    scope: str,
    low: int,
    high: int,
    *,
    basis: str,
    method: str,
    in_total: bool = True,
) -> dict[str, Any]:
    return {
        "source": "budget",
        "kind": "budget_item",
        "component": component,
        "scope": scope,
        "low": int(low),
        "high": int(high),
        "unit": "currency",
        "basis": basis,
        "method": method,
        "estimated": True,
        "in_total": in_total,
    }


def _assumption(key: str, text: str) -> dict[str, Any]:
    return {"source": "budget", "kind": "assumption", "key": key, "text": text}


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------

def _transport_component(
    origin_raw: str | None,
    destination_raw: str | None,
    mode: str,
    params: dict[str, Any],
) -> tuple[list[dict], list[str]]:
    """Reuses the travel agent verbatim (same resolve, chain, cost calc).

    The car-fuel formula applies ONLY to driving. Transit gets an honest
    'fare not in our dataset' gap (bus fares live on Redbus), walking gets
    an explicit zero (no cost item, no nagging missing entry).
    """
    missing: list[str] = []
    if not (origin_raw and str(origin_raw).strip()):
        missing.append("your starting point (for travel cost)")
        return [], missing
    if not (destination_raw and str(destination_raw).strip()):
        missing.append("your destination (for travel cost)")
        return [], missing

    result = _travel.run(
        {
            "id": "budget_transport",
            "category": "travel_route",
            "question": f"route from {origin_raw} to {destination_raw}",
            "origin": origin_raw,
            "destination": destination_raw,
            "mode": mode,
        }
    )
    if result.get("status") not in {"COMPLETED", "ERROR"}:
        return [], missing

    evidence = result.get("evidence") or []
    if result.get("status") == "ERROR":
        items = [
            e
            for e in evidence
            if isinstance(e, dict) and e.get("kind") in {"note", "error", "place"}
        ]
        missing.append("travel cost (route unavailable)")
        return items, missing

    cost_value: int | None = None
    cost_method = ""
    kept: list[dict] = []
    for entry in evidence:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("kind") == "calculation"
            and entry.get("key") == "travel_cost"
            and entry.get("unit") == "currency"
        ):
            try:
                cost_value = int(round(float(entry.get("value"))))
            except (TypeError, ValueError):
                cost_value = None
            cost_method = str(entry.get("method") or "")
        elif entry.get("kind") in {"fact", "status", "note", "error", "place"}:
            kept.append(entry)

    if mode == "walking":
        kept.append(
            {
                "source": "budget",
                "kind": "note",
                "key": "transport_walking",
                "error": "Walking trip - no transport cost included.",
                "hint": None,
            }
        )
        return kept, missing

    if mode == "transit":
        # no car-fuel formula for bus/train trips; fares are not in our data
        missing.append("public transport fare (not in our dataset - check Redbus)")
        return kept, missing

    if cost_value is None:
        missing.append("travel cost (route gave no cost)")
        return kept, missing

    if params.get("roundtrip"):
        low = high = cost_value * 2
        method = f"round_trip_2x_one_way ({cost_method or 'distance_times_cost_per_km'})"
        items = kept + [
            _item(
                "transport",
                "trip_total",
                low,
                high,
                basis=f"round trip: one-way route cost x2 (one way = {cost_value})",
                method=method,
            )
        ]
    else:
        items = kept + [
            _item(
                "transport",
                "trip_total",
                cost_value,
                cost_value,
                basis="one-way route cost",
                method=cost_method or "distance_times_cost_per_km",
            )
        ]
        if not params.get("oneway"):
            items.append(
                {
                    "source": "budget",
                    "kind": "note",
                    "key": "one_way_only",
                    "error": (
                        "Transport covers ONE WAY only - tell the user to add "
                        "'round trip' to their question to include the return "
                        "journey."
                    ),
                    "hint": None,
                }
            )
    return items, missing


def _stay_and_food_items(
    question: str,
    entities: list[str],
    temple_ids: list[str] | None,
    params: dict[str, Any],
) -> tuple[list[dict], list[str]]:
    from app.agents import accommodation as _acc

    evidence: list[dict] = []
    missing: list[str] = []
    q = question or ""
    rooms = max(int(params.get("rooms") or 1), 1)

    # --- stay ---
    if not params.get("stay_excluded"):
        hotels = _acc._select_static(
            _acc._load_hotels(),
            question=q,
            entities=entities,
            temple_ids=temple_ids,
            kind="hotel",
        )[: _acc.MAX_STATIC]
        ranges = [
            r for r in (_parse_price_range(h.get("price_range")) for h in hotels) if r
        ]
        if ranges:
            low = min(r[0] for r in ranges)
            high = max(r[1] for r in ranges)
            basis = f"nightly price_range across {len(ranges)} listed stays"
            evidence.append(
                _item("stay", "per_night", low, high, basis=basis, method="dataset_price_range")
            )
            nights = params.get("nights")
            if nights:
                evidence.append(
                    _item(
                        "stay",
                        "trip_total",
                        low * int(nights) * rooms,
                        high * int(nights) * rooms,
                        basis=(
                            f"{basis} x {int(nights)} night(s)"
                            + (f" x {rooms} room(s)" if rooms > 1 else "")
                        ),
                        method="range_x_nights_x_rooms",
                    )
                )
        else:
            missing.append("hotel prices (no stays found for this place)")
    else:
        evidence.append(
            {
                "source": "budget",
                "kind": "note",
                "key": "stay_excluded",
                "error": "Stay/hotel excluded from this budget (as requested).",
                "hint": None,
            }
        )

    # --- food ---
    restaurants = _acc._select_static(
        _acc._load_restaurants(),
        question=q,
        entities=entities,
        temple_ids=temple_ids,
        kind="restaurant",
    )[: _acc.MAX_STATIC]
    ranges = [
        r
        for r in (
            _parse_price_range(r_.get("average_cost_for_one")) for r_ in restaurants
        )
        if r
    ]
    if ranges:
        low = min(r[0] for r in ranges)
        high = max(r[1] for r in ranges)
        basis = f"average cost for one across {len(ranges)} listed restaurants"
        evidence.append(
            _item("food", "per_meal", low, high, basis=basis, method="dataset_avg_cost")
        )
        days = params.get("days")
        persons = params.get("persons")
        meals = max(int(settings.budget_meals_per_day), 1)
        if days:
            day_low, day_high = low * meals, high * meals
            evidence.append(
                _item(
                    "food",
                    "per_day",
                    day_low,
                    day_high,
                    basis=f"{basis} x {meals} meals/day",
                    method="range_x_meals_per_day",
                )
            )
            if persons:
                evidence.append(
                    _item(
                        "food",
                        "trip_total",
                        day_low * int(days) * int(persons),
                        day_high * int(days) * int(persons),
                        basis=(
                            f"per-day range x {int(days)} day(s) x "
                            f"{int(persons)} person(s)"
                        ),
                        method="range_x_meals_x_days_x_persons",
                    )
                )
    else:
        missing.append("meal prices (no priced restaurants found for this place)")

    return evidence, missing


def _temple_costs(temple_ids: list[str] | None, anchor_tokens: set[str]) -> list[dict]:
    """Seva/ticket charges straight from the temple JSON. Never summed."""
    candidates: list[Path] = []
    for tid in temple_ids or []:
        path = TEMPLE_DIR / f"{tid}.json"
        if path.exists():
            candidates.append(path)

    if not candidates and anchor_tokens:
        for path in sorted(TEMPLE_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            names = [str(data.get("name") or "")]
            names.extend(str(a) for a in (data.get("alternate_names") or []) if a)
            name_tokens = set(_TOKEN_RE.findall(" ".join(names).lower()))
            if name_tokens and (name_tokens & anchor_tokens):
                candidates.append(path)
                break

    if not candidates:
        return []

    data = json.loads(candidates[0].read_text(encoding="utf-8"))
    samples: list[dict] = []
    amounts: list[int] = []
    free_stated = False

    for seva in data.get("sevas") or []:
        if not isinstance(seva, dict):
            continue
        charges = str(seva.get("charges") or "").strip()
        if not charges:
            continue
        if re.search(r"\bfree\b", charges, re.I):
            free_stated = True
        values = _money_values(charges)
        if values:
            amounts.extend(values)
            if len(samples) < 6:
                samples.append(
                    {"name": str(seva.get("name") or "").strip(), "charges": charges}
                )

    tickets = data.get("darshan_and_tickets") or {}
    for field in ("general_darshan", "special_darshan", "ticket_details"):
        text = str(tickets.get(field) or "")
        if re.search(r"\bfree\b", text, re.I):
            free_stated = True
        amounts.extend(_money_values(text))

    items: list[dict] = [
        {
            "source": "budget",
            "kind": "budget_item",
            "component": "temple",
            "scope": "optional_sevas",
            "temple_id": str(data.get("temple_id") or ""),
            "temple_name": str(data.get("name") or ""),
            "low": min(amounts) if amounts else None,
            "high": max(amounts) if amounts else None,
            "unit": "currency" if amounts else None,
            "free_darshan_stated": free_stated,
            "samples": samples,
            "in_total": False,
            "basis": "seva/ticket charges from temple records",
            "estimated": False,
        }
    ]
    if not amounts:
        if free_stated:
            note = (
                "General darshan listed as FREE in our records; no priced "
                "seva/ticket entries to total."
            )
        else:
            note = "No priced seva/ticket entries in our records for this temple."
        items.append(
            {
                "source": "budget",
                "kind": "note",
                "key": "temple_prices_unavailable",
                "error": note,
                "hint": None,
            }
        )
    return items


# ---------------------------------------------------------------------------
# agent entry point (registry contract)
# ---------------------------------------------------------------------------

def _trip_parameters_item(
    params: dict[str, Any],
    origin: str | None,
    destination: str | None,
    mode: str,
) -> dict[str, Any]:
    rooms = params.get("rooms") or 1
    parts = [
        f"{origin or 'origin unknown'} to {destination or 'destination unknown'}"
    ]
    if params.get("days") is not None:
        parts.append(f"{params['days']} day(s)")
    if params.get("nights") is not None:
        parts.append(f"{params['nights']} night(s)")
    if params.get("persons") is not None:
        parts.append(f"{params['persons']} person(s)")
    parts.append(f"{rooms} room(s)" if not params.get("stay_excluded") else "no hotel")
    parts.append(mode)
    return {
        "source": "budget",
        "kind": "trip_parameters",
        "origin": origin,
        "destination": destination,
        "mode": mode,
        # canonical direction the arithmetic AND the reply must use:
        # round_trip (x2 legs) or one_way (default single leg)
        "travel_direction": "round_trip" if params.get("roundtrip") else "one_way",
        "days": params.get("days"),
        "nights": params.get("nights"),
        "persons": params.get("persons"),
        "rooms": None if params.get("stay_excluded") else int(rooms),
        "roundtrip": params.get("roundtrip", False),
        "oneway": params.get("oneway", False),
        "stay_excluded": params.get("stay_excluded", False),
        "text": "Trip: " + " · ".join(parts),
    }


def _structured_budget(
    params: dict[str, Any],
    evidence: list[dict],
    missing: list[str],
    origin: str | None,
    destination: str | None,
    mode: str,
) -> dict[str, Any]:
    components = [
        {
            "component": e.get("component"),
            "scope": e.get("scope"),
            "low": e.get("low"),
            "high": e.get("high"),
            "currency": e.get("unit") == "currency",
            "basis": e.get("basis"),
            "method": e.get("method"),
            "in_total": bool(e.get("in_total", True)),
        }
        for e in evidence
        if e.get("kind") == "budget_item"
    ]
    total_item = next(
        (e for e in evidence if e.get("kind") == "budget_total"), None
    )
    assumption_keys = [
        a.get("key") for a in evidence if a.get("kind") == "assumption"
    ]
    estimate_status = (
        "complete" if (not missing and total_item is not None) else "partial"
    )
    return {
        "trip_parameters": {
            "origin": origin,
            "destination": destination,
            "mode": mode,
            "travel_direction": (
                "round_trip" if params.get("roundtrip") else "one_way"
            ),
            "days": params.get("days"),
            "nights": params.get("nights"),
            "persons": params.get("persons"),
            "rooms": None if params.get("stay_excluded") else (params.get("rooms") or 1),
            "roundtrip": params.get("roundtrip", False),
            "oneway": params.get("oneway", False),
            "stay_excluded": params.get("stay_excluded", False),
        },
        "components": components,
        "estimate_status": estimate_status,
        "estimated_total": (
            {
                "low": total_item.get("low"),
                "high": total_item.get("high"),
                "currency": True,
                "components_included": total_item.get("components_included"),
                "partial": bool(total_item.get("partial", False)),
                "components_missing": total_item.get("components_missing", []),
            }
            if total_item
            else None
        ),
        "missing_information": list(missing),
        "assumption_keys": assumption_keys,
        "is_complete": estimate_status == "complete",
        "clarification_required": bool(missing),
    }


def run(
    *,
    question: str = "",
    entities: list[str] | None = None,
    temple_ids: list[str] | None = None,
    origin: str | None = None,
    destination: str | None = None,
    mode: str | None = None,
) -> dict:
    entities = entities or []
    params = _extract_params(question)
    resolved_mode = mode or "driving"

    try:
        evidence: list[dict] = []
        missing: list[str] = []

        evidence.append(_trip_parameters_item(params, origin, destination, resolved_mode))

        # --- assumptions (composer MUST restate these) ---
        if params["days"] is not None:
            evidence.append(
                _assumption("trip_duration", f"Trip length taken from your query: {params['days']} day(s).")
            )
        if not params["stay_excluded"] and params["nights"] is not None:
            if params["nights_from_days"]:
                evidence.append(
                    _assumption(
                        "nights_from_days",
                        f"Nights assumed as days-1 = {params['nights']} night(s) "
                        "(travel-day pattern).",
                    )
                )
            else:
                evidence.append(
                    _assumption("nights", f"Stay length: {params['nights']} night(s).")
                )
        if params["persons"] is not None:
            evidence.append(
                _assumption("persons", f"Party size from your query: {params['persons']} person(s).")
            )
        if params["rooms"] is not None and not params["stay_excluded"]:
            evidence.append(
                _assumption("rooms", f"Rooms from your query: {params['rooms']} room(s).")
            )
        if params["roundtrip"]:
            evidence.append(
                _assumption(
                    "roundtrip",
                    "Round trip assumed: return leg estimated as 2 x one-way "
                    "cost (same route; actual return may differ).",
                )
            )
        if resolved_mode == "driving":
            # car-fuel assumption is irrelevant to bus/train/walking trips -
            # only driving budgets may mention the fuel rate
            evidence.append(
                _assumption(
                    "fuel_rate",
                    f"Travel cost uses fuel ₹{settings.fuel_price_per_liter} per litre "
                    f"÷ {settings.vehicle_mileage_kmpl} km/litre = "
                    f"₹{settings.fuel_cost_per_km:.2f} per km - FUEL ONLY, excludes "
                    f"tolls, parking and driver charges (config, derived from real "
                    f"pump prices).",
                )
            )
        if not params["stay_excluded"]:
            evidence.append(
                _assumption(
                    "per_room",
                    "Hotel prices are per room per night (not multiplied by persons).",
                )
            )
        evidence.append(
            _assumption(
                "temple_excluded",
                "Temple seva/ticket costs are optional and NOT included in the total.",
            )
        )

        # --- transport (travel agent, same math as everywhere else) ---
        transport_items, transport_missing = _transport_component(
            origin, destination, resolved_mode, params
        )
        evidence.extend(transport_items)
        missing.extend(transport_missing)

        # --- stay + food (accommodation dataset) ---
        stay_food, stay_food_missing = _stay_and_food_items(
            question, entities, temple_ids, params
        )
        evidence.extend(stay_food)
        missing.extend(stay_food_missing)

        # --- temple sevas (dataset, optional, never summed) ---
        anchor_tokens = set(_TOKEN_RE.findall((destination or "").lower()))
        for mention in [destination, *entities]:
            if mention:
                anchor_tokens |= set(_TOKEN_RE.findall(str(mention).lower()))
        anchor_tokens = {t for t in anchor_tokens if len(t) >= 3}
        evidence.extend(_temple_costs(temple_ids, anchor_tokens))

        # --- totals: sum only trip_total components (transport/stay/food) ---
        totals = [
            e
            for e in evidence
            if e.get("kind") == "budget_item"
            and e.get("in_total")
            and e.get("scope") == "trip_total"
        ]
        if totals:
            components = sorted({t["component"] for t in totals})
            all_components = {"transport", "stay", "food"}
            if params["stay_excluded"]:
                all_components.discard("stay")
            absent = sorted(all_components - set(components))
            evidence.append(
                {
                    "source": "budget",
                    "kind": "budget_total",
                    "low": sum(int(t["low"]) for t in totals),
                    "high": sum(int(t["high"]) for t in totals),
                    "unit": "currency",
                    "components_included": components,
                    "partial": bool(absent),
                    "components_missing": absent,
                    "method": "sum_trip_components",
                    "estimated": True,
                }
            )
            if absent:
                missing.append(
                    "this total only covers "
                    + ", ".join(components)
                    + " (missing: "
                    + ", ".join(absent)
                    + ")"
                )

        if params["days"] is None or params["persons"] is None:
            gaps = []
            if params["days"] is None:
                gaps.append("number of days/nights")
            if params["persons"] is None:
                gaps.append("number of persons")
            missing.append("to get a full trip total, tell me " + " and ".join(gaps))

        # de-duplicate while preserving order
        seen: set[str] = set()
        missing_unique = [m for m in missing if not (m in seen or seen.add(m))]

        if missing_unique:
            evidence.append(
                {
                    "source": "budget",
                    "kind": "note",
                    "key": "missing_params",
                    "error": "; ".join(missing_unique),
                    "hint": None,
                }
            )

        budget_block = _structured_budget(
            params, evidence, missing_unique, origin, destination, resolved_mode
        )
        return {"status": "COMPLETED", "evidence": evidence, "budget": budget_block}

    except TravelResolverClarificationError:
        raise
    except Exception as exc:  # noqa: BLE001 - budget must not crash the pipeline
        logger.exception("budget agent failed")
        return {
            "status": "ERROR",
            "error": f"budget failed: {type(exc).__name__}: {exc}",
            "evidence": [],
        }
