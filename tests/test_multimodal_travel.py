from unittest.mock import patch
from datetime import datetime, timezone

from app.api.chat import _build_multimodal_reply
from app.graph import nodes
from app.travel.schemas import (
    Coordinate,
    ResolvedPlace,
    TravelFact,
    TravelPlanStep,
    TravelResult,
)


def _place(name: str) -> ResolvedPlace:
    return ResolvedPlace(
        query=name,
        display_name=name,
        coordinates=Coordinate(latitude=17.4, longitude=78.4),
        source="test",
    )


def test_multimodal_reply_renders_each_option_and_unavailable_fare():
    reply = _build_multimodal_reply(
        {
            "step_results": {
                "travel-1": {
                    "multimodal_result": {
                        "origin": "Nalgonda",
                        "destination": "Yadadri",
                        "options": [
                            {
                                "mode": "bus",
                                "available": True,
                                "duration_minutes": 120,
                                "provider": "tgsrtc",
                                "scheduled": True,
                                "attribution": "Contains data provided by TGSRTC",
                                "fare": {"status": "UNAVAILABLE"},
                                "journeys": [
                                    {
                                        "departure_time": "08:00:00",
                                        "arrival_time": "10:00:00",
                                    }
                                ],
                            },
                            {
                                "mode": "car",
                                "available": False,
                                "reason": "routing failed",
                            },
                            {
                                "mode": "bike",
                                "available": True,
                                "duration_minutes": 110,
                                "provider": "osrm",
                                "fuel": {"status": "UNAVAILABLE"},
                                "profile_disclosure": (
                                    "Standard driving profile; not motorcycle-specific."
                                ),
                            },
                        ],
                        "ranking": {
                            "status": "UNAVAILABLE",
                            "preference": "cheapest",
                            "reason": "no comparable costs",
                        },
                    }
                }
            }
        }
    )

    assert reply is not None
    assert "Bus:" in reply
    assert "Car: unavailable" in reply
    assert "Bike:" in reply
    assert "Contains data provided by TGSRTC" in reply
    assert "does not mean free travel" in reply
    assert "not motorcycle-specific" in reply


def test_multimodal_agent_keeps_partial_provider_results():
    travel_step = TravelPlanStep(
        step_id="travel-1",
        question="Compare bus, car and bike",
        intent="route_search",
        origin="A",
        destination="B",
        preferences={},
    )
    state = {
        "step_results": {},
        "status": "RUNNING",
    }

    route_fact = TravelFact(
        key="distance",
        value=10,
        unit="km",
        provider="osrm",
        source_tier="open-data",
        fetched_at=datetime.now(timezone.utc),
    )
    duration_fact = route_fact.model_copy(
        update={
            "key": "duration",
            "value": 60,
            "unit": "minutes",
        }
    )

    with patch.object(nodes, "resolve_place", side_effect=[_place("A"), _place("B")]), \
         patch.object(
             nodes,
             "route_with_fallbacks",
             side_effect=[
                 TravelResult(
                     status="COMPLETED",
                     facts=[route_fact, duration_fact],
                     metadata={"selected_provider": "osrm"},
                 ),
                 RuntimeError("bike unavailable"),
             ],
         ), \
         patch.object(
             nodes,
             "plan_transit_journey",
             side_effect=RuntimeError("feed unavailable"),
         ):
        result = nodes._complete_multimodal_travel(
            state,
            {"step_id": "travel-1"},
            "travel-1",
            travel_step,
            {},
        )

    options = result["step_results"]["travel-1"]["multimodal_result"]["options"]
    assert [option["mode"] for option in options] == ["bus", "car", "bike"]
    assert options[1]["available"] is True
    assert options[2]["available"] is False
