from __future__ import annotations

import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    VectorParams,
)
from sentence_transformers import SentenceTransformer


PROJECT_ROOT = Path(__file__).resolve().parents[2]

QDRANT_PATH = PROJECT_ROOT / "data" / "qdrant"

COLLECTION_NAME = "temple_documents_bge_m3"

EMBEDDING_MODEL = "BAAI/bge-m3"

EMBEDDING_DIMENSION = 1024

BATCH_SIZE = 32

RETRIEVAL_CANDIDATES = 20


CONTENT_TYPE_PRIORITY = {
    "overview": 1.00,
    "summary": 1.00,
    "religious_significance": 1.00,
    "spiritual_significance": 0.98,
    "sthala_puranam": 0.98,
    "history": 0.98,
    "faq": 0.96,
    "festivals": 0.95,
    "rituals": 0.95,
    "special_poojas": 0.95,
    "architecture": 0.90,
    "architectural_style": 0.90,
    "temple_layout": 0.88,
    "travel": 0.75,
    "travel_tips": 0.65,
    "nearby_places": 0.55,
    "nearby_infrastructure": 0.55,
    "audio": 0.85,
}


TIMING_QUERY_TERMS = {
    "timing",
    "timings",
    "time",
    "times",
    "hours",
    "opening",
    "opens",
    "open",
    "closing",
    "closes",
    "darshan",
    "visiting",
    "visit",
}

TIMING_DOCUMENT_TERMS = {
    "timing",
    "timings",
    "time",
    "times",
    "hours",
    "opening",
    "opens",
    "open",
    "closing",
    "closes",
    "darshan",
    "visiting",
    "visit",
}

ACCESS_QUERY_TERMS = {
    "reach",
    "route",
    "travel",
    "airport",
    "railway",
    "station",
    "bus",
    "distance",
    "directions",
}

FESTIVAL_QUERY_TERMS = {
    "festival",
    "festivals",
    "jathara",
    "utsavam",
    "celebration",
    "celebrations",
}

HISTORY_QUERY_TERMS = {
    "history",
    "built",
    "construction",
    "constructed",
    "inaugurated",
    "founded",
    "foundation",
    "founder",
    "founders",
}

RELIGIOUS_QUERY_TERMS = {
    "deity",
    "god",
    "goddess",
    "religious",
    "spiritual",
    "significance",
    "worship",
    "ritual",
    "rituals",
    "pooja",
    "puja",
}

FACILITY_QUERY_TERMS = {
    "facility",
    "facilities",
    "parking",
    "water",
    "shops",
    "amenities",
}


class BGEEmbeddings(Embeddings):
    def __init__(self) -> None:
        self.model = SentenceTransformer(
            EMBEDDING_MODEL,
            device="cpu",
        )

        self.model.max_seq_length = 512

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        embeddings = self.model.encode(
            texts,
            batch_size=BATCH_SIZE,
            show_progress_bar=True,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

        return embeddings.tolist()

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        embedding = self.model.encode(
            text,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

        return embedding.tolist()


_embeddings = BGEEmbeddings()


def get_qdrant_client() -> QdrantClient:
    QDRANT_PATH.mkdir(
        parents=True,
        exist_ok=True,
    )

    return QdrantClient(
        path=str(QDRANT_PATH),
    )


def ensure_collection() -> None:
    client = get_qdrant_client()

    collections = client.get_collections().collections

    existing_names = {
        collection.name
        for collection in collections
    }

    if COLLECTION_NAME in existing_names:
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=EMBEDDING_DIMENSION,
            distance=Distance.COSINE,
        ),
    )


def get_vector_store() -> QdrantVectorStore:
    ensure_collection()

    client = get_qdrant_client()

    return QdrantVectorStore(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding=_embeddings,
    )


def _tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(
            r"\b[\w'-]+\b",
            text,
            flags=re.UNICODE,
        )
        if len(token) > 1
    }


def _lexical_score(
    query: str,
    document: Document,
) -> float:
    query_tokens = _tokenize(query)

    if not query_tokens:
        return 0.0

    content = document.page_content.lower()

    metadata_text = " ".join(
        [
            str(document.metadata.get("name", "")),
            str(document.metadata.get("content_type", "")),
            str(document.metadata.get("section", "")),
        ]
    ).lower()

    content_tokens = _tokenize(content)
    metadata_tokens = _tokenize(metadata_text)

    content_matches = query_tokens.intersection(
        content_tokens
    )

    metadata_matches = query_tokens.intersection(
        metadata_tokens
    )

    content_ratio = (
        len(content_matches) / len(query_tokens)
    )

    metadata_ratio = (
        len(metadata_matches) / len(query_tokens)
    )

    phrase_bonus = 0.0

    normalized_query = " ".join(
        query.lower().split()
    )

    if (
        normalized_query
        and normalized_query in content
    ):
        phrase_bonus = 0.20

    return (
        (content_ratio * 0.60)
        + (metadata_ratio * 0.20)
        + phrase_bonus
    )


def _query_focus_terms(
    query: str,
) -> set[str]:
    query_tokens = _tokenize(query)

    focus_terms: set[str] = set()

    if query_tokens.intersection(TIMING_QUERY_TERMS):
        focus_terms.update(TIMING_QUERY_TERMS)

    if query_tokens.intersection(ACCESS_QUERY_TERMS):
        focus_terms.update(ACCESS_QUERY_TERMS)

    if query_tokens.intersection(FESTIVAL_QUERY_TERMS):
        focus_terms.update(FESTIVAL_QUERY_TERMS)

    if query_tokens.intersection(HISTORY_QUERY_TERMS):
        focus_terms.update(HISTORY_QUERY_TERMS)

    if query_tokens.intersection(RELIGIOUS_QUERY_TERMS):
        focus_terms.update(RELIGIOUS_QUERY_TERMS)

    if query_tokens.intersection(FACILITY_QUERY_TERMS):
        focus_terms.update(FACILITY_QUERY_TERMS)

    return focus_terms


def _query_focus_score(
    query: str,
    document: Document,
) -> float:
    query_tokens = _tokenize(query)

    if not query_tokens:
        return 0.0

    focus_terms = _query_focus_terms(query)

    if not focus_terms:
        return 0.0

    content = document.page_content.lower()

    document_tokens = _tokenize(content)

    matched_focus_terms = (
        focus_terms.intersection(document_tokens)
    )

    if not matched_focus_terms:
        return 0.0

    score = (
        len(matched_focus_terms)
        / len(focus_terms)
    )

    content_type = str(
        document.metadata.get(
            "content_type",
            "",
        )
    ).lower()

    if query_tokens.intersection(
        TIMING_QUERY_TERMS
    ):
        timing_matches = (
            document_tokens.intersection(
                TIMING_DOCUMENT_TERMS
            )
        )

        if timing_matches:
            score += 0.35

        if content_type == "faq":
            score += 0.20

        if (
            "darshan" in query_tokens
            and "darshan" in document_tokens
        ):
            score += 0.25

        if (
            "timing" in query_tokens
            or "timings" in query_tokens
            or "hours" in query_tokens
        ):
            timing_question_phrases = (
                "temple timings",
                "darshan timings",
                "darshan time",
                "opening hours",
                "visiting hours",
                "temple hours",
                "what are the temple timings",
            )

            if any(
                phrase in content
                for phrase in timing_question_phrases
            ):
                score += 0.50

    if query_tokens.intersection(
        FESTIVAL_QUERY_TERMS
    ):
        festival_matches = (
            document_tokens.intersection(
                FESTIVAL_QUERY_TERMS
            )
        )

        if festival_matches:
            score += 0.35

        if content_type in {
            "faq",
            "festivals",
            "best_time_to_visit",
        }:
            score += 0.15

    if query_tokens.intersection(
        HISTORY_QUERY_TERMS
    ):
        history_matches = (
            document_tokens.intersection(
                HISTORY_QUERY_TERMS
            )
        )

        if history_matches:
            score += 0.35

        if content_type == "history":
            score += 0.20

    if query_tokens.intersection(
        ACCESS_QUERY_TERMS
    ):
        access_matches = (
            document_tokens.intersection(
                ACCESS_QUERY_TERMS
            )
        )

        if access_matches:
            score += 0.35

        if content_type in {
            "travel",
            "travel_tips",
            "nearby_infrastructure",
            "faq",
        }:
            score += 0.15

    return min(score, 2.0)


def _content_type_score(
    document: Document,
) -> float:
    content_type = str(
        document.metadata.get(
            "content_type",
            "",
        )
    )

    return CONTENT_TYPE_PRIORITY.get(
        content_type,
        0.70,
    )


def _rerank_results(
    query: str,
    results: list[tuple[Document, float]],
) -> list[Document]:
    scored_results: list[tuple[float, Document]] = []

    for document, vector_score in results:
        lexical_score = _lexical_score(
            query,
            document,
        )

        query_focus_score = _query_focus_score(
            query,
            document,
        )

        content_type_score = _content_type_score(
            document,
        )

        final_score = (
            (vector_score * 0.55)
            + (lexical_score * 0.20)
            + (query_focus_score * 0.20)
            + (content_type_score * 0.05)
        )

        scored_results.append(
            (
                final_score,
                document,
            )
        )

    scored_results.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return [
        document
        for _, document in scored_results
    ]


def ingest_documents(
    documents: list[Document],
) -> int:
    if not documents:
        return 0

    vector_store = get_vector_store()

    vector_store.add_documents(
        documents,
    )

    return len(documents)


def _build_temple_filter(
    temple_ids: list[str] | None,
) -> Filter | None:
    if not temple_ids:
        return None

    normalized_ids = sorted(
        {
            str(temple_id).strip()
            for temple_id in temple_ids
            if str(temple_id).strip()
        }
    )

    if not normalized_ids:
        return None

    return Filter(
        must=[
            FieldCondition(
                key="metadata.temple_id",
                match=MatchAny(
                    any=normalized_ids,
                ),
            )
        ]
    )


def similarity_search(
    query: str,
    *,
    k: int = 5,
    temple_ids: list[str] | None = None,
) -> list[Document]:
    vector_store = get_vector_store()

    query_filter = _build_temple_filter(
        temple_ids
    )

    candidates = (
        vector_store.similarity_search_with_score(
            query,
            k=max(
                k,
                RETRIEVAL_CANDIDATES,
            ),
            filter=query_filter,
        )
    )

    reranked = _rerank_results(
        query,
        candidates,
    )

    return reranked[:k]