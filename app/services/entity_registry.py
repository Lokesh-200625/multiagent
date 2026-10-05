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
    - exact canonical/alias lookup
    - entity-type filtering
    - fuzzy spelling matching
    - contextual candidate scoring
    - relationship/semantic mention resolution
    - ambiguity detection

    It does NOT use an LLM.
    """

    FUZZY_THRESHOLD = 0.88
    RELATED_FUZZY_THRESHOLD = 0.82

    RELATED_TYPE_COMPATIBILITY = {
        "person": {
            "person",
            "topic",
            "associated_person",
            "historical_person",
        },
        "festival": {
            "festival",
            "topic",
            "query_variation",
        },
        "event": {
            "festival",
            "topic",
            "query_variation",
        },
        "topic": {
            "topic",
            "festival",
            "query_variation",
        },
        "landmark": {
            "landmark",
            "place",
            "festival",
            "event",
            "topic",
            "query_variation",
        },
    }

    def __init__(
        self,
        registry_file: Path | None = None,
    ):
        self.registry_file = registry_file or REGISTRY_FILE

        self._entities: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, list[str]] = {}
        self._related_mentions: dict[
            str,
            list[dict[str, Any]],
        ] = {}

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

        if not isinstance(data, dict):
            raise EntityRegistryError(
                "Entity registry root must be an object"
            )

        self._entities = data.get("entities", {})
        self._aliases = data.get("aliases", {})
        self._related_mentions = data.get(
            "related_mentions",
            {},
        )

    def get(
        self,
        entity_id: str,
    ) -> dict[str, Any] | None:
        return self._entities.get(entity_id)

    def all_entities(self) -> list[dict[str, Any]]:
        return list(self._entities.values())

    def find_exact(
        self,
        mention: str,
        entity_type: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized = self.normalize(mention)

        entity_ids = self._aliases.get(
            normalized,
            [],
        )

        results = []

        for entity_id in entity_ids:
            entity = self._entities.get(entity_id)

            if not entity:
                continue

            if (
                entity_type
                and entity.get("entity_type") != entity_type
            ):
                continue

            results.append(entity)

        return results

    def find_fuzzy(
        self,
        mention: str,
        entity_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_mention = self.normalize(mention)

        if not normalized_mention:
            return []

        candidates = []

        for alias, entity_ids in self._aliases.items():
            similarity = SequenceMatcher(
                None,
                normalized_mention,
                alias,
            ).ratio()

            if similarity < self.FUZZY_THRESHOLD:
                continue

            for entity_id in entity_ids:
                entity = self._entities.get(entity_id)

                if not entity:
                    continue

                if (
                    entity_type
                    and entity.get("entity_type") != entity_type
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
            entity_id = entity["entity_id"]

            if entity_id in seen_ids:
                continue

            seen_ids.add(entity_id)

            result = dict(entity)
            result["_match_score"] = round(
                similarity,
                4,
            )

            results.append(result)

            if len(results) >= limit:
                break

        return results

    def _related_type_matches(
        self,
        requested_type: str | None,
        record: dict[str, Any],
    ) -> bool:
        """
        Validate a related mention.

        The mention_type describes what the source text called the
        mention. It does NOT necessarily describe the canonical
        entity being resolved.

        Example:

            Ramadasu
              mention_type = person
              relation = associated_person
              target_entity_id = T0002
              target entity_type = temple

        Example:

            Komuravelli Jathara
              mention_type = festival
              relation = festival
              target_entity_id = T0015
              target entity_type = temple

        Therefore both the source mention semantics and the
        canonical target type are considered.
        """

        if not requested_type:
            return True

        requested_type = (
            str(requested_type)
            .lower()
            .strip()
        )

        mention_type = str(
            record.get("mention_type", "")
        ).lower().strip()

        relation = str(
            record.get("relation", "")
        ).lower().strip()

        allowed = self.RELATED_TYPE_COMPATIBILITY.get(
            requested_type,
            {requested_type},
        )

        # Existing semantic relationship compatibility.
        if (
            mention_type in allowed
            or relation in allowed
        ):
            return True

        # IMPORTANT:
        # A related mention may have a different source type from
        # the canonical entity it points to.
        target_id = record.get(
            "target_entity_id"
        )

        target = self._entities.get(target_id)

        if target:
            target_type = str(
                target.get("entity_type", "")
            ).lower().strip()

            if target_type == requested_type:
                return True

        return False

    def find_related_exact(
        self,
        mention: str,
        mention_type: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized = self.normalize(mention)

        records = self._related_mentions.get(
            normalized,
            [],
        )

        return [
            record
            for record in records
            if self._related_type_matches(
                mention_type,
                record,
            )
        ]

    def find_related_fuzzy(
        self,
        mention: str,
        mention_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_mention = self.normalize(mention)

        if not normalized_mention:
            return []

        candidates = []

        for alias, records in self._related_mentions.items():
            if not records:
                continue

            similarity = SequenceMatcher(
                None,
                normalized_mention,
                alias,
            ).ratio()

            if similarity < self.RELATED_FUZZY_THRESHOLD:
                continue

            for record in records:
                if not self._related_type_matches(
                    mention_type,
                    record,
                ):
                    continue

                candidates.append(
                    (
                        similarity,
                        record,
                    )
                )

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        results = []
        seen = set()

        for similarity, record in candidates:
            key = (
                record.get("target_entity_id"),
                record.get("relation"),
                record.get("mention"),
            )

            if key in seen:
                continue

            seen.add(key)

            result = dict(record)
            result["_match_score"] = round(
                similarity,
                4,
            )

            results.append(result)

            if len(results) >= limit:
                break

        return results

    def _resolve_related(
        self,
        mention: str,
        entity_type: str | None = None,
    ) -> dict[str, Any]:
        exact = self.find_related_exact(
            mention=mention,
            mention_type=entity_type,
        )

        if exact:
            valid = []

            for record in exact:
                target_id = record.get(
                    "target_entity_id"
                )

                target = self._entities.get(
                    target_id
                )

                if target:
                    valid.append(
                        (
                            record,
                            target,
                        )
                    )

            if valid:
                target_ids = {
                    target["entity_id"]
                    for _, target in valid
                }

                if len(target_ids) == 1:
                    record, target = valid[0]

                    return {
                        "mention": mention,
                        "status": "RESOLVED",
                        "entity_id": target["entity_id"],
                        "entity": target,
                        "match_type": "related_exact",
                        "match_score": 1.0,
                        "relation": record.get(
                            "relation"
                        ),
                        "mention_type": record.get(
                            "mention_type"
                        ),
                        "source_file": record.get(
                            "source_file"
                        ),
                        "source_field": record.get(
                            "source_field"
                        ),
                        "source_section": record.get(
                            "source_section"
                        ),
                        "candidates": sorted(
                            target_ids
                        ),
                    }

                return {
                    "mention": mention,
                    "status": "AMBIGUOUS",
                    "entity_id": None,
                    "entity": None,
                    "match_type": "related_exact_ambiguous",
                    "match_score": 1.0,
                    "candidates": [
                        {
                            "entity_id": target["entity_id"],
                            "entity_type": target[
                                "entity_type"
                            ],
                            "canonical_name": target[
                                "canonical_name"
                            ],
                        }
                        for _, target in valid
                    ],
                }

        fuzzy = self.find_related_fuzzy(
            mention=mention,
            mention_type=entity_type,
        )

        if not fuzzy:
            return {
                "mention": mention,
                "status": "UNRESOLVED",
                "entity_id": None,
                "entity": None,
                "match_type": "related_none",
                "match_score": 0.0,
                "candidates": [],
            }

        best_score = fuzzy[0]["_match_score"]

        top = [
            item
            for item in fuzzy
            if item["_match_score"] == best_score
        ]

        target_ids = {
            item.get("target_entity_id")
            for item in top
            if item.get("target_entity_id")
            in self._entities
        }

        if len(target_ids) == 1:
            target_id = next(iter(target_ids))
            target = self._entities[target_id]

            record = next(
                item
                for item in top
                if item.get("target_entity_id")
                == target_id
            )

            return {
                "mention": mention,
                "status": "RESOLVED",
                "entity_id": target_id,
                "entity": target,
                "match_type": "related_fuzzy",
                "match_score": best_score,
                "relation": record.get("relation"),
                "mention_type": record.get(
                    "mention_type"
                ),
                "source_file": record.get(
                    "source_file"
                ),
                "source_field": record.get(
                    "source_field"
                ),
                "source_section": record.get(
                    "source_section"
                ),
                "candidates": [target_id],
            }

        candidates = []

        for item in top:
            target_id = item.get(
                "target_entity_id"
            )

            target = self._entities.get(
                target_id
            )

            if not target:
                continue

            candidates.append(
                {
                    "entity_id": target_id,
                    "entity_type": target[
                        "entity_type"
                    ],
                    "canonical_name": target[
                        "canonical_name"
                    ],
                    "match_score": item.get(
                        "_match_score",
                        0.0,
                    ),
                }
            )

        if candidates:
            return {
                "mention": mention,
                "status": "AMBIGUOUS",
                "entity_id": None,
                "entity": None,
                "match_type": "related_fuzzy_ambiguous",
                "match_score": best_score,
                "candidates": candidates,
            }

        return {
            "mention": mention,
            "status": "UNRESOLVED",
            "entity_id": None,
            "entity": None,
            "match_type": "related_none",
            "match_score": 0.0,
            "candidates": [],
        }

    def _context_score(
        self,
        entity: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> int:
        if not context:
            return 0

        score = 0

        entity_id = entity.get("entity_id")
        entity_type = entity.get("entity_type")

        referenced_entity_id = context.get(
            "entity_id"
        )

        if (
            referenced_entity_id
            and referenced_entity_id == entity_id
        ):
            score += 100

        context_temple_id = context.get(
            "temple_id"
        )

        if context_temple_id:
            if entity_type == "temple":
                if entity_id == context_temple_id:
                    score += 100

            associated_temple_id = entity.get(
                "associated_temple_id"
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
                relationships.get("temple_id")
                == context_temple_id
            ):
                score += 80

        context_city = context.get("city")

        if context_city:
            normalized_context_city = self.normalize(
                str(context_city)
            )

            location = entity.get(
                "location",
                {},
            )

            if isinstance(location, dict):
                entity_city = location.get("city")

                if entity_city:
                    if (
                        self.normalize(
                            str(entity_city)
                        )
                        == normalized_context_city
                    ):
                        score += 40

        context_district = context.get(
            "district"
        )

        if context_district:
            normalized_context_district = self.normalize(
                str(context_district)
            )

            location = entity.get(
                "location",
                {},
            )

            if isinstance(location, dict):
                entity_district = location.get(
                    "district"
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

    def resolve(
        self,
        mention: str,
        entity_type: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(mention, str) or not mention.strip():
            return {
                "mention": mention,
                "status": "UNRESOLVED",
                "entity_id": None,
                "entity": None,
                "candidates": [],
            }

        exact_matches = self.find_exact(
            mention=mention,
            entity_type=entity_type,
        )

        if exact_matches:
            scored = [
                (
                    self._context_score(
                        entity,
                        context,
                    ),
                    entity,
                )
                for entity in exact_matches
            ]

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

            if len(best_candidates) == 1:
                return {
                    "mention": mention,
                    "status": "RESOLVED",
                    "entity_id": best_candidates[0][
                        "entity_id"
                    ],
                    "entity": best_candidates[0],
                    "match_type": (
                        "exact_context"
                        if best_score > 0
                        else "exact"
                    ),
                    "match_score": 1.0,
                    "context_score": best_score,
                    "candidates": [
                        entity["entity_id"]
                        for _, entity in scored
                    ],
                }

            return {
                "mention": mention,
                "status": "AMBIGUOUS",
                "entity_id": None,
                "entity": None,
                "match_type": "exact_ambiguous",
                "match_score": 1.0,
                "candidates": [
                    {
                        "entity_id": entity["entity_id"],
                        "entity_type": entity["entity_type"],
                        "canonical_name": entity[
                            "canonical_name"
                        ],
                    }
                    for entity in exact_matches
                ],
            }

        fuzzy_matches = self.find_fuzzy(
            mention=mention,
            entity_type=entity_type,
        )

        if fuzzy_matches:
            scored = []

            for entity in fuzzy_matches:
                scored.append(
                    (
                        self._context_score(
                            entity,
                            context,
                        ),
                        entity.get(
                            "_match_score",
                            0.0,
                        ),
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

            if best_context_score > 0:
                top = [
                    item
                    for item in scored
                    if item[0] == best_context_score
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

            top_fuzzy = [
                item
                for item in scored
                if item[1] == best_fuzzy_score
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
                        "entity_id": item[2]["entity_id"],
                        "entity_type": item[2]["entity_type"],
                        "canonical_name": item[2][
                            "canonical_name"
                        ],
                        "match_score": item[1],
                    }
                    for item in scored
                ],
            }

        return self._resolve_related(
            mention=mention,
            entity_type=entity_type,
        )


_registry: EntityRegistry | None = None


def get_entity_registry() -> EntityRegistry:
    global _registry

    if _registry is None:
        _registry = EntityRegistry()

    return _registry