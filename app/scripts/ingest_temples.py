from app.services.temple_document_loader import (
    load_all_temple_documents,
)
from app.services.temple_text_splitter import (
    split_temple_documents,
)
from app.services.temple_vector_store import (
    ingest_documents,
)


def main() -> None:
    print("Loading temple documents...")

    documents = load_all_temple_documents()

    print(f"Semantic documents: {len(documents)}")

    print("Splitting documents...")

    chunks = split_temple_documents(documents)

    print(f"Chunks: {len(chunks)}")

    print("Embedding and indexing into Qdrant...")

    count = ingest_documents(chunks)

    print(f"Indexed: {count}")


if __name__ == "__main__":
    main()