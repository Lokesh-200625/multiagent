from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

REGISTRY_FILE = (
    PROJECT_ROOT
    / "data"
    / "registry"
    / "entity_registry.json"
)


class EntityRegistryError(RuntimeError):
    pass


class EntityRegistry:
    """
    Deterministic entity registry and resolver.

    Responsibilities:
    - exact alias lookup
    - entity-type filtering
    - fuzzy spelling matching
    - contextual candidate scoring
    - ambiguity detection

    It does NOT use an LLM.
    """

    FUZZY_THRESHOLD = 0.88

    def __init__(
        self,
        registry_file: Path | None = None,
    ):
        self.registry_file = (
            registry_file or REGISTRY_FILE
        )

        self._entities: dict[
            str,
            dict[str, Any],
        ] = {}

        self._aliases: dict[
            str,
            list[str],
        ] = {}

        self.load()

    # ---------------------------------------------------------
    # NORMALIZATION
    # ---------------------------------------------------------

    @staticmethod
    def normalize(
        value: str,
    ) -> str:

        if not isinstance(
            value,
            str,
        ):
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
        }

        for old, new in replacements.items():
            value = value.replace(
                old,
                new,
            )

        return " ".join(
            value.split()
        )

    # ---------------------------------------------------------
    # LOAD
    # ---------------------------------------------------------

    def load(self) -> None:

        if not self.registry_file.exists():
            raise EntityRegistryError(
                "Entity registry not found: "
                f"{self.registry_file}"
            )

        try:
            import json

            with self.registry_file.open(
                "r",
                encoding="utf-8",
            ) as file:
                data = json.load(file)

        except Exception as exc:
            raise EntityRegistryError(
                "Unable to load entity registry"
            ) from exc

        self._entities = data.get(
            "entities",
            {},
        )

        self._aliases = data.get(
            "aliases",
            {},
        )

    # ---------------------------------------------------------
    # BASIC ACCESS
    # ---------------------------------------------------------

    def get(
        self,
        entity_id: str,
    ) -> dict[str, Any] | None:

        return self._entities.get(
            entity_id
        )

    def all_entities(
        self,
    ) -> list[dict[str, Any]]:

        return list(
            self._entities.values()
        )

    # ---------------------------------------------------------
    # EXACT LOOKUP
    # ---------------------------------------------------------

    def find_exact(
        self,
        mention: str,
        entity_type: str | None = None,
    ) -> list[dict[str, Any]]:

        normalized = self.normalize(
            mention
        )

        entity_ids = self._aliases.get(
            normalized,
            [],
        )

        results = []

        for entity_id in entity_ids:

            entity = self._entities.get(
                entity_id
            )

            if not entity:
                continue

            if (
                entity_type
                and entity.get(
                    "entity_type"
                )
                != entity_type
            ):
                continue

            results.append(entity)

        return results

    # ---------------------------------------------------------
    # FUZZY LOOKUP
    # ---------------------------------------------------------

    def find_fuzzy(
        self,
        mention: str,
        entity_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:

        normalized_mention = self.normalize(
            mention
        )

        if not normalized_mention:
            return []

        candidates = []

        for alias, entity_ids in (
            self._aliases.items()
        ):

            similarity = SequenceMatcher(
                None,
                normalized_mention,
                alias,
            ).ratio()

            if (
                similarity
                < self.FUZZY_THRESHOLD
            ):
                continue

            for entity_id in entity_ids:

                entity = self._entities.get(
                    entity_id
                )

                if not entity:
                    continue

                if (
                    entity_type
                    and entity.get(
                        "entity_type"
                    )
                    != entity_type
                ):
                    continue

                candidates.append(
                    (
                        similarity,
                        entity,
                    )
                )

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        results = []

        seen_ids = set()

        for similarity, entity in candidates:

            entity_id = entity[
                "entity_id"
            ]

            if entity_id in seen_ids:
                continue

            seen_ids.add(entity_id)

            result = dict(entity)

            result[
                "_match_score"
            ] = round(
                similarity,
                4,
            )

            results.append(result)

            if len(results) >= limit:
                break

        return results

    # ---------------------------------------------------------
    # CONTEXT SCORING
    # ---------------------------------------------------------

    def _context_score(
        self,
        entity: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> int:

        if not context:
            return 0

        score = 0

        entity_id = entity.get(
            "entity_id"
        )

        entity_type = entity.get(
            "entity_type"
        )

        # -----------------------------------------------------
        # Direct entity ID context
        # -----------------------------------------------------

        referenced_entity_id = context.get(
            "entity_id"
        )

        if (
            referenced_entity_id
            and referenced_entity_id
            == entity_id
        ):
            score += 100

        # -----------------------------------------------------
        # Temple context
        # -----------------------------------------------------

        context_temple_id = context.get(
            "temple_id"
        )

        if context_temple_id:

            if entity_type == "temple":

                if (
                    entity_id
                    == context_temple_id
                ):
                    score += 100

            associated_temple_id = (
                entity.get(
                    "associated_temple_id"
                )
            )

            if (
                associated_temple_id
                == context_temple_id
            ):
                score += 80

            relationships = entity.get(
                "relationships",
                {},
            )

            if (
                relationships.get(
                    "temple_id"
                )
                == context_temple_id
            ):
                score += 80

        # -----------------------------------------------------
        # City / location context
        # -----------------------------------------------------

        context_city = context.get(
            "city"
        )

        if context_city:

            normalized_context_city = (
                self.normalize(
                    str(context_city)
                )
            )

            location = entity.get(
                "location",
                {},
            )

            if isinstance(
                location,
                dict,
            ):

                entity_city = location.get(
                    "city"
                )

                if entity_city:

                    if (
                        self.normalize(
                            str(entity_city)
                        )
                        == normalized_context_city
                    ):
                        score += 40

        # -----------------------------------------------------
        # District context
        # -----------------------------------------------------

        context_district = context.get(
            "district"
        )

        if context_district:

            normalized_context_district = (
                self.normalize(
                    str(context_district)
                )
            )

            location = entity.get(
                "location",
                {},
            )

            if isinstance(
                location,
                dict,
            ):

                entity_district = (
                    location.get(
                        "district"
                    )
                )

                if entity_district:

                    if (
                        self.normalize(
                            str(entity_district)
                        )
                        == normalized_context_district
                    ):
                        score += 25

        return score

    # ---------------------------------------------------------
    # RESOLVE
    # ---------------------------------------------------------

    def resolve(
        self,
        mention: str,
        entity_type: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        if not mention.strip():
            return {
                "mention": mention,
                "status": "UNRESOLVED",
                "entity_id": None,
                "entity": None,
                "candidates": [],
            }

        # =====================================================
        # STEP 1 — EXACT MATCH
        # =====================================================

        exact_matches = self.find_exact(
            mention=mention,
            entity_type=entity_type,
        )

        if exact_matches:

            scored = []

            for entity in exact_matches:

                score = self._context_score(
                    entity,
                    context,
                )

                scored.append(
                    (
                        score,
                        entity,
                    )
                )

            scored.sort(
                key=lambda item: item[0],
                reverse=True,
            )

            best_score = scored[0][0]

            best_candidates = [
                entity
                for score, entity in scored
                if score == best_score
            ]

            # One exact entity.
            if len(best_candidates) == 1:

                return {
                    "mention": mention,
                    "status": "RESOLVED",
                    "entity_id": best_candidates[
                        0
                    ]["entity_id"],
                    "entity": best_candidates[
                        0
                    ],
                    "match_type": "exact",
                    "match_score": 1.0,
                    "candidates": [
                        entity[
                            "entity_id"
                        ]
                        for _, entity
                        in scored
                    ],
                }

            # Multiple entities but context selected one.
            if best_score > 0:

                return {
                    "mention": mention,
                    "status": "RESOLVED",
                    "entity_id": best_candidates[
                        0
                    ]["entity_id"],
                    "entity": best_candidates[
                        0
                    ],
                    "match_type": "exact_context",
                    "match_score": 1.0,
                    "context_score": best_score,
                    "candidates": [
                        entity[
                            "entity_id"
                        ]
                        for _, entity
                        in scored
                    ],
                }

            # Genuine ambiguity.
            return {
                "mention": mention,
                "status": "AMBIGUOUS",
                "entity_id": None,
                "entity": None,
                "match_type": "exact_ambiguous",
                "match_score": 1.0,
                "candidates": [
                    {
                        "entity_id": entity[
                            "entity_id"
                        ],
                        "entity_type": entity[
                            "entity_type"
                        ],
                        "canonical_name": entity[
                            "canonical_name"
                        ],
                    }
                    for entity in exact_matches
                ],
            }

        # =====================================================
        # STEP 2 — FUZZY MATCH
        # =====================================================

        fuzzy_matches = self.find_fuzzy(
            mention=mention,
            entity_type=entity_type,
        )

        if not fuzzy_matches:

            return {
                "mention": mention,
                "status": "UNRESOLVED",
                "entity_id": None,
                "entity": None,
                "match_type": "none",
                "match_score": 0.0,
                "candidates": [],
            }

        scored = []

        for entity in fuzzy_matches:

            fuzzy_score = entity.get(
                "_match_score",
                0.0,
            )

            context_score = (
                self._context_score(
                    entity,
                    context,
                )
            )

            scored.append(
                (
                    context_score,
                    fuzzy_score,
                    entity,
                )
            )

        scored.sort(
            key=lambda item: (
                item[0],
                item[1],
            ),
            reverse=True,
        )

        best_context_score = scored[0][0]
        best_fuzzy_score = scored[0][1]

        # Strong contextual match.
        if best_context_score > 0:

            top = [
                item
                for item in scored
                if item[0]
                == best_context_score
            ]

            if len(top) == 1:

                entity = top[0][2]

                return {
                    "mention": mention,
                    "status": "RESOLVED",
                    "entity_id": entity[
                        "entity_id"
                    ],
                    "entity": entity,
                    "match_type": "fuzzy_context",
                    "match_score": best_fuzzy_score,
                    "context_score": best_context_score,
                    "candidates": [
                        item[2]["entity_id"]
                        for item in scored
                    ],
                }

        # Strong unique fuzzy match.
        top_fuzzy = [
            item
            for item in scored
            if item[1]
            == best_fuzzy_score
        ]

        if len(top_fuzzy) == 1:

            entity = top_fuzzy[0][2]

            return {
                "mention": mention,
                "status": "RESOLVED",
                "entity_id": entity[
                    "entity_id"
                ],
                "entity": entity,
                "match_type": "fuzzy",
                "match_score": best_fuzzy_score,
                "context_score": best_context_score,
                "candidates": [
                    item[2]["entity_id"]
                    for item in scored
                ],
            }

        # Otherwise do not guess.
        return {
            "mention": mention,
            "status": "AMBIGUOUS",
            "entity_id": None,
            "entity": None,
            "match_type": "fuzzy_ambiguous",
            "match_score": best_fuzzy_score,
            "context_score": best_context_score,
            "candidates": [
                {
                    "entity_id": item[2][
                        "entity_id"
                    ],
                    "entity_type": item[2][
                        "entity_type"
                    ],
                    "canonical_name": item[2][
                        "canonical_name"
                    ],
                    "match_score": item[1],
                }
                for item in scored
            ],
        }


_registry: EntityRegistry | None = None


def get_entity_registry() -> EntityRegistry:

    global _registry

    if _registry is None:
        _registry = EntityRegistry()

    return _registry