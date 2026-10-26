"""Temple agent - RAG over the Qdrant collection. Heavy imports stay lazy so
the API boots (and tests run) without loading sentence-transformers."""
from __future__ import annotations

from typing import Any


def _document_to_evidence(document: Any) -> dict:
    metadata = dict(document.metadata)
    return {
        "source": "temple_rag",
        "kind": "chunk",
        "text": document.page_content,
        "temple_id": metadata.get("temple_id"),
        "name": metadata.get("name"),
        "content_type": metadata.get("content_type"),
        "field_name": metadata.get("field_name"),
        "section": metadata.get("section"),
        "chunk_index": metadata.get("chunk_index"),
        "source_tier": metadata.get("source_tier"),
    }


def run(question: str, temple_ids: list[str] | None = None) -> dict:
    """Retrieve top-5 reranked chunks, filtered to resolved temple ids."""
    try:
        from app.services.temple_vector_store import similarity_search
    except Exception as exc:  # pragma: no cover - environment issue
        return {
            "status": "ERROR",
            "error": f"temple retrieval unavailable: {type(exc).__name__}: {exc}",
            "evidence": [],
        }

    try:
        documents = similarity_search(
            question,
            k=5,
            temple_ids=sorted(temple_ids) if temple_ids else None,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "ERROR",
            "error": f"temple retrieval failed: {type(exc).__name__}: {exc}",
            "evidence": [],
        }

    evidence = [_document_to_evidence(doc) for doc in documents]

    # hard filter: never return another temple's chunks when ids were resolved
    if temple_ids:
        expected = set(temple_ids)
        evidence = [e for e in evidence if e.get("temple_id") in expected]

    return {"status": "COMPLETED", "evidence": evidence}
