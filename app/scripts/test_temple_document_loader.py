from collections import Counter

from app.services.temple_document_loader import (
    TEMPLES_DIR,
    build_temple_documents,
)


def main() -> None:
    temple_files = sorted(TEMPLES_DIR.glob("*.json"))

    if not temple_files:
        raise RuntimeError(
            f"No temple JSON files found in {TEMPLES_DIR}"
        )

    print(f"Temple files found: {len(temple_files)}")
    print()

    for path in temple_files[:2]:
        documents = build_temple_documents(path)

        print("=" * 80)
        print(f"FILE: {path.name}")
        print(f"DOCUMENTS: {len(documents)}")

        content_types = Counter(
            document.metadata["content_type"]
            for document in documents
        )

        print("CONTENT TYPES:")
        for content_type, count in sorted(content_types.items()):
            print(f"  {content_type}: {count}")

        print()
        print("FIRST DOCUMENT:")
        document = documents[0]

        print("METADATA:")
        print(document.metadata)

        print()
        print("CONTENT:")
        print(document.page_content[:1000])
        print()


if __name__ == "__main__":
    main()