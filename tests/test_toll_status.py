from app.travel.providers.alternatives import build_toll_status


def test_toll_data_defaults_to_unavailable():
    result = build_toll_status(provider="osrm")

    assert result["status"] == "UNAVAILABLE"
    assert result["amount_inr"] is None
    assert result["verified"] is False
    assert result["toll_free"] is None
    assert "does not mean the route is toll-free" in result["message"]


def test_unverified_toll_amount_is_not_reported():
    result = build_toll_status(
        provider="example",
        toll_data={
            "verified": False,
            "amount_inr": 250,
            "toll_free": False,
        },
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["amount_inr"] is None


def test_verified_toll_amount_is_preserved():
    result = build_toll_status(
        provider="example",
        toll_data={
            "verified": True,
            "amount_inr": 250,
            "toll_free": False,
        },
    )

    assert result["status"] == "AVAILABLE"
    assert result["amount_inr"] == 250
    assert result["verified"] is True


def test_negative_toll_amount_is_rejected():
    result = build_toll_status(
        provider="example",
        toll_data={
            "verified": True,
            "amount_inr": -10,
        },
    )

    assert result["status"] == "UNAVAILABLE"
