from app.api.chat import _build_road_route_reply


def _graph_result(metadata=None):
    metadata = metadata or {}
    return {
        "step_results": {
            "travel-1": {
                "travel_result": {
                    "status": "COMPLETED",
                    "facts": [
                        {
                            "key": "distance",
                            "value": 80,
                            "unit": "km",
                        },
                        {
                            "key": "duration",
                            "value": 90,
                            "unit": "minutes",
                        },
                    ],
                    "errors": [],
                    "calculations": metadata.pop("calculations", []),
                    "metadata": metadata,
                }
            }
        }
    }


def test_road_reply_displays_alternative_routes():
    result = _graph_result({
        "selected_provider": "osrm",
        "alternative_routes": [
            {
                "route_number": 1,
                "distance_km": 82,
                "duration_minutes": 95,
            },
            {
                "route_number": 2,
                "distance_km": 88,
                "duration_minutes": 100,
            },
        ],
    })

    reply = _build_road_route_reply(result)

    assert reply is not None
    assert "Driving route distance: 80 km." in reply
    assert "Estimated driving time: 1 hr 30 min." in reply
    assert "Available alternative driving routes:" in reply
    assert "Route 1: approximately 82 km, 95 min (OSRM estimate)." in reply
    assert "Route 2: approximately 88 km, 100 min (OSRM estimate)." in reply
    assert "does not guarantee the fastest or cheapest route" in reply


def test_road_reply_without_alternatives_preserves_primary_route():
    reply = _build_road_route_reply(_graph_result({
        "selected_provider": "osrm",
        "alternative_routes": [],
    }))

    assert reply is not None
    assert "Driving route distance: 80 km." in reply
    assert "Estimated driving time: 1 hr 30 min." in reply
    assert "Available alternative driving routes:" not in reply


def test_road_reply_ignores_invalid_alternative_values():
    result = _graph_result({
        "alternative_routes": [
            {"route_number": 1, "distance_km": None, "duration_minutes": 90},
            {"route_number": 2, "distance_km": "invalid", "duration_minutes": 90},
            {"route_number": 3, "distance_km": -1, "duration_minutes": 90},
        ],
    })

    reply = _build_road_route_reply(result)

    assert reply is not None
    assert "Driving route distance: 80 km." in reply
    assert "Available alternative driving routes:" not in reply

def test_road_reply_discloses_unavailable_toll_data():
    result = _graph_result({
        "selected_provider": "osrm",
        "toll_status": {
            "status": "UNAVAILABLE",
            "message": (
                "Toll charges are unavailable from the current "
                "routing provider. This does not mean the route "
                "is toll-free."
            ),
        },
    })

    reply = _build_road_route_reply(result)

    assert reply is not None
    assert "Toll charges are unavailable" in reply
    assert "does not mean the route is toll-free" in reply


def test_road_reply_discloses_bike_profile_and_fuel_assumptions():
    result = _graph_result({
        "selected_provider": "osrm",
        "profile_disclosure": (
            "Bike routes use the provider's standard driving profile; "
            "they are not motorcycle-specific."
        ),
        "calculations": [
            {
                "key": "fuel_cost",
                "status": "COMPLETED",
                "value": 262.5,
                "inputs": {
                    "mileage_km_per_litre": 40,
                    "fuel_price_per_litre": 105,
                },
            },
        ],
    })

    reply = _build_road_route_reply(result)

    assert reply is not None
    assert "Estimated fuel cost: INR 262.50" in reply
    assert "40 km/litre" in reply
    assert "not motorcycle-specific" in reply