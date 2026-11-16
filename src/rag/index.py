"""Construction de la base vectorielle Chroma à partir des rubriques du RCP.

Usage :
    python -m src.rag.index
"""

from __future__ import annotations

import shutil

from langchain_chroma import Chroma
from tqdm import tqdm

from src.config import CHROMA_DIR, COLLECTION
from src.rag.chunking import decouper, lire_sections
from src.rag.embeddings import embeddings

LOT = 256


def ouvrir_base() -> Chroma:
    return Chroma(collection_name=COLLECTION, embedding_function=embeddings(),
                  persist_directory=str(CHROMA_DIR),
                  collection_metadata={"hnsw:space": "cosine"})


def construire() -> int:
    documents = decouper(lire_sections())
    if CHROMA_DIR.exists():
        shutil.rmtree(CHROMA_DIR)
    base = ouvrir_base()
    for debut in tqdm(range(0, len(documents), LOT), desc="Indexation", mininterval=5):
        base.add_documents(documents[debut:debut + LOT])
    return len(documents)


if __name__ == "__main__":
    print(f"{construire()} chunks indexés -> {CHROMA_DIR}")
