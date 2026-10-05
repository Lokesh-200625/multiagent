from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_community.document_loaders import JSONLoader


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLES_DIR = PROJECT_ROOT / "data" / "temples"


# Narrative fields that should become retrievable RAG documents.
NARRATIVE_FIELDS = {
    "overview",
    "summary",
    "sthala_puranam",
    "religious_significance",
    "spiritual_significance",
    "architecture",
    "architectural_style",
    "history",
    "temple_layout",
    "rituals",
    "special_poojas",
    "festivals",
    "travel",
    "travel_tips",
    "best_time_to_visit",
    "accommodation",
    "accessibility",
    "nearby_places",
    "nearby_infrastructure",
    "healing_beliefs",
    "miracles_and_devotee_experiences",
    "faq",
    "audio",
}


# Fields that contain structured operational facts.
# These stay in the canonical registry rather than becoming ordinary
# narrative RAG content.
STRUCTURED_ONLY_FIELDS = {
    "temple_id",
    "location",
    "darshan_timings",
    "sevas",
    "darshan_and_tickets",
    "contact",
    "images",
    "wikidata",
    "knowledge_graph",
    "sources",
    "quality_check",
    "last_verified",
    "query_variations",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stringify(value: Any) -> str:
    """
    Convert nested JSON content into readable text without losing
    the semantic structure of the source data.
    """
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, (int, float, bool)):
        return str(value)

    if isinstance(value, list):
        parts: list[str] = []

        for item in value:
            text = _stringify(item)
            if text:
                parts.append(text)

        return "\n".join(parts)

    if isinstance(value, dict):
        parts: list[str] = []

        for key, item in value.items():
            text = _stringify(item)

            if not text:
                continue

            readable_key = key.replace("_", " ").strip().title()

            if isinstance(item, (dict, list)):
                parts.append(f"{readable_key}:\n{text}")
            else:
                parts.append(f"{readable_key}: {text}")

        return "\n".join(parts)

    return str(value)


def _section_documents(
    temple: dict[str, Any],
    field_name: str,
    value: Any,
    *,
    source_tier: str,
    fetched_at: str,
) -> list[Document]:
    """
    Convert one semantic field from a temple JSON object into one or more
    LangChain Documents.

    Arrays such as history, festivals, FAQ, etc. are kept as individual
    semantic sections whenever possible.
    """
    temple_id = str(temple.get("temple_id", ""))
    temple_name = str(temple.get("name", ""))

    documents: list[Document] = []

    if isinstance(value, list):
        for index, item in enumerate(value):
            content = _stringify(item)

            if not content:
                continue

            section = f"{field_name}[{index}]"

            if isinstance(item, dict):
                title = (
                    item.get("title")
                    or item.get("name")
                    or item.get("heading")
                    or item.get("section")
                )

                if title:
                    section = str(title)

            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        "temple_id": temple_id,
                        "name": temple_name,
                        "content_type": field_name,
                        "field_name": field_name,
                        "section": section,
                        "source_tier": source_tier,
                        "fetched_at": fetched_at,
                        "valid_to": None,
                        "last_verified": temple.get("last_verified"),
                    },
                )
            )

        return documents

    content = _stringify(value)

    if not content:
        return documents

    documents.append(
        Document(
            page_content=content,
            metadata={
                "temple_id": temple_id,
                "name": temple_name,
                "content_type": field_name,
                "field_name": field_name,
                "section": field_name,
                "source_tier": source_tier,
                "fetched_at": fetched_at,
                "valid_to": None,
                "last_verified": temple.get("last_verified"),
            },
        )
    )

    return documents


def load_temple_json(path: str | Path) -> dict[str, Any]:
    """
    Load one temple JSON through LangChain JSONLoader.

    JSONLoader gives us a LangChain Document containing the complete
    source JSON. We then parse that source and create semantic Documents.
    """
    path = Path(path)

    loader = JSONLoader(
        file_path=str(path),
        jq_schema=".",
        text_content=False,
    )

    source_documents = loader.load()

    if len(source_documents) != 1:
        raise ValueError(
            f"Expected exactly one source document from {path}, "
            f"got {len(source_documents)}"
        )

    raw_content = source_documents[0].page_content

    if isinstance(raw_content, str):
        temple = json.loads(raw_content)
    elif isinstance(raw_content, dict):
        temple = raw_content
    else:
        raise ValueError(
            f"Unexpected JSONLoader content type for {path}: "
            f"{type(raw_content).__name__}"
        )

    if not isinstance(temple, dict):
        raise ValueError(f"Temple JSON root must be an object: {path}")

    return temple


def build_temple_documents(
    path: str | Path,
    *,
    source_tier: str = "internal_dataset",
    fetched_at: str | None = None,
) -> list[Document]:
    """
    Build semantic LangChain Documents from one temple JSON file.
    """
    temple = load_temple_json(path)

    temple_id = temple.get("temple_id")
    temple_name = temple.get("name")

    if not temple_id:
        raise ValueError(f"Missing temple_id in {path}")

    if not temple_name:
        raise ValueError(f"Missing name in {path}")

    ingestion_time = fetched_at or _now_iso()

    documents: list[Document] = []

    for field_name in NARRATIVE_FIELDS:
        if field_name not in temple:
            continue

        documents.extend(
            _section_documents(
                temple,
                field_name,
                temple[field_name],
                source_tier=source_tier,
                fetched_at=ingestion_time,
            )
        )

    return documents


def load_all_temple_documents(
    temples_dir: str | Path = TEMPLES_DIR,
    *,
    source_tier: str = "internal_dataset",
) -> list[Document]:
    """
    Load every temple JSON under data/temples/.
    """
    temples_dir = Path(temples_dir)

    if not temples_dir.exists():
        raise FileNotFoundError(
            f"Temple directory does not exist: {temples_dir}"
        )

    documents: list[Document] = []

    for path in sorted(temples_dir.glob("*.json")):
        documents.extend(
            build_temple_documents(
                path,
                source_tier=source_tier,
            )
        )

    return documents