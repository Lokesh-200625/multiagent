from unittest.mock import patch
from datetime import datetime, timezone

from app.api.chat import _build_multimodal_reply
from app.graph import nodes
from app.graph.workflow import workflow
from app.travel.resolver import TravelResolverClarificationError
from app.travel.schemas import (
    Coordinate,
    PlaceCandidate,
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


def test_multimodal_agent_keeps_two_successful_options_when_bus_fails():
    travel_step = TravelPlanStep(
        step_id="travel-1",
        question="Compare bus, car and bike",
        intent="route_search",
        origin="A",
        destination="B",
    )
    route_fact = TravelFact(
        key="distance",
        value=10,
        unit="km",
        provider="osrm",
        source_tier="open-data",
        fetched_at=datetime.now(timezone.utc),
    )
    duration_fact = route_fact.model_copy(
        update={"key": "duration", "value": 60, "unit": "minutes"}
    )
    road = TravelResult(
        status="COMPLETED",
        facts=[route_fact, duration_fact],
        metadata={"selected_provider": "osrm"},
    )

    with patch.object(
        nodes,
        "resolve_place",
        side_effect=[_place("A"), _place("B")],
    ), patch.object(nodes, "route_with_fallbacks", return_value=road), patch.object(
        nodes,
        "plan_transit_journey",
        side_effect=RuntimeError("feed unavailable"),
    ):
        result = nodes._complete_multimodal_travel(
            {"step_results": {}, "status": "RUNNING"},
            {"step_id": "travel-1"},
            "travel-1",
            travel_step,
            {},
        )

    options = result["step_results"]["travel-1"]["multimodal_result"]["options"]
    assert [option["available"] for option in options] == [False, True, True]


def test_multimodal_agent_reports_all_provider_failures():
    travel_step = TravelPlanStep(
        step_id="travel-1",
        question="Compare bus, car and bike",
        intent="route_search",
        origin="A",
        destination="B",
    )

    with patch.object(
        nodes,
        "resolve_place",
        side_effect=[_place("A"), _place("B")],
    ), patch.object(
        nodes,
        "route_with_fallbacks",
        side_effect=RuntimeError("routing unavailable"),
    ), patch.object(
        nodes,
        "plan_transit_journey",
        side_effect=RuntimeError("feed unavailable"),
    ):
        result = nodes._complete_multimodal_travel(
            {"step_results": {}, "status": "RUNNING"},
            {"step_id": "travel-1"},
            "travel-1",
            travel_step,
            {},
        )

    options = result["step_results"]["travel-1"]["multimodal_result"]["options"]
    assert [option["available"] for option in options] == [False, False, False]


def test_single_mode_requests_do_not_trigger_multimodal():
    bus = TravelPlanStep(
        step_id="travel-1",
        question="Find a bus from A to B",
        intent="route_search",
        origin="A",
        destination="B",
        mode="bus",
    )
    car = bus.model_copy(
        update={"question": "Drive from A to B by car", "mode": "car"}
    )

    assert nodes._is_multimodal_request(bus) is False
    assert nodes._is_multimodal_request(car) is False


def test_ambiguous_travel_place_returns_clarification():
    travel_step = TravelPlanStep(
        step_id="travel-1",
        question="Travel from Nalgonda to Yadadri",
        intent="route_search",
        origin="Nalgonda",
        destination="Yadadri",
    )
    error = TravelResolverClarificationError(
        "Nalgonda",
        [
            PlaceCandidate(
                display_name="Nalgonda district",
                latitude=17.05,
                longitude=79.27,
                source="test",
            ),
            PlaceCandidate(
                display_name="Nalgonda municipality",
                latitude=17.05,
                longitude=79.27,
                source="test",
            ),
        ],
    )

    with patch.object(nodes, "resolve_place", side_effect=error):
        result = nodes.travel_agent(
            {
                "current_step_id": "travel-1",
                "current_step": {
                    "step_id": "travel-1",
                    "origin": "Nalgonda",
                    "destination": "Yadadri",
                    "question": travel_step.question,
                },
                "execution_plan": {
                    "steps": [
                        {
                            "step_id": "travel-1",
                            "status": "READY",
                        }
                    ]
                },
                "step_results": {},
                "status": "RUNNING",
            }
        )

    assert result["status"] == "BLOCKED"
    assert result["clarification_required"] is True
    assert result["clarification_question"] == (
        "Which place do you mean by 'Nalgonda'?"
    )
    assert result["step_results"]["travel-1"]["status"] == (
        "NEEDS_CLARIFICATION"
    )
    assert result["execution_plan"]["steps"][0]["status"] == "BLOCKED"


def test_ambiguous_workflow_terminates_with_specific_question():
    error = TravelResolverClarificationError("Nalgonda", [])
    state = {
        "session_id": "test",
        "execution_plan": {
            "intent": "travel",
            "status": "READY",
            "requires_clarification": False,
            "steps": [
                {
                    "step_id": "travel-1",
                    "domain": "travel",
                    "intent": "route_search",
                    "question": "Travel from Nalgonda to Yadadri",
                    "origin": "Nalgonda",
                    "destination": "Yadadri",
                    "mode": "driving",
                    "status": "READY",
                }
            ],
        },
        "current_step_id": None,
        "step_results": {},
        "status": "PENDING",
        "error": None,
    }

    with patch.object(nodes, "resolve_place", side_effect=error):
        result = workflow.invoke(state)

    assert result["status"] == "BLOCKED"
    assert result["clarification_question"] == (
        "Which place do you mean by 'Nalgonda'?"
    )
