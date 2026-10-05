from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

GEOGRAPHY_FILE = (
    PROJECT_ROOT
    / "data"
    / "geography"
    / "places.json"
)


class GeographyRegistryError(RuntimeError):
    """Raised when the geographic registry cannot be loaded."""


class GeographyRegistry:
    """Deterministic registry for geographic places."""

    FUZZY_THRESHOLD = 0.90

    def __init__(
        self,
        registry_file: Path | None = None,
    ):
        self.registry_file = (
            registry_file or GEOGRAPHY_FILE
        )

        self._places: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, list[str]] = {}

        self.load()

    @staticmethod
    def normalize(value: str) -> str:
        if not isinstance(value, str):
            return ""

        value = value.strip().lower()

        replacements = {
            "-": " ",
            "_": " ",
            ",": " ",
            ".": " ",
            "/": " ",
            "\\": " ",
            "(": " ",
            ")": " ",
            "'": "",
            '"': "",
            ":": " ",
            ";": " ",
        }

        for old, new in replacements.items():
            value = value.replace(old, new)

        return " ".join(value.split())

    def load(self) -> None:
        if not self.registry_file.exists():
            raise GeographyRegistryError(
                "Geography registry not found: "
                f"{self.registry_file}"
            )

        try:
            with self.registry_file.open(
                "r",
                encoding="utf-8",
            ) as file:
                data = json.load(file)

        except Exception as exc:
            raise GeographyRegistryError(
                "Unable to load geography registry"
            ) from exc

        if not isinstance(data, dict):
            raise GeographyRegistryError(
                "Geography registry root must be an object"
            )

        places = data.get("places", [])

        if not isinstance(places, list):
            raise GeographyRegistryError(
                "Geography registry 'places' must be a list"
            )

        for place in places:
            if not isinstance(place, dict):
                continue

            place_id = place.get("place_id")
            canonical_name = place.get(
                "canonical_name"
            )

            if not place_id or not canonical_name:
                continue

            place_id = str(place_id)

            self._places[place_id] = place

            aliases = [canonical_name]
            aliases.extend(
                place.get("aliases", [])
            )

            for alias in aliases:
                if not isinstance(alias, str):
                    continue

                normalized = self.normalize(alias)

                if not normalized:
                    continue

                existing = self._aliases.setdefault(
                    normalized,
                    [],
                )

                # Prevent the same place from being inserted
                # multiple times for the same normalized alias.
                if place_id not in existing:
                    existing.append(place_id)

    def resolve(
        self,
        mention: str,
    ) -> dict[str, Any] | None:
        normalized = self.normalize(mention)

        if not normalized:
            return None

        exact_ids = self._aliases.get(
            normalized,
            [],
        )

        # Exact alias with one unique place.
        if len(exact_ids) == 1:
            return self._places.get(
                exact_ids[0]
            )

        # Multiple genuinely different places share
        # the same alias. Do not guess.
        if len(exact_ids) > 1:
            return None

        candidates = []

        for alias, place_ids in self._aliases.items():
            score = SequenceMatcher(
                None,
                normalized,
                alias,
            ).ratio()

            if score < self.FUZZY_THRESHOLD:
                continue

            for place_id in place_ids:
                place = self._places.get(place_id)

                if place is not None:
                    candidates.append(
                        (
                            score,
                            place_id,
                            place,
                        )
                    )

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        best_score = candidates[0][0]

        best = [
            item
            for item in candidates
            if item[0] == best_score
        ]

        # Multiple different places with the same
        # best fuzzy score are ambiguous.
        best_place_ids = {
            item[1]
            for item in best
        }

        if len(best_place_ids) != 1:
            return None

        return best[0][2]


_registry: GeographyRegistry | None = None


def get_geography_registry() -> GeographyRegistry:
    global _registry

    if _registry is None:
        _registry = GeographyRegistry()

    return _registry