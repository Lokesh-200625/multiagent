from app.services.travel_fallback import (
    deterministic_travel_understanding,
)


def test_deterministic_fallback_parses_comparison():
    result = deterministic_travel_understanding(
        "Compare bus, car, and bike from Hyderabad MGBS to Bhongir"
    )

    assert result is not None
    assert result.intent == "travel"
    assert result.subqueries[0].locations[0].mention == "Hyderabad MGBS"
    assert result.subqueries[0].locations[1].mention == "Bhongir"
    assert "route_search" in result.required_capabilities


def test_deterministic_fallback_parses_explicit_modes():
    result = deterministic_travel_understanding(
        "Find a metro from Ameerpet to Raidurg"
    )

    assert result is not None
    assert {item.value for item in result.preferences} >= {
        "metro",
    }
    assert "transit" in result.required_capabilities


def test_deterministic_fallback_does_not_guess_places():
    assert (
        deterministic_travel_understanding(
            "What is the cheapest way to travel?"
        )
        is None
    )
