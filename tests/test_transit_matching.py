from unittest.mock import patch

import pytest

from app.travel.transit.agent import _select_stop, plan_transit_journey
from app.travel.schemas import TravelPlanStep


def _stop(stop_id: str, name: str, **extra):
    return {
        "stop_id": stop_id,
        "stop_name": name,
        "location_type": extra.get("location_type"),
        "parent_station": extra.get("parent_station"),
    }


def test_exact_stop_beats_incidental_token_matches():
    selected = _select_stop(
        "MGBS",
        [
            _stop("incidental", "Nalgonda X Road Twd Mgbs"),
            _stop("official", "MGBS", location_type=1),
        ],
    )

    assert selected["stop_id"] == "official"


def test_multiple_token_matches_remain_ambiguous():
    with pytest.raises(ValueError, match="matches multiple transit stops"):
        _select_stop(
            "Nalgonda",
            [
                _stop("one", "Nalgonda X Road Twd Mgbs"),
                _stop("two", "Nalgonda X Road Twd Koti"),
            ],
        )


def test_nonexistent_stop_is_distinct_from_ambiguity():
    with pytest.raises(ValueError, match="No transit stop matched"):
        _select_stop("Adilabad", [_stop("one", "Nalgonda X Road")])


def test_hyderabad_mgbs_uses_defensible_feed_alias():
    step = TravelPlanStep(
        step_id="travel-1",
        question="Bus from Hyderabad MGBS to Bhongir",
        intent="route_search",
        origin="Hyderabad MGBS",
        destination="Bhongir",
        mode="bus",
    )
    stops = {
        "MGBS": [_stop("mgbs", "MGBS")],
        "Bhongir": [_stop("bhongir", "Bhongir")],
    }

    with patch(
        "app.travel.transit.agent.query_service.find_stops",
        side_effect=lambda provider_id, query, limit: stops[query],
    ), patch(
        "app.travel.transit.agent.find_direct_journeys",
        return_value=[],
    ), patch(
        "app.travel.transit.agent.find_best_transfer_options",
        return_value=[],
    ):
        result = plan_transit_journey(step)

    assert result["origin"]["stop_name"] == "MGBS"
    assert result["journey_status"] == "NO_SCHEDULED_JOURNEY"
