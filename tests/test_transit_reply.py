from app.api.chat import _build_transit_reply


def test_transit_reply_includes_attribution_and_fare_disclosure():
    reply = _build_transit_reply(
        {
            "step_results": {
                "travel-1": {
                    "transit_result": {
                        "origin": {"stop_name": "A"},
                        "destination": {"stop_name": "B"},
                        "service_date": "2026-10-23",
                        "mode": "bus",
                        "journeys": [
                            {
                                "departure_time": "08:00:00",
                                "arrival_time": "09:00:00",
                                "travel_minutes": 60,
                                "legs": [],
                            }
                        ],
                        "attribution": "Contains data provided by TGSRTC",
                        "fare": {
                            "status": "UNAVAILABLE",
                            "message": (
                                "Fare data is unavailable; this does not "
                                "mean free travel."
                            ),
                        },
                    }
                }
            }
        }
    )

    assert reply is not None
    assert "Contains data provided by TGSRTC" in reply
    assert "does not mean free travel" in reply


def test_transit_reply_offers_external_fallback_without_journeys():
    reply = _build_transit_reply(
        {
            "step_results": {
                "travel-1": {
                    "transit_result": {
                        "origin": {"stop_name": "A"},
                        "destination": {"stop_name": "B"},
                        "service_date": "2026-10-23",
                        "mode": "bus",
                        "journeys": [],
                    }
                }
            }
        }
    )

    assert reply is not None
    assert "https://www.redbus.in/" in reply
