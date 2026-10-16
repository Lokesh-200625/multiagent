from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TransitProvider:
    provider_id: str
    name: str
    mode: str
    source_url: str
    attribution: str


TRANSIT_PROVIDERS: dict[str, TransitProvider] = {
    "tgsrtc": TransitProvider(
        provider_id="tgsrtc",
        name="Telangana State Road Transport Corporation",
        mode="bus",
        source_url="https://tgsrtc.telangana.gov.in/open-data",
        attribution="Contains data provided by TGSRTC",
    ),
    "hmrl": TransitProvider(
        provider_id="hmrl",
        name="Hyderabad Metro Rail Limited",
        mode="metro",
        source_url="https://hmrl.co.in/open-data/",
        attribution=(
            "Contains data provided by "
            "Hyderabad Metro Rail Ltd."
        ),
    ),
}


def get_provider(
    provider_id: str,
) -> TransitProvider:
    try:
        return TRANSIT_PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(
            f"Unknown transit provider: {provider_id}"
        ) from exc