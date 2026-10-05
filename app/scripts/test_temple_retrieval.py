from app.services.temple_vector_store import similarity_search


QUERIES = [
    "Who is the deity at Yadadri?",
    "Tell me about Ramadasu",
    "What is Komuravelli Jathara?",
]


def main() -> None:
    for query in QUERIES:
        print("=" * 80)
        print(f"QUERY: {query}")
        print()

        results = similarity_search(
            query,
            k=5,
        )

        for index, document in enumerate(results, start=1):
            print(f"--- RESULT {index} ---")
            print("METADATA:")
            print(document.metadata)
            print()
            print("CONTENT:")
            print(document.page_content[:700])
            print()


if __name__ == "__main__":
    main()