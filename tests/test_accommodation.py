"""Accommodation agent tests - static loaders, temple filter, labeling,
Overpass merge (mocked, no network), honesty note, registry + brain wiring."""
from __future__ import annotations

import app.agents.accommodation as acc
from app.agents.registry import (
    AGENT_REGISTRY,
    REGISTRY_RESOLVED_CATEGORIES,
    get_agent,
)
from app.pipeline.brain import BrainResult, BrainSubquery
from app.pipeline.validator import find_missing_numbers


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------

def test_registry_dispatch_entries():
    assert "accommodation" in AGENT_REGISTRY
    assert "restaurant" in AGENT_REGISTRY
    assert get_agent("accommodation") is AGENT_REGISTRY["accommodation"]
    assert get_agent("restaurant") is AGENT_REGISTRY["restaurant"]
    assert {"accommodation", "restaurant"} <= REGISTRY_RESOLVED_CATEGORIES


def test_brain_schema_accepts_new_labels():
    sub = BrainSubquery(id="q1", category="accommodation", question="hotels?")
    assert sub.category == "accommodation"
    result = BrainResult(
        language="en",
        category="restaurant",
        rewritten_query="veg food near Yadadri",
        subqueries=[BrainSubquery(id="q1", category="restaurant", question="veg food near Yadadri")],
    )
    assert result.category == "restaurant"


def test_brain_repair_synthesizes_subquery_for_new_category():
    from app.pipeline.brain import _postprocess

    result = BrainResult(
        language="en",
        category="accommodation",
        rewritten_query="hotels near Warangal",
        subqueries=[],
    )
    fixed = _postprocess(result)
    assert len(fixed.subqueries) == 1
    assert fixed.subqueries[0].category == "accommodation"


# --------------------------------------------------------------------------
# static loaders
# --------------------------------------------------------------------------

def test_hotel_loader_normalizes_all_temples():
    hotels = acc._load_hotels()
    assert len(hotels) >= 100
    temples = {h["temple_id"] for h in hotels}
    assert "T0001" in temples and len(temples) >= 20
    for h in hotels[:20]:
        assert h["name"]
        assert "_haystack" in h
        assert isinstance(h["booking_links"], dict)
    # contentReference artifacts cleaned from about text
    assert all(":contentReference" not in h["about"] for h in hotels)


def test_restaurant_loader_tolerates_10_schemas():
    restaurants = acc._load_restaurants()
    assert len(restaurants) >= 100
    named = [r for r in restaurants if r["name"]]
    assert len(named) == len(restaurants)
    assert {"T0001", "T0002"} <= {r["temple_id"] for r in restaurants}
    verified = [r for r in restaurants if r["last_verified"]]
    assert len(verified) >= 80  # dataset carries verification dates


def test_restaurant_distance_variants_normalize():
    restaurants = acc._load_restaurants()
    with_distance = [r for r in restaurants if r["distance_km"] is not None]
    assert len(with_distance) >= 60  # number | {value} | walkingDistance forms


# --------------------------------------------------------------------------
# agent behavior (Overpass mocked - no network in tests)
# --------------------------------------------------------------------------

def _no_overpass(monkeypatch):
    monkeypatch.setattr(acc, "_overpass_search", lambda kind, lat, lng: ([], "down"))


def test_static_filter_by_temple_id(monkeypatch):
    _no_overpass(monkeypatch)
    out = acc.run(
        question="hotels near Yadadri",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        kind="hotel",
    )
    assert out["status"] == "COMPLETED"
    hotels = [e for e in out["evidence"] if e.get("kind") == "hotel"]
    assert hotels, "expected static hotels for T0001"
    assert all(e["temple_id"] == "T0001" for e in hotels)
    assert all(e["source_tier"] == "internal_dataset" for e in hotels)
    anchor = [e for e in out["evidence"] if e.get("kind") == "place"]
    assert anchor and anchor[0]["resolved_via"]


def test_static_restaurants_veg_pref(monkeypatch):
    _no_overpass(monkeypatch)
    out = acc.run(
        question="pure veg restaurants near Yadadri",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        kind="restaurant",
    )
    items = [e for e in out["evidence"] if e.get("kind") == "restaurant"]
    assert items
    veg_items = [e for e in items if e.get("vegetarian") is not None]
    assert veg_items and all(e["vegetarian"] for e in veg_items)
    assert all(e.get("last_verified") for e in items if e.get("last_verified"))


def test_osm_merge_labels_and_dedupe(monkeypatch):
    static_names = [h["name"] for h in acc._load_hotels() if h["temple_id"] == "T0001"]
    dup_name = static_names[0]
    fake_pois = [
        {  # duplicate of a static hotel -> must be dropped
            "name": dup_name, "category": "hotel", "cuisine": "", "phone": "",
            "website": "", "address": "", "latitude": 17.5, "longitude": 78.9,
            "distance_km": 0.4,
        },
        {  # genuinely new POI -> kept, labeled open-data
            "name": "OSM Guest House", "category": "guest_house", "cuisine": "",
            "phone": "9000000000", "website": "", "address": "Hill Rd",
            "latitude": 17.59, "longitude": 78.94, "distance_km": 1.2,
        },
    ]
    monkeypatch.setattr(acc, "_overpass_search", lambda kind, lat, lng: (fake_pois, None))

    out = acc.run(
        question="hotels near Yadadri",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        kind="hotel",
    )
    osm_items = [e for e in out["evidence"] if e.get("source") == "osm_search"]
    assert [e["name"] for e in osm_items] == ["OSM Guest House"]
    assert all(e["source_tier"] == "open-data" for e in osm_items)
    # no osm_status note on success
    notes = [e for e in out["evidence"] if e.get("kind") == "note"]
    assert not any(n.get("key") == "osm_status" for n in notes)


def test_osm_failure_is_noted_but_static_still_answers(monkeypatch):
    _no_overpass(monkeypatch)
    out = acc.run(
        question="hotels near Yadadri",
        entities=["Yadadri"],
        temple_ids=["T0001"],
        kind="hotel",
    )
    assert out["status"] == "COMPLETED"
    assert [e for e in out["evidence"] if e.get("kind") == "hotel"]
    notes = [e for e in out["evidence"] if e.get("kind") == "note"]
    assert any(n.get("key") == "osm_status" for n in notes)


def test_no_data_returns_honest_note_with_hint(monkeypatch):
    _no_overpass(monkeypatch)
    out = acc.run(
        question="hotels in Zzznowhere", entities=[], temple_ids=None, kind="hotel"
    )
    assert out["status"] == "COMPLETED"
    notes = [e for e in out["evidence"] if e.get("key") == "no_data"]
    assert len(notes) == 1
    assert notes[0]["hint"] and "booking.com" in notes[0]["hint"]


def test_no_cross_temple_leak_via_food_words(monkeypatch):
    """Regression: 'pure/veg/vegetarian' are filters, never selectors."""
    _no_overpass(monkeypatch)
    out = acc.run(
        question="Which pure vegetarian restaurants are near Bhadrachalam?",
        entities=["Bhadrachalam"],
        temple_ids=None,
        kind="restaurant",
    )
    items = [e for e in out["evidence"] if e.get("kind") == "restaurant"]
    assert items, "expected Bhadrachalam restaurants"
    assert all(e["temple_id"] == "T0002" for e in items), (
        f"cross-temple leak: {[e['temple_id'] for e in items]}"
    )


def test_place_token_path_without_entities(monkeypatch):
    _no_overpass(monkeypatch)
    out = acc.run(
        question="veg restaurants near bhadrachalam",
        entities=[],
        temple_ids=None,
        kind="restaurant",
    )
    items = [e for e in out["evidence"] if e.get("kind") == "restaurant"]
    assert items
    assert all(e["temple_id"] == "T0002" for e in items)
    veg_known = [e for e in items if e.get("vegetarian") is not None]
    assert veg_known and all(e["vegetarian"] for e in veg_known)


def test_exact_name_lookup_when_no_place(monkeypatch):
    _no_overpass(monkeypatch)
    out = acc.run(
        question="sannidhi emerald", entities=[], temple_ids=None, kind="hotel"
    )
    items = [e for e in out["evidence"] if e.get("kind") == "hotel"]
    assert items
    assert any(e["name"] == "Sannidhi Emerald" for e in items)


def test_numbers_in_evidence_pass_validator(monkeypatch):
    _no_overpass(monkeypatch)
    out = acc.run(
        question="hotels near Basara",
        entities=["Basara"],
        temple_ids=["T0004"],
        kind="hotel",
    )
    prices = [e for e in out["evidence"] if e.get("price_range")]
    assert prices, "expected price_range in static hotels"
    import re

    match = re.search(r"\d+", prices[0]["price_range"])
    assert match, "price_range should contain a number"
    number = match.group(0)
    draft = f"The listed price starts at ₹{number} per night."
    results = [{"status": "COMPLETED", "evidence": out["evidence"]}]
    assert find_missing_numbers(draft, results) == []
