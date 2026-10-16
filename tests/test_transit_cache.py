
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from app.travel.transit.cache import get_dataset, get_metadata, set_dataset
from app.travel.transit.schemas import TransitDataset, TransitFeedMetadata
from app.travel.transit import query_service


def test_transit_dataset_cache():
    dataset = TransitDataset(
        metadata=TransitFeedMetadata(
            provider_id="tgsrtc",
            fetched_at=datetime.now(timezone.utc),
            source_url="https://example.com/tgsrtc.zip",
            static=True,
        )
    )

    set_dataset("tgsrtc", dataset)

    cached = get_dataset("tgsrtc")
    metadata = get_metadata("tgsrtc")

    assert cached is not None
    assert cached.metadata.provider_id == "tgsrtc"
    assert cached.metadata.source_url == "https://example.com/tgsrtc.zip"

    assert metadata is not None
    assert metadata["provider_id"] == "tgsrtc"


def test_cache_key_changes_when_feed_version_changes():
    arguments = {
        "provider_id": "tgsrtc",
        "query": "Hyderabad",
        "limit": 10,
    }

    old_key = query_service._cache_key(
        "find-stops",
        arguments,
        "feed-hash-old",
    )
    new_key = query_service._cache_key(
        "find-stops",
        arguments,
        "feed-hash-new",
    )

    assert old_key != new_key
    assert "feed-hash-old" in old_key
    assert "feed-hash-new" in new_key


def test_cache_miss_returns_json_compatible_values(monkeypatch):
    monkeypatch.setattr(
        query_service.repository,
        "get_active_feed",
        lambda provider_id: {
            "id": "feed-1",
            "content_sha256": "hash-1",
        },
    )

    monkeypatch.setattr(
        query_service.redis_client,
        "get",
        lambda key: None,
    )

    monkeypatch.setattr(
        query_service.redis_client,
        "set",
        lambda *args, **kwargs: True,
    )

    result = query_service._cached(
        "test-operation",
        {"provider_id": "tgsrtc"},
        lambda: {
            "feed_id": "feed-1",
            "service_date": date(2026, 10, 10),
            "fetched_at": datetime(
                2026, 10, 10, tzinfo=timezone.utc
            ),
        },
    )

    assert result["feed_id"] == "feed-1"
    assert result["service_date"] == "2026-10-10"
    assert result["fetched_at"] == "2026-10-10T00:00:00+00:00"


def test_cache_hit_returns_decoded_json(monkeypatch):
    monkeypatch.setattr(
        query_service.repository,
        "get_active_feed",
        lambda provider_id: {
            "id": "feed-1",
            "content_sha256": "hash-1",
        },
    )

    monkeypatch.setattr(
        query_service.redis_client,
        "get",
        lambda key: (
            '{"feed_id":"feed-1",'
            '"service_date":"2026-10-10"}'
        ),
    )

    loader = MagicMock(
        return_value={"feed_id": "should-not-be-used"}
    )

    result = query_service._cached(
        "test-operation",
        {"provider_id": "tgsrtc"},
        loader,
    )

    assert result == {
        "feed_id": "feed-1",
        "service_date": "2026-10-10",
    }
    loader.assert_not_called()
