"""Budget agent tests - extraction, price parsing, arithmetic against evidence,
assumptions present, temple optional-not-summed, honesty notes. Transport is
mocked (no network); stay/food/temple use the real committed datasets."""
from __future__ import annotations

import app.agents.budget as bud
from app.agents.registry import (
    AGENT_REGISTRY,
    REGISTRY_RESOLVED_CATEGORIES,
    get_agent,
)
from app.pipeline.brain import BrainResult, BrainSubquery
from app.pipeline.validator import find_missing_numbers


def _mock_transport(cost: int = 912):
    def fake(origin_raw, destination_raw, mode, params):
        if not (origin_raw and destination_raw):
            return [], ["your starting point (for travel cost)"]
        low = cost * 2 if params.get("roundtrip") else cost
        return (
            [
                bud._item(
                    "transport",
                    "trip_total",
                    low,
                    low,
                    basis="one-way route cost",
                    method="distance_times_cost_per_km",
                )
            ],
            [],
        )

    return fake


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------

def test_registry_dispatch_budget():
    assert "budget" in AGENT_REGISTRY
    assert get_agent("budget") is AGENT_REGISTRY["budget"]
    assert "budget" in REGISTRY_RESOLVED_CATEGORIES


def test_brain_accepts_budget_label():
    sub = BrainSubquery(
        id="q1",
        category="budget",
        question="budget for Hyderabad to Yadadri",
        origin="Hyderabad",
        destination="Yadadri",
    )
    assert sub.category == "budget"
    result = BrainResult(
        language="en",
        category="budget",
        rewritten_query="trip cost",
        subqueries=[sub],
    )
    assert result.category == "budget"

    from app.pipeline.brain import _postprocess

    fixed = _postprocess(
        BrainResult(
            language="en", category="budget", rewritten_query="trip cost", subqueries=[]
        )
    )
    assert fixed.subqueries[0].category == "budget"


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

def test_extract_days_persons_roundtrip():
    p = bud._extract_params("budget for hyderabad to yadadri for 2 days, family of 4, round trip")
    assert p["days"] == 2
    assert p["nights"] == 1 and p["nights_from_days"]
    assert p["persons"] == 4
    assert p["roundtrip"] is True
    assert p["stay_excluded"] is False


def test_extract_does_not_misread_days_as_persons():
    p = bud._extract_params("how much for a 2 day trip")
    assert p["days"] == 2
    assert p["persons"] is None  # 'for 2 days' must NOT become persons=2


def test_extract_nights_direct_and_no_stay():
    p = bud._extract_params("3 nights in warangal trip without hotel")
    assert p["nights"] == 3
    assert p["days"] is None
    assert p["nights_from_days"] is False
    assert p["stay_excluded"] is True


# --------------------------------------------------------------------------
# money parsing
# --------------------------------------------------------------------------

def test_price_range_parsers():
    assert bud._parse_price_range("₹700–₹1,000 per night (Approx.)") == (700, 1000)
    assert bud._parse_price_range("₹100–200") == (100, 200)
    assert bud._parse_price_range("₹10,000+") == (10000, 10000)
    assert bud._parse_price_range(None) is None
    assert bud._money_values("Free") == []


# --------------------------------------------------------------------------
# end-to-end agent (transport mocked, real datasets)
# --------------------------------------------------------------------------

def test_full_budget_arithmetic_and_assumptions(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(912))

    out = bud.run(
        question="budget for hyderabad to yadadri for 2 days, family of 4, round trip",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
        mode="driving",
    )
    assert out["status"] == "COMPLETED"
    ev = out["evidence"]

    items = {i["component"]: i for i in ev if i.get("kind") == "budget_item"}
    # transport: round trip x2
    tr = [i for i in ev if i.get("component") == "transport" and i.get("scope") == "trip_total"]
    assert tr and tr[0]["low"] == 912 * 2 == tr[0]["high"]

    # stay: per_night and trip_total(=nights 1) consistent
    stay_night = [i for i in ev if i.get("component") == "stay" and i.get("scope") == "per_night"]
    stay_trip = [i for i in ev if i.get("component") == "stay" and i.get("scope") == "trip_total"]
    assert stay_night and stay_trip
    assert stay_trip[0]["low"] == stay_night[0]["low"] * 1
    assert stay_trip[0]["high"] == stay_night[0]["high"] * 1

    # food: per_meal -> per_day(x3) -> trip_total(x days x persons)
    meal = [i for i in ev if i.get("component") == "food" and i.get("scope") == "per_meal"]
    day = [i for i in ev if i.get("component") == "food" and i.get("scope") == "per_day"]
    food_trip = [i for i in ev if i.get("component") == "food" and i.get("scope") == "trip_total"]
    assert meal and day and food_trip
    assert day[0]["low"] == meal[0]["low"] * 3
    assert day[0]["high"] == meal[0]["high"] * 3
    assert food_trip[0]["low"] == day[0]["low"] * 2 * 4  # days=2, persons=4
    assert food_trip[0]["high"] == day[0]["high"] * 2 * 4

    # total = transport + stay + food (temple excluded)
    totals = [i for i in ev if i.get("kind") == "budget_total"]
    assert len(totals) == 1
    total = totals[0]
    expected_components = {"transport", "stay", "food"}
    assert set(total["components_included"]) == expected_components
    expected_low = tr[0]["low"] + stay_trip[0]["low"] + food_trip[0]["low"]
    expected_high = tr[0]["high"] + stay_trip[0]["high"] + food_trip[0]["high"]
    assert total["low"] == expected_low
    assert total["high"] == expected_high

    # assumptions the composer must restate
    keys = {a["key"] for a in ev if a.get("kind") == "assumption"}
    assert {"trip_duration", "nights_from_days", "persons", "roundtrip", "fuel_rate", "temple_excluded"} <= keys

    # temple component present but NOT in total
    temple = [i for i in ev if i.get("component") == "temple"]
    assert temple and temple[0]["in_total"] is False


def test_budget_without_inputs_returns_units_and_askback(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(500))

    out = bud.run(
        question="trip cost to yadadri",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin=None,
        destination="Yadadri",
    )
    ev = out["evidence"]
    assert out["status"] == "COMPLETED"
    # no days/persons -> NO food/stay trip totals, per-unit scopes only
    trip_totals = [
        i for i in ev if i.get("kind") == "budget_item" and i.get("scope") == "trip_total"
    ]
    assert trip_totals == []
    assert not any(i.get("kind") == "budget_total" for i in ev)
    notes = [i for i in ev if i.get("kind") == "note"]
    missing = [n for n in notes if n.get("key") == "missing_params"]
    assert missing, "must ask the user for the missing inputs"
    # per-unit evidence exists so rates are still usable
    scopes = {i["scope"] for i in ev if i.get("kind") == "budget_item"}
    assert "per_night" in scopes and "per_meal" in scopes


def test_no_hotel_query_excludes_stay_from_total(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(400))

    out = bud.run(
        question="1 day trip to yadadri without hotel, 2 people",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
    )
    ev = out["evidence"]
    components = {
        i["component"] for i in ev if i.get("kind") == "budget_item" and i.get("in_total")
    }
    assert "stay" not in components
    assert any(i.get("key") == "stay_excluded" for i in ev)
    # stay excluded -> no stay-related assumptions (no stray "nights" line)
    assert not any(
        a.get("key") in {"nights", "nights_from_days", "per_room"}
        for a in ev
        if a.get("kind") == "assumption"
    )
    totals = [i for i in ev if i.get("kind") == "budget_total"]
    assert totals and "stay" not in totals[0]["components_included"]
    # 1 day -> 0 nights -> no stay trip_total even if included
    assert not [
        i for i in ev if i.get("component") == "stay" and i.get("scope") == "trip_total"
    ]


def test_temple_prices_found_and_never_summed(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(300))
    out = bud.run(
        question="budget for a bhadrachalam day trip with hotel for 2 days",
        entities=["Bhadrachalam"],
        temple_ids=["T0002"],
        origin="Hyderabad",
        destination="Bhadrachalam",
    )
    ev = out["evidence"]
    temple = [i for i in ev if i.get("component") == "temple"]
    assert temple
    t = temple[0]
    assert t["in_total"] is False
    assert t["samples"], "T0002 has priced sevas - samples expected"
    assert t["high"] is not None and t["high"] >= 50
    totals = [i for i in ev if i.get("kind") == "budget_total"]
    assert totals and "temple" not in totals[0]["components_included"]


def test_budget_reply_numbers_pass_validator(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(912))
    out = bud.run(
        question="budget for hyderabad to yadadri for 2 days",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
    )
    total = next(i for i in out["evidence"] if i.get("kind") == "budget_total")
    draft = (
        f"Estimated total: ₹{total['low']} to ₹{total['high']} "
        f"covering {', '.join(total['components_included'])}."
    )
    results = [{"status": "COMPLETED", "evidence": out["evidence"]}]
    assert find_missing_numbers(draft, results) == []


# --------------------------------------------------------------------------
# regressions from the first live smoke (DEGRADED dumps, missing prices,
# magic fuel rate)
# --------------------------------------------------------------------------

def test_validator_accepts_thousand_separators_in_draft():
    from app.pipeline.validator import find_missing_numbers

    results = [
        {
            "status": "COMPLETED",
            "evidence": [
                {
                    "kind": "budget_total",
                    "low": 1066,
                    "high": 4866,
                    "unit": "currency",
                    "components_included": ["stay", "transport"],
                },
                {"kind": "budget_item", "component": "stay", "low": 700, "high": 4500},
                {"kind": "assumption", "text": "Nights assumed as days-1 = 1 night(s)."},
            ],
        }
    ]
    draft = (
        "Estimated total: ₹1,066 - ₹4,866 (stay ₹700 - ₹4,500), "
        "assuming 1 night."
    )
    assert find_missing_numbers(draft, results) == []


def test_validator_still_flags_real_hallucinated_number():
    from app.pipeline.validator import find_missing_numbers

    results = [
        {
            "status": "COMPLETED",
            "evidence": [{"kind": "budget_item", "low": 700, "high": 4500}],
        }
    ]
    assert find_missing_numbers("this will cost 9999 rupees", results) == ["9999"]


def test_degraded_fallback_shows_budget_prices():
    from app.pipeline.validator import render_structured

    results = [
        {
            "question": "budget",
            "status": "COMPLETED",
            "evidence": [
                {
                    "kind": "budget_item",
                    "component": "stay",
                    "scope": "per_night",
                    "low": 700,
                    "high": 4500,
                    "unit": "currency",
                    "basis": "nightly price_range across 5 listed stays",
                },
                {
                    "kind": "budget_total",
                    "low": 1066,
                    "high": 4866,
                    "unit": "currency",
                    "components_included": ["stay", "transport"],
                },
            ],
        }
    ]
    out = render_structured(results)
    assert "700" in out and "4500" in out          # range survives fallback
    assert "1066" in out and "4866" in out         # total survives fallback
    assert "budget total" in out
    assert "currency" not in out                   # unit never leaks raw


def test_fuel_rate_derived_from_reality_and_in_assumptions():
    from app.core.config import settings

    # derived, not hardcoded: price / mileage
    assert settings.fuel_price_per_liter == 116.15  # Hyderabad 2026-10-26
    assert settings.fuel_cost_per_km == round(
        settings.fuel_price_per_liter / settings.vehicle_mileage_kmpl, 2
    )
    assert settings.vehicle_mileage_kmpl == 14.0  # blended real-world city+highway
    assert settings.fuel_cost_per_km == 8.30

    # every number the composer may quote from the fuel assumption is in evidence
    ev = bud.run(
        question="trip cost to yadadri",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin=None,
        destination="Yadadri",
    )["evidence"]
    text = " ".join(a["text"] for a in ev if a.get("kind") == "assumption")
    assert "116.15" in text and "14.0" in text and "8.30" in text


def test_composer_range_glue_repair_is_punctuation_only():
    from app.pipeline.compose import _repair_range_glue

    glued = (
        "**Stay:** ₹700₹4500 per night; total ₹1172₹4972. "
        "Food ₹300₹900 per day."
    )
    fixed = _repair_range_glue(glued)
    assert "₹700–₹4500" in fixed
    assert "₹1172–₹4972" in fixed
    assert "₹300–₹900" in fixed
    # already-hyphenated ranges and year-then-price patterns untouched
    unchanged = "₹2400–₹9600 in 2026 ₹100 per meal = ₹8.30 per km"
    assert _repair_range_glue(unchanged) == unchanged
    # digits never change
    assert "".join(ch for ch in fixed if ch.isdigit()) == "".join(
        ch for ch in glued if ch.isdigit()
    )


# --------------------------------------------------------------------------
# review round 2: rooms, one-way vs round-trip, modes, structured block
# --------------------------------------------------------------------------

def test_extract_rooms_and_oneway():
    p = bud._extract_params("2 nights in 2 rooms, one way trip")
    assert p["rooms"] == 2
    assert p["oneway"] is True and p["roundtrip"] is False

    p = bud._extract_params("round trip budget")
    assert p["roundtrip"] is True and p["oneway"] is False

    # "0 rooms" clamps to 1, negative-looking input cannot explode
    assert bud._extract_params("0 rooms")["rooms"] == 1


def test_stay_multiplies_rooms(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(400))
    out = bud.run(
        question="1 night at yadadri in 2 rooms, 2 people",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
    )
    ev = out["evidence"]
    night = next(i for i in ev if i.get("component") == "stay" and i.get("scope") == "per_night")
    trip = next(i for i in ev if i.get("component") == "stay" and i.get("scope") == "trip_total")
    assert trip["low"] == night["low"] * 1 * 2
    assert trip["high"] == night["high"] * 1 * 2
    assert any(a["key"] == "rooms" for a in ev if a.get("kind") == "assumption")


def test_roundtrip_doubles_and_oneway_does_not(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(500))

    out = bud.run(
        question="round trip budget hyderabad to yadadri for 2 days",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
    )
    # _mock_transport honors params["roundtrip"]
    tr = next(
        i for i in out["evidence"]
        if i.get("component") == "transport" and i.get("scope") == "trip_total"
    )
    assert tr["low"] == 1000
    assert any(a["key"] == "roundtrip" for a in out["evidence"] if a.get("kind") == "assumption")

    out2 = bud.run(
        question="budget hyderabad to yadadri one way for 2 days",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
    )
    tr2 = next(
        i for i in out2["evidence"]
        if i.get("component") == "transport" and i.get("scope") == "trip_total"
    )
    assert tr2["low"] == 500  # one-way: no doubling
    assert not any(a["key"] == "roundtrip" for a in out2["evidence"] if a.get("kind") == "assumption")


def test_walking_and_transit_never_use_car_fuel(monkeypatch):
    calls = {}

    def fake_travel_run(subquery):
        calls["mode"] = subquery.get("mode")
        return {
            "status": "COMPLETED",
            "evidence": [
                {"source": "travel", "kind": "fact", "key": "distance", "value": 60.96},
                {
                    "source": "travel",
                    "kind": "calculation",
                    "key": "travel_cost",
                    "value": 500,
                    "unit": "currency",
                    "method": "distance_times_cost_per_km",
                },
            ],
        }

    monkeypatch.setattr(bud._travel, "run", fake_travel_run)

    # walking: distance shown, NO cost item, NO missing nag
    out = bud.run(
        question="walking budget hyderabad to yadadri",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
        mode="walking",
    )
    assert calls["mode"] == "walking"
    assert not any(
        i.get("component") == "transport" for i in out["evidence"]
        if i.get("kind") == "budget_item"
    )
    assert any(i.get("key") == "transport_walking" for i in out["evidence"])
    # car-fuel assumption must NOT appear for walking
    assert not any(
        a.get("key") == "fuel_rate" for a in out["evidence"] if a.get("kind") == "assumption"
    )
    # params ask-back may exist (no days given), but NO transport gap
    missing_err = str(
        next(
            (i.get("error") for i in out["evidence"] if i.get("key") == "missing_params"),
            "",
        )
    )
    assert "public transport fare" not in missing_err
    assert "starting point" not in missing_err

    # transit: NO car-fuel cost item; fare reported as missing (Redbus)
    out = bud.run(
        question="budget by bus hyderabad to yadadri for 2 days",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
        mode="transit",
    )
    assert not any(
        i.get("component") == "transport" for i in out["evidence"]
        if i.get("kind") == "budget_item"
    )
    total = next((i for i in out["evidence"] if i.get("kind") == "budget_total"), None)
    if total:
        assert "transport" not in total["components_included"]
    assert "public transport fare" in str(
        next(i for i in out["evidence"] if i.get("key") == "missing_params")["error"]
    )
    # regression: bus trip must NOT expose the petrol/mileage assumption
    assert not any(
        a.get("key") == "fuel_rate" for a in out["evidence"] if a.get("kind") == "assumption"
    )


def test_structured_budget_block_for_api(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(472))
    out = bud.run(
        question="budget for hyderabad to yadadri for 2 days with hotel",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
    )
    block = out["budget"]
    tp = block["trip_parameters"]
    assert tp["origin"] == "Hyderabad"
    assert tp["days"] == 2 and tp["nights"] == 1 and tp["rooms"] == 1
    assert block["estimated_total"]["low"] == 472 + 700  # min stay 700 (dataset)
    assert block["estimated_total"]["components_included"] == ["stay", "transport"]
    assert block["is_complete"] is False            # food persons missing
    assert block["clarification_required"] is True
    assert "persons" in " ".join(block["missing_information"])
    assert "fuel_rate" in block["assumption_keys"]
    # every priced component is marked as currency; unpriced (temple) is not
    assert all(c["currency"] is True for c in block["components"] if c["low"] is not None)
    assert all(c["currency"] is False for c in block["components"] if c["low"] is None)


def test_zero_days_yields_no_crash_and_no_false_totals(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(300))
    out = bud.run(
        question="0 day trip to yadadri, 0 people budget",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
    )
    assert out["status"] == "COMPLETED"
    # 0 nights -> no stay trip_total; 0 persons -> no food trip_total
    assert not any(
        i.get("component") == "stay" and i.get("scope") == "trip_total"
        for i in out["evidence"]
        if i.get("kind") == "budget_item"
    )
    assert not any(
        i.get("component") == "food" and i.get("scope") == "trip_total"
        for i in out["evidence"]
        if i.get("kind") == "budget_item"
    )


# --------------------------------------------------------------------------
# review round 3: partial vs complete totals, canonical direction, statuses
# --------------------------------------------------------------------------

def test_partial_total_labeled_and_estimate_status(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(506))
    # no persons -> food missing -> total covers stay+transport only
    out = bud.run(
        question="budget for hyderabad to yadadri for 2 days with hotel",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
    )
    total = next(i for i in out["evidence"] if i.get("kind") == "budget_total")
    assert total["partial"] is True
    assert "food" in total["components_missing"]
    block = out["budget"]
    assert block["estimate_status"] == "partial"
    assert block["estimated_total"]["partial"] is True
    assert block["is_complete"] is False

    # direction metadata must match the arithmetic (one-way default)
    assert block["trip_parameters"]["travel_direction"] == "one_way"
    tp_item = next(i for i in out["evidence"] if i.get("kind") == "trip_parameters")
    assert tp_item["travel_direction"] == "one_way"

    # round trip -> canonical direction round_trip everywhere
    out2 = bud.run(
        question="round trip budget for hyderabad to yadadri for 2 days with hotel, family of 4",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
    )
    tp2 = next(i for i in out2["evidence"] if i.get("kind") == "trip_parameters")
    assert tp2["travel_direction"] == "round_trip"
    assert out2["budget"]["trip_parameters"]["travel_direction"] == "round_trip"
    tr = next(
        i for i in out2["evidence"]
        if i.get("component") == "transport" and i.get("scope") == "trip_total"
    )
    assert tr["low"] == 506 * 2  # arithmetic agrees with metadata


def test_complete_estimate_when_all_components_present(monkeypatch):
    monkeypatch.setattr(bud, "_transport_component", _mock_transport(506))
    out = bud.run(
        question="budget for hyderabad to yadadri for 2 days with hotel, family of 4",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        origin="Hyderabad",
        destination="Yadadri",
    )
    block = out["budget"]
    total = next(i for i in out["evidence"] if i.get("kind") == "budget_total")
    assert set(total["components_included"]) == {"transport", "stay", "food"}
    assert total["partial"] is False
    assert block["estimate_status"] == "complete"
    assert block["is_complete"] is True
    assert block["clarification_required"] is False
    # no missing-note when everything is priced
    assert not any(i.get("key") == "missing_params" for i in out["evidence"])
    # recompute totals from components (validator-grade check)
    trip_items = [
        i for i in out["evidence"]
        if i.get("kind") == "budget_item" and i.get("scope") == "trip_total" and i.get("in_total")
    ]
    assert total["low"] == sum(i["low"] for i in trip_items)
    assert total["high"] == sum(i["high"] for i in trip_items)


def test_transit_assumptions_exclude_fuel_but_keep_others(monkeypatch):
    def fake_travel_run(subquery):
        return {"status": "COMPLETED", "evidence": []}

    monkeypatch.setattr(bud._travel, "run", fake_travel_run)
    out = bud.run(
        question="budget by bus from hyderabad to bhadrachalam for 2 days",
        entities=["Bhadrachalam"],
        origin="Hyderabad",
        destination="Bhadrachalam",
        mode="transit",
    )
    keys = {a["key"] for a in out["evidence"] if a.get("kind") == "assumption"}
    assert "fuel_rate" not in keys          # bus never sees petrol assumptions
    assert "trip_duration" in keys          # relevant assumptions stay
    assert "nights_from_days" in keys