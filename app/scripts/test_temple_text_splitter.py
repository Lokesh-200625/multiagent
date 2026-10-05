from collections import Counter

from app.services.temple_document_loader import (
    TEMPLES_DIR,
    build_temple_documents,
)
from app.services.temple_text_splitter import (
    split_temple_documents,
)


def main() -> None:
    temple_files = sorted(TEMPLES_DIR.glob("*.json"))

    if not temple_files:
        raise RuntimeError(
            f"No temple JSON files found in {TEMPLES_DIR}"
        )

    for path in temple_files[:2]:
        documents = build_temple_documents(path)
        chunks = split_temple_documents(documents)

        print("=" * 80)
        print(f"FILE: {path.name}")
        print(f"DOCUMENTS BEFORE SPLIT: {len(documents)}")
        print(f"CHUNKS AFTER SPLIT: {len(chunks)}")

        counts = Counter(
            chunk.metadata["content_type"]
            for chunk in chunks
        )

        print("\nCHUNKS BY CONTENT TYPE:")
        for content_type, count in sorted(counts.items()):
            print(f"  {content_type}: {count}")

        print("\nFIRST 3 CHUNKS:")

        for index, chunk in enumerate(chunks[:3]):
            print(f"\n--- CHUNK {index} ---")
            print("METADATA:")
            print(chunk.metadata)
            print("\nCONTENT:")
            print(chunk.page_content[:500])

        print()


if __name__ == "__main__":
    main()