from app.travel.options import normalize_option, rank_options


def test_cheapest_does_not_treat_missing_cost_as_zero():
    result = rank_options(
        [
            normalize_option(
                mode="bus",
                duration_minutes=180,
                estimated_cost_inr=None,
            ),
            normalize_option(
                mode="car",
                duration_minutes=150,
                estimated_cost_inr=800,
            ),
        ],
        "cheapest",
    )

    assert result["status"] == "COMPLETED"
    assert result["selected"]["mode"] == "car"


def test_balanced_requires_comparable_costs():
    result = rank_options(
        [
            normalize_option(
                mode="bus",
                duration_minutes=180,
                estimated_cost_inr=None,
            ),
            normalize_option(
                mode="bike",
                duration_minutes=150,
                estimated_cost_inr=None,
            ),
        ],
        "balanced",
    )

    assert result["status"] == "UNAVAILABLE"


def test_fastest_can_rank_without_cost():
    result = rank_options(
        [
            normalize_option(
                mode="bus",
                duration_minutes=180,
            ),
            normalize_option(
                mode="car",
                duration_minutes=150,
            ),
        ],
        "fastest",
    )

    assert result["selected"]["mode"] == "car"
