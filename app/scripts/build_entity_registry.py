import json
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"

TEMPLES_DIR = DATA_DIR / "temples"
ACCOMMODATIONS_DIR = DATA_DIR / "accommodations"
RESTAURANTS_DIR = DATA_DIR / "restaurants"
EMERGENCY_DIR = DATA_DIR / "emergency"

TEMPLE_REGISTRY_FILE = DATA_DIR / "temple_registry.json"

OUTPUT_DIR = DATA_DIR / "registry"
OUTPUT_FILE = OUTPUT_DIR / "entity_registry.json"
REPORT_FILE = OUTPUT_DIR / "registry_validation_report.json"


def normalize_text(value: str) -> str:
    """
    Basic deterministic normalization.

    More advanced spelling/fuzzy matching will be added later.
    """
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
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    return " ".join(value.split())


def load_json(path: Path) -> Any:
    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def ensure_list(value: Any) -> list:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


def first_non_empty(
    *values: Any,
) -> Any:

    for value in values:
        if value not in (
            None,
            "",
            [],
            {},
        ):
            return value

    return None


def extract_temple_registry() -> dict[str, dict]:
    """
    Read data/temple_registry.json.

    Supports the registry structure currently used by PilgrimAI.
    """

    if not TEMPLE_REGISTRY_FILE.exists():
        return {}

    data = load_json(
        TEMPLE_REGISTRY_FILE
    )

    if isinstance(data, list):
        records = data

    elif isinstance(data, dict):
        if isinstance(
            data.get("temples"),
            list,
        ):
            records = data["temples"]

        elif isinstance(
            data.get("entities"),
            list,
        ):
            records = data["entities"]

        else:
            records = []

    else:
        records = []

    result = {}

    for record in records:
        if not isinstance(record, dict):
            continue

        temple_id = first_non_empty(
            record.get("temple_id"),
            record.get("templeId"),
            record.get("id"),
        )

        if not temple_id:
            continue

        result[str(temple_id)] = record

    return result


def add_alias(
    aliases: dict[str, set[str]],
    alias: Any,
    entity_id: str,
):
    if not isinstance(alias, str):
        return

    normalized = normalize_text(alias)

    if not normalized:
        return

    aliases[normalized].add(
        entity_id
    )


def add_aliases(
    aliases: dict[str, set[str]],
    values: list[Any],
    entity_id: str,
):
    for value in values:
        if isinstance(value, str):
            add_alias(
                aliases,
                value,
                entity_id,
            )


def build_temple_entities(
    entities: dict[str, dict],
    aliases: dict[str, set[str]],
    report: dict,
):
    registry_data = extract_temple_registry()

    files = sorted(
        TEMPLES_DIR.glob("T*.json")
    )

    report["counts"]["temple_files"] = len(
        files
    )

    for path in files:

        try:
            data = load_json(path)

        except Exception as exc:
            report["invalid_json"].append(
                {
                    "file": str(path.relative_to(PROJECT_ROOT)),
                    "error": str(exc),
                }
            )
            continue

        if not isinstance(data, dict):
            report["invalid_records"].append(
                {
                    "file": str(path.relative_to(PROJECT_ROOT)),
                    "reason": "Root JSON is not an object.",
                }
            )
            continue

        temple_id = first_non_empty(
            data.get("temple_id"),
            data.get("templeId"),
            path.stem,
        )

        temple_id = str(temple_id)

        name = first_non_empty(
            data.get("name"),
            data.get("temple_name"),
            data.get("templeName"),
        )

        if not name:
            name = path.stem

        alternate_names = ensure_list(
            data.get("alternate_names")
        )

        multilingual = data.get(
            "multilingual",
            {},
        )

        if not isinstance(
            multilingual,
            dict,
        ):
            multilingual = {}

        multilingual_names = (
            multilingual.get(
                "names",
                {},
            )
        )

        alternate_spellings = ensure_list(
            multilingual.get(
                "alternate_spellings"
            )
        )

        common_abbreviations = ensure_list(
            multilingual.get(
                "common_abbreviations"
            )
        )

        voice_variants = ensure_list(
            multilingual.get(
                "voice_pronunciation_variants"
            )
        )

        registry_record = registry_data.get(
            temple_id,
            {},
        )

        registry_aliases = ensure_list(
            registry_record.get("aliases")
        )

        all_aliases = []

        all_aliases.extend(
            alternate_names
        )

        if isinstance(
            multilingual_names,
            dict,
        ):
            all_aliases.extend(
                multilingual_names.values()
            )

        all_aliases.extend(
            alternate_spellings
        )

        all_aliases.extend(
            common_abbreviations
        )

        all_aliases.extend(
            voice_variants
        )

        all_aliases.extend(
            registry_aliases
        )

        all_aliases.append(name)

        unique_aliases = []

        seen = set()

        for alias in all_aliases:
            if not isinstance(
                alias,
                str,
            ):
                continue

            normalized = normalize_text(
                alias
            )

            if not normalized:
                continue

            if normalized in seen:
                continue

            seen.add(normalized)
            unique_aliases.append(alias)

            add_alias(
                aliases,
                alias,
                temple_id,
            )

        location = data.get(
            "location",
            {},
        )

        if not isinstance(
            location,
            dict,
        ):
            location = {}

        entities[temple_id] = {
            "entity_id": temple_id,
            "entity_type": "temple",
            "canonical_name": name,
            "aliases": unique_aliases,
            "location": {
                "village": location.get(
                    "village"
                ),
                "city": location.get(
                    "city"
                ),
                "district": location.get(
                    "district"
                ),
                "state": location.get(
                    "state"
                ),
                "country": location.get(
                    "country"
                ),
                "latitude": location.get(
                    "latitude"
                ),
                "longitude": location.get(
                    "longitude"
                ),
            },
            "source_file": str(
                path.relative_to(
                    PROJECT_ROOT
                )
            ),
            "relationships": {
                "accommodations": [],
                "restaurants": [],
                "emergency": False,
            },
        }


def build_accommodation_entities(
    entities: dict[str, dict],
    aliases: dict[str, set[str]],
    report: dict,
):
    files = sorted(
        ACCOMMODATIONS_DIR.glob("H*.json")
    )

    report["counts"][
        "accommodation_files"
    ] = len(files)

    for path in files:

        try:
            data = load_json(path)

        except Exception as exc:
            report["invalid_json"].append(
                {
                    "file": str(path.relative_to(PROJECT_ROOT)),
                    "error": str(exc),
                }
            )
            continue

        if not isinstance(data, dict):
            continue

        hotel_records = data.get(
            "hotels"
        )

        if isinstance(
            hotel_records,
            list,
        ):
            records = hotel_records
        else:
            records = [data]

        file_temple_id = first_non_empty(
            data.get("temple_id"),
            data.get("templeId"),
        )

        for record in records:

            if not isinstance(
                record,
                dict,
            ):
                continue

            hotel_id = first_non_empty(
                record.get("hotel_id"),
                record.get("hotelId"),
            )

            if not hotel_id:
                hotel_id = path.stem

            hotel_id = str(hotel_id)

            name = first_non_empty(
                record.get("hotel_name"),
                record.get("hotelName"),
                record.get("name"),
            )

            if not name:
                name = hotel_id

            temple_id = first_non_empty(
                record.get("temple_id"),
                record.get("templeId"),
                file_temple_id,
            )

            temple_id = (
                str(temple_id)
                if temple_id
                else None
            )

            aliases_for_entity = [
                name
            ]

            aliases_for_entity.extend(
                ensure_list(
                    record.get("aliases")
                )
            )

            unique_aliases = []

            seen = set()

            for alias in aliases_for_entity:
                if not isinstance(
                    alias,
                    str,
                ):
                    continue

                normalized = normalize_text(
                    alias
                )

                if (
                    not normalized
                    or normalized in seen
                ):
                    continue

                seen.add(normalized)
                unique_aliases.append(alias)

                add_alias(
                    aliases,
                    alias,
                    hotel_id,
                )

            entities[hotel_id] = {
                "entity_id": hotel_id,
                "entity_type": "accommodation",
                "canonical_name": name,
                "aliases": unique_aliases,
                "associated_temple_id": temple_id,
                "location": {
                    "address": record.get(
                        "address"
                    ),
                },
                "source_file": str(
                    path.relative_to(
                        PROJECT_ROOT
                    )
                ),
                "relationships": {
                    "temple_id": temple_id,
                },
            }


def build_restaurant_entities(
    entities: dict[str, dict],
    aliases: dict[str, set[str]],
    report: dict,
):
    files = sorted(
        RESTAURANTS_DIR.glob("R*.json")
    )

    report["counts"][
        "restaurant_files"
    ] = len(files)

    for path in files:

        try:
            data = load_json(path)

        except Exception as exc:
            report["invalid_json"].append(
                {
                    "file": str(path.relative_to(PROJECT_ROOT)),
                    "error": str(exc),
                }
            )
            continue

        if not isinstance(data, dict):
            continue

        restaurant_id = first_non_empty(
            data.get("restaurantId"),
            data.get("restaurant_id"),
            path.stem,
        )

        restaurant_id = str(
            restaurant_id
        )

        name = first_non_empty(
            data.get("restaurantName"),
            data.get("restaurant_name"),
            data.get("name"),
        )

        if not name:
            name = restaurant_id

        associated_temple = data.get(
            "associatedTemple",
            {},
        )

        if not isinstance(
            associated_temple,
            dict,
        ):
            associated_temple = {}

        temple_id = first_non_empty(
            associated_temple.get(
                "templeId"
            ),
            associated_temple.get(
                "temple_id"
            ),
        )

        temple_id = (
            str(temple_id)
            if temple_id
            else None
        )

        aliases_for_entity = [
            name
        ]

        aliases_for_entity.extend(
            ensure_list(
                data.get("aliases")
            )
        )

        unique_aliases = []

        seen = set()

        for alias in aliases_for_entity:
            if not isinstance(
                alias,
                str,
            ):
                continue

            normalized = normalize_text(
                alias
            )

            if (
                not normalized
                or normalized in seen
            ):
                continue

            seen.add(normalized)
            unique_aliases.append(alias)

            add_alias(
                aliases,
                alias,
                restaurant_id,
            )

        location = data.get(
            "location",
            {},
        )

        if not isinstance(
            location,
            dict,
        ):
            location = {}

        entities[restaurant_id] = {
            "entity_id": restaurant_id,
            "entity_type": "restaurant",
            "canonical_name": name,
            "aliases": unique_aliases,
            "associated_temple_id": temple_id,
            "location": {
                "address": location.get(
                    "fullAddress"
                ),
                "city": location.get(
                    "city"
                ),
                "district": location.get(
                    "district"
                ),
                "state": location.get(
                    "state"
                ),
            },
            "source_file": str(
                path.relative_to(
                    PROJECT_ROOT
                )
            ),
            "relationships": {
                "temple_id": temple_id,
            },
        }


def build_emergency_relationships(
    entities: dict[str, dict],
    report: dict,
):
    files = sorted(
        EMERGENCY_DIR.glob("*.json")
    )

    report["counts"][
        "emergency_files"
    ] = len(files)

    for path in files:

        try:
            data = load_json(path)

        except Exception as exc:
            report["invalid_json"].append(
                {
                    "file": str(path.relative_to(PROJECT_ROOT)),
                    "error": str(exc),
                }
            )
            continue

        if not isinstance(
            data,
            dict,
        ):
            continue

        temple_id = first_non_empty(
            data.get("temple_id"),
            data.get("templeId"),
        )

        if not temple_id:
            continue

        temple_id = str(
            temple_id
        )

        if temple_id not in entities:
            report["unknown_references"].append(
                {
                    "type": "emergency_temple",
                    "id": temple_id,
                    "file": str(
                        path.relative_to(
                            PROJECT_ROOT
                        )
                    ),
                }
            )
            continue

        entities[temple_id][
            "relationships"
        ]["emergency"] = True


def connect_relationships(
    entities: dict[str, dict],
    report: dict,
):
    for entity in entities.values():

        entity_type = entity.get(
            "entity_type"
        )

        if entity_type not in {
            "accommodation",
            "restaurant",
        }:
            continue

        temple_id = entity.get(
            "associated_temple_id"
        )

        if not temple_id:
            continue

        if temple_id not in entities:
            report["unknown_references"].append(
                {
                    "type": entity_type,
                    "entity_id": entity[
                        "entity_id"
                    ],
                    "temple_id": temple_id,
                    "file": entity[
                        "source_file"
                    ],
                }
            )
            continue

        temple = entities[
            temple_id
        ]

        relationship_list = (
            "accommodations"
            if entity_type
            == "accommodation"
            else "restaurants"
        )

        temple[
            "relationships"
        ][relationship_list].append(
            entity["entity_id"]
        )


def detect_duplicate_ids(
    entities: dict[str, dict],
    report: dict,
):
    counts = defaultdict(int)

    for entity in entities.values():
        counts[
            entity["entity_id"]
        ] += 1

    for entity_id, count in counts.items():
        if count > 1:
            report["duplicate_ids"].append(
                {
                    "entity_id": entity_id,
                    "count": count,
                }
            )


def build_alias_report(
    aliases: dict[str, set[str]],
    entities: dict[str, dict],
    report: dict,
):
    for normalized, entity_ids in sorted(
        aliases.items()
    ):
        if len(entity_ids) <= 1:
            continue

        candidates = []

        for entity_id in sorted(
            entity_ids
        ):
            entity = entities.get(
                entity_id
            )

            if not entity:
                continue

            candidates.append(
                {
                    "entity_id": entity_id,
                    "entity_type": entity[
                        "entity_type"
                    ],
                    "canonical_name": entity[
                        "canonical_name"
                    ],
                }
            )

        report["ambiguous_aliases"].append(
            {
                "normalized_alias": normalized,
                "candidates": candidates,
            }
        )


def main():
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    entities: dict[str, dict] = {}

    aliases: dict[
        str,
        set[str],
    ] = defaultdict(set)

    report = {
        "counts": {
            "temple_files": 0,
            "accommodation_files": 0,
            "restaurant_files": 0,
            "emergency_files": 0,
            "entities": 0,
            "aliases": 0,
        },
        "duplicate_ids": [],
        "ambiguous_aliases": [],
        "unknown_references": [],
        "invalid_json": [],
        "invalid_records": [],
    }

    print()
    print(
        "=========================================="
    )
    print(
        "PilgrimAI Entity Registry Builder"
    )
    print(
        "=========================================="
    )
    print()

    print("Reading temples...")
    build_temple_entities(
        entities,
        aliases,
        report,
    )

    print("Reading accommodations...")
    build_accommodation_entities(
        entities,
        aliases,
        report,
    )

    print("Reading restaurants...")
    build_restaurant_entities(
        entities,
        aliases,
        report,
    )

    print("Reading emergency data...")
    build_emergency_relationships(
        entities,
        report,
    )

    print("Connecting relationships...")
    connect_relationships(
        entities,
        report,
    )

    print("Checking duplicate IDs...")
    detect_duplicate_ids(
        entities,
        report,
    )

    print("Checking ambiguous aliases...")
    build_alias_report(
        aliases,
        entities,
        report,
    )

    report["counts"]["entities"] = len(
        entities
    )

    report["counts"]["aliases"] = len(
        aliases
    )

    registry = {
        "metadata": {
            "version": "1.0",
            "generated_by": "PilgrimAI Entity Registry Builder",
        },
        "entities": entities,
        "aliases": {
            alias: sorted(
                entity_ids
            )
            for alias, entity_ids
            in sorted(
                aliases.items()
            )
        },
    }

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            registry,
            file,
            ensure_ascii=False,
            indent=2,
        )

    with REPORT_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print()
    print(
        "=========================================="
    )
    print("BUILD COMPLETE")
    print(
        "=========================================="
    )

    print(
        f"Temples:        "
        f"{report['counts']['temple_files']}"
    )

    print(
        f"Accommodations: "
        f"{report['counts']['accommodation_files']}"
    )

    print(
        f"Restaurants:    "
        f"{report['counts']['restaurant_files']}"
    )

    print(
        f"Emergency:      "
        f"{report['counts']['emergency_files']}"
    )

    print(
        f"Total entities: "
        f"{report['counts']['entities']}"
    )

    print(
        f"Total aliases:  "
        f"{report['counts']['aliases']}"
    )

    print()
    print(
        f"Duplicate IDs:  "
        f"{len(report['duplicate_ids'])}"
    )

    print(
        f"Ambiguous aliases: "
        f"{len(report['ambiguous_aliases'])}"
    )

    print(
        f"Unknown references: "
        f"{len(report['unknown_references'])}"
    )

    print(
        f"Invalid JSON: "
        f"{len(report['invalid_json'])}"
    )

    print()
    print(
        f"Registry: {OUTPUT_FILE}"
    )

    print(
        f"Report:   {REPORT_FILE}"
    )

    print()


if __name__ == "__main__":
    main()