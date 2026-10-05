import json
import re

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


# =============================================================
# NORMALIZATION
# =============================================================


def normalize_text(value: str) -> str:
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


# =============================================================
# BASIC HELPERS
# =============================================================


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


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (
            None,
            "",
            [],
            {},
        ):
            return value

    return None


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

    aliases[normalized].add(entity_id)


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


# =============================================================
# RELATED MENTION INDEX
# =============================================================


def add_related_mention(
    related_mentions: dict[str, list[dict]],
    mention: Any,
    *,
    mention_type: str,
    relation: str,
    target_entity_id: str,
    source_file: str,
    source_field: str,
    source_section: str | None = None,
):
    """
    Add a deterministic semantic/relationship mention.

    Example:

        Kancharla Gopanna (Bhakta Ramadasu)
            -> person
            -> associated_person
            -> T0002

        Ramadasu
            -> person
            -> associated_person
            -> T0002

        Komuravelli Mallanna Jathara
            -> festival
            -> festival
            -> T0015

        Komuravelli Jathara
            -> festival
            -> festival
            -> T0015
    """

    if not isinstance(mention, str):
        return

    mention = mention.strip()

    if not mention:
        return

    normalized = normalize_text(mention)

    if not normalized:
        return

    record = {
        "mention": mention,
        "mention_type": mention_type,
        "relation": relation,
        "target_entity_id": target_entity_id,
        "source_file": source_file,
        "source_field": source_field,
        "source_section": source_section,
    }

    existing = related_mentions[normalized]

    identity = (
        record["target_entity_id"],
        record["relation"],
        record["mention_type"],
        record["source_file"],
        record["source_field"],
        record["source_section"],
    )

    for item in existing:
        existing_identity = (
            item.get("target_entity_id"),
            item.get("relation"),
            item.get("mention_type"),
            item.get("source_file"),
            item.get("source_field"),
            item.get("source_section"),
        )

        if existing_identity == identity:
            return

    existing.append(record)


def _clean_named_phrase(value: str) -> str:
    if not isinstance(value, str):
        return ""

    value = value.strip()

    while value.endswith(
        (
            ".",
            ",",
            ";",
            ":",
        )
    ):
        value = value[:-1].strip()

    return value


def _add_parenthetical_variants(
    related_mentions: dict[str, list[dict]],
    value: str,
    *,
    mention_type: str,
    relation: str,
    target_entity_id: str,
    source_file: str,
    source_field: str,
    source_section: str | None = None,
):
    """
    Extract deterministic variants from parenthetical names.

    Example:

        Kancharla Gopanna (Bhakta Ramadasu)

    produces:

        Kancharla Gopanna (Bhakta Ramadasu)
        Kancharla Gopanna
        Bhakta Ramadasu
        Ramadasu

    Example:

        Komuravelli Mallanna Jathara (Brahmotsavam)

    produces:

        Komuravelli Mallanna Jathara (Brahmotsavam)
        Komuravelli Mallanna Jathara
        Brahmotsavam
    """

    value = _clean_named_phrase(value)

    if not value:
        return

    # Always retain the complete source phrase.
    add_related_mention(
        related_mentions,
        value,
        mention_type=mention_type,
        relation=relation,
        target_entity_id=target_entity_id,
        source_file=source_file,
        source_field=source_field,
        source_section=source_section,
    )

    match = re.match(
        r"^(.*?)\s*\(([^()]+)\)\s*$",
        value,
    )

    if not match:
        return

    main_name = _clean_named_phrase(
        match.group(1)
    )

    parenthetical = _clean_named_phrase(
        match.group(2)
    )

    if main_name:
        add_related_mention(
            related_mentions,
            main_name,
            mention_type=mention_type,
            relation=relation,
            target_entity_id=target_entity_id,
            source_file=source_file,
            source_field=source_field,
            source_section=source_section,
        )

    if parenthetical:
        add_related_mention(
            related_mentions,
            parenthetical,
            mention_type=mention_type,
            relation=relation,
            target_entity_id=target_entity_id,
            source_file=source_file,
            source_field=source_field,
            source_section=source_section,
        )

    # Deterministic short-name extraction for person variants.
    #
    # Example:
    #
    #     Bhakta Ramadasu -> Ramadasu
    #
    # This is intentionally applied only to person mentions
    # extracted from a parenthetical name. It does not scan
    # arbitrary prose for person names.
    if mention_type == "person":
        parenthetical_tokens = parenthetical.split()

        if len(parenthetical_tokens) >= 2:
            short_name = _clean_named_phrase(
                parenthetical_tokens[-1]
            )

            if short_name:
                add_related_mention(
                    related_mentions,
                    short_name,
                    mention_type=mention_type,
                    relation=relation,
                    target_entity_id=target_entity_id,
                    source_file=source_file,
                    source_field=source_field,
                    source_section=source_section,
                )


def _add_name_variants(
    related_mentions: dict[str, list[dict]],
    value: str,
    *,
    mention_type: str,
    relation: str,
    target_entity_id: str,
    source_file: str,
    source_field: str,
    source_section: str | None = None,
):
    """
    Generate deterministic variants for known named entities.

    For festivals/events this additionally supports:

        Komuravelli Mallanna Jathara
        -> Komuravelli Jathara
    """

    value = _clean_named_phrase(value)

    if not value:
        return

    _add_parenthetical_variants(
        related_mentions,
        value,
        mention_type=mention_type,
        relation=relation,
        target_entity_id=target_entity_id,
        source_file=source_file,
        source_field=source_field,
        source_section=source_section,
    )

    if mention_type not in {
        "festival",
        "event",
    }:
        return

    base = re.sub(
        r"\s*\([^()]*\)",
        "",
        value,
    ).strip()

    tokens = base.split()

    if len(tokens) < 3:
        return

    event_tokens = {
        "jathara",
        "jatara",
        "jatra",
        "festival",
        "brahmotsavam",
        "utsavam",
        "mahotsavam",
    }

    event_positions = [
        index
        for index, token in enumerate(tokens)
        if token.lower().strip(".,;:")
        in event_tokens
    ]

    for event_index in event_positions:
        if event_index < 1:
            continue

        first_token = tokens[0]
        event_token = tokens[event_index]

        candidate = (
            f"{first_token} {event_token}"
        )

        if (
            normalize_text(candidate)
            == normalize_text(value)
        ):
            continue

        add_related_mention(
            related_mentions,
            candidate,
            mention_type=mention_type,
            relation=relation,
            target_entity_id=target_entity_id,
            source_file=source_file,
            source_field=source_field,
            source_section=source_section,
        )


def add_related_values(
    related_mentions: dict[str, list[dict]],
    values: Any,
    *,
    mention_type: str,
    relation: str,
    target_entity_id: str,
    source_file: str,
    source_field: str,
    source_section: str | None = None,
):
    for value in ensure_list(values):
        if not isinstance(value, str):
            continue

        _add_name_variants(
            related_mentions,
            value,
            mention_type=mention_type,
            relation=relation,
            target_entity_id=target_entity_id,
            source_file=source_file,
            source_field=source_field,
            source_section=source_section,
        )


def extract_related_mentions_from_temple(
    data: dict,
    temple_id: str,
    source_file: str,
    related_mentions: dict[str, list[dict]],
):
    """
    Extract deterministic relationship mentions from known
    semantic structures in the temple document.

    This intentionally does not treat arbitrary prose as aliases.
    """

    # ---------------------------------------------------------
    # FOUNDERS / ASSOCIATED PEOPLE
    # ---------------------------------------------------------

    add_related_values(
        related_mentions,
        data.get("founders"),
        mention_type="person",
        relation="associated_person",
        target_entity_id=temple_id,
        source_file=source_file,
        source_field="founders",
    )

    # ---------------------------------------------------------
    # HISTORY
    # ---------------------------------------------------------

    history = data.get(
        "history",
        [],
    )

    for index, item in enumerate(
        ensure_list(history)
    ):
        if not isinstance(item, dict):
            continue

        section = first_non_empty(
            item.get("section"),
            item.get("title"),
            f"history[{index}]",
        )

        section_name = str(section)

        # Explicit people fields.
        for field in (
            "person",
            "people",
            "persons",
            "founder",
            "founders",
            "associated_person",
            "associated_people",
        ):
            add_related_values(
                related_mentions,
                item.get(field),
                mention_type="person",
                relation="associated_person",
                target_entity_id=temple_id,
                source_file=source_file,
                source_field=f"history.{field}",
                source_section=section_name,
            )

        # Explicit named person fields.
        for field in (
            "name",
            "person_name",
        ):
            value = item.get(field)

            if isinstance(value, str):
                add_related_values(
                    related_mentions,
                    value,
                    mention_type="person",
                    relation="historical_person",
                    target_entity_id=temple_id,
                    source_file=source_file,
                    source_field=f"history.{field}",
                    source_section=section_name,
                )

    # ---------------------------------------------------------
    # FESTIVALS
    # ---------------------------------------------------------

    festivals = data.get(
        "festivals",
        [],
    )

    for index, festival in enumerate(
        ensure_list(festivals)
    ):
        if not isinstance(festival, dict):
            continue

        section = first_non_empty(
            festival.get("festival"),
            festival.get("name"),
            festival.get("title"),
            f"festivals[{index}]",
        )

        section_name = str(section)

        for field in (
            "festival",
            "name",
            "title",
            "festival_name",
            "event",
            "event_name",
        ):
            value = festival.get(field)

            if not isinstance(value, str):
                continue

            _add_name_variants(
                related_mentions,
                value,
                mention_type="festival",
                relation="festival",
                target_entity_id=temple_id,
                source_file=source_file,
                source_field=f"festivals.{field}",
                source_section=section_name,
            )

    # ---------------------------------------------------------
    # FAQ
    # ---------------------------------------------------------

    faq = data.get(
        "faq",
        [],
    )

    for index, item in enumerate(
        ensure_list(faq)
    ):
        if not isinstance(item, dict):
            continue

        question = first_non_empty(
            item.get("question"),
            item.get("query"),
            item.get("title"),
        )

        answer = first_non_empty(
            item.get("answer"),
            item.get("response"),
        )

        section = f"faq[{index}]"

        if isinstance(question, str):
            add_related_mention(
                related_mentions,
                question,
                mention_type="topic",
                relation="faq_topic",
                target_entity_id=temple_id,
                source_file=source_file,
                source_field="faq.question",
                source_section=section,
            )

            # Also index the meaningful phrase after
            # common interrogative/auxiliary words.
            cleaned_question = re.sub(
                r"^(what|who|where|when|why|how|which)\b",
                "",
                question.strip(),
                flags=re.IGNORECASE,
            )

            cleaned_question = re.sub(
                r"^(is|are|was|were|does|do|did|can|could|should)\b",
                "",
                cleaned_question.strip(),
                flags=re.IGNORECASE,
            )

            cleaned_question = cleaned_question.rstrip(
                " ?."
            ).strip()

            if cleaned_question:
                add_related_mention(
                    related_mentions,
                    cleaned_question,
                    mention_type="topic",
                    relation="faq_topic_phrase",
                    target_entity_id=temple_id,
                    source_file=source_file,
                    source_field="faq.question",
                    source_section=section,
                )

        for field in (
            "topic",
            "subject",
            "name",
        ):
            value = item.get(field)

            if isinstance(value, str):
                add_related_mention(
                    related_mentions,
                    value,
                    mention_type="topic",
                    relation="faq_topic",
                    target_entity_id=temple_id,
                    source_file=source_file,
                    source_field=f"faq.{field}",
                    source_section=section,
                )

        # Do not index arbitrary FAQ answers.
        # Only short named answers are useful as topics.
        if (
            isinstance(answer, str)
            and 1 <= len(answer.split()) <= 8
        ):
            add_related_mention(
                related_mentions,
                answer,
                mention_type="topic",
                relation="faq_answer_topic",
                target_entity_id=temple_id,
                source_file=source_file,
                source_field="faq.answer",
                source_section=section,
            )

    # ---------------------------------------------------------
    # QUERY VARIATIONS
    # ---------------------------------------------------------

    query_variations = data.get(
        "query_variations",
        [],
    )

    for index, variation in enumerate(
        ensure_list(query_variations)
    ):
        if isinstance(variation, str):
            add_related_mention(
                related_mentions,
                variation,
                mention_type="topic",
                relation="query_variation",
                target_entity_id=temple_id,
                source_file=source_file,
                source_field="query_variations",
                source_section=f"query_variations[{index}]",
            )
            continue

        if not isinstance(variation, dict):
            continue

        for field in (
            "query",
            "question",
            "variation",
            "text",
        ):
            value = variation.get(field)

            if isinstance(value, str):
                add_related_mention(
                    related_mentions,
                    value,
                    mention_type="topic",
                    relation="query_variation",
                    target_entity_id=temple_id,
                    source_file=source_file,
                    source_field=f"query_variations.{field}",
                    source_section=f"query_variations[{index}]",
                )


# =============================================================
# TEMPLE REGISTRY
# =============================================================


def extract_temple_registry() -> dict[str, dict]:
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


# =============================================================
# TEMPLE ENTITIES
# =============================================================


def build_temple_entities(
    entities: dict[str, dict],
    aliases: dict[str, set[str]],
    related_mentions: dict[str, list[dict]],
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
                    "file": str(
                        path.relative_to(
                            PROJECT_ROOT
                        )
                    ),
                    "error": str(exc),
                }
            )
            continue

        if not isinstance(data, dict):
            report["invalid_records"].append(
                {
                    "file": str(
                        path.relative_to(
                            PROJECT_ROOT
                        )
                    ),
                    "reason": (
                        "Root JSON is not an object."
                    ),
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

        multilingual_names = multilingual.get(
            "names",
            {},
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

            normalized = normalize_text(alias)

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

        source_file = str(
            path.relative_to(
                PROJECT_ROOT
            )
        )

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
            "source_file": source_file,
            "relationships": {
                "accommodations": [],
                "restaurants": [],
                "emergency": False,
            },
        }

        extract_related_mentions_from_temple(
            data=data,
            temple_id=temple_id,
            source_file=source_file,
            related_mentions=related_mentions,
        )


# =============================================================
# ACCOMMODATIONS
# =============================================================


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
                    "file": str(
                        path.relative_to(
                            PROJECT_ROOT
                        )
                    ),
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

            aliases_for_entity = [name]

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

                normalized = normalize_text(alias)

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


# =============================================================
# RESTAURANTS
# =============================================================


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
                    "file": str(
                        path.relative_to(
                            PROJECT_ROOT
                        )
                    ),
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

        aliases_for_entity = [name]

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

            normalized = normalize_text(alias)

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


# =============================================================
# EMERGENCY
# =============================================================


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
                    "file": str(
                        path.relative_to(
                            PROJECT_ROOT
                        )
                    ),
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

        temple_id = str(temple_id)

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


# =============================================================
# RELATIONSHIPS
# =============================================================


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

        temple = entities[temple_id]

        relationship_list = (
            "accommodations"
            if entity_type == "accommodation"
            else "restaurants"
        )

        temple[
            "relationships"
        ][relationship_list].append(
            entity["entity_id"]
        )


# =============================================================
# VALIDATION
# =============================================================


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


def build_related_mention_report(
    related_mentions: dict[str, list[dict]],
    entities: dict[str, dict],
    report: dict,
):
    for normalized, records in sorted(
        related_mentions.items()
    ):
        valid_records = []

        for record in records:
            target_id = record.get(
                "target_entity_id"
            )

            if target_id not in entities:
                report[
                    "unknown_related_references"
                ].append(
                    {
                        "mention": record.get(
                            "mention"
                        ),
                        "target_entity_id": target_id,
                        "source_file": record.get(
                            "source_file"
                        ),
                        "source_field": record.get(
                            "source_field"
                        ),
                    }
                )
                continue

            valid_records.append(record)

        target_ids = {
            record["target_entity_id"]
            for record in valid_records
        }

        if len(target_ids) > 1:
            report[
                "ambiguous_related_mentions"
            ].append(
                {
                    "normalized_mention": normalized,
                    "targets": sorted(
                        target_ids
                    ),
                    "records": valid_records,
                }
            )


# =============================================================
# MAIN
# =============================================================


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

    related_mentions: dict[
        str,
        list[dict],
    ] = defaultdict(list)

    report = {
        "counts": {
            "temple_files": 0,
            "accommodation_files": 0,
            "restaurant_files": 0,
            "emergency_files": 0,
            "entities": 0,
            "aliases": 0,
            "related_mentions": 0,
        },
        "duplicate_ids": [],
        "ambiguous_aliases": [],
        "ambiguous_related_mentions": [],
        "unknown_references": [],
        "unknown_related_references": [],
        "invalid_json": [],
        "invalid_records": [],
    }

    print()
    print("==========================================")
    print("PilgrimAI Entity Registry Builder")
    print("==========================================")
    print()

    print("Reading temples...")
    build_temple_entities(
        entities,
        aliases,
        related_mentions,
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

    print("Checking related mentions...")
    build_related_mention_report(
        related_mentions,
        entities,
        report,
    )

    report["counts"]["entities"] = len(
        entities
    )

    report["counts"]["aliases"] = len(
        aliases
    )

    report["counts"]["related_mentions"] = len(
        related_mentions
    )

    registry = {
        "metadata": {
            "version": "2.2",
            "generated_by": (
                "PilgrimAI Entity Registry Builder"
            ),
            "features": [
                "canonical_entities",
                "aliases",
                "related_mentions",
                "parenthetical_name_variants",
                "person_short_name_variants",
                "festival_name_variants",
                "faq_topic_variants",
                "deterministic_relationship_resolution",
            ],
        },
        "entities": entities,
        "aliases": {
            alias: sorted(entity_ids)
            for alias, entity_ids
            in sorted(aliases.items())
        },
        "related_mentions": {
            mention: records
            for mention, records
            in sorted(
                related_mentions.items()
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
    print("==========================================")
    print("BUILD COMPLETE")
    print("==========================================")

    print(
        f"Temples:            "
        f"{report['counts']['temple_files']}"
    )

    print(
        f"Accommodations:     "
        f"{report['counts']['accommodation_files']}"
    )

    print(
        f"Restaurants:        "
        f"{report['counts']['restaurant_files']}"
    )

    print(
        f"Emergency:          "
        f"{report['counts']['emergency_files']}"
    )

    print(
        f"Total entities:     "
        f"{report['counts']['entities']}"
    )

    print(
        f"Total aliases:      "
        f"{report['counts']['aliases']}"
    )

    print(
        f"Related mentions:   "
        f"{report['counts']['related_mentions']}"
    )

    print()

    print(
        f"Duplicate IDs:      "
        f"{len(report['duplicate_ids'])}"
    )

    print(
        f"Ambiguous aliases:  "
        f"{len(report['ambiguous_aliases'])}"
    )

    print(
        f"Ambiguous related:  "
        f"{len(report['ambiguous_related_mentions'])}"
    )

    print(
        f"Unknown references: "
        f"{len(report['unknown_references'])}"
    )

    print(
        f"Invalid JSON:       "
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