from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


CHUNK_SIZE = 800
CHUNK_OVERLAP = 120


_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=[
        "\n\n",
        "\n",
        ". ",
        " ",
        "",
    ],
)


def split_temple_documents(
    documents: list[Document],
) -> list[Document]:
    """
    Split each semantic source document independently.

    chunk_index is relative to the original semantic document,
    not global across the temple.
    """
    chunks: list[Document] = []

    for document in documents:
        source_chunks = _splitter.split_documents([document])

        for index, chunk in enumerate(source_chunks):
            chunk.metadata["chunk_index"] = index
            chunks.append(chunk)

    return chunks