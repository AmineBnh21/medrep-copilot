"""Découpage des rubriques du RCP en chunks prêts à être indexés."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import RCP_JSONL

# Rubriques utiles à un délégué médical. Les rubriques pharmaceutiques
# (excipients, conservation, emballage, ...) ne sont pas indexées.
RUBRIQUES = {
    "4.1": "Indications thérapeutiques",
    "4.2": "Posologie et mode d'administration",
    "4.3": "Contre-indications",
    "4.4": "Mises en garde et précautions d'emploi",
    "4.5": "Interactions médicamenteuses",
    "4.6": "Fertilité, grossesse et allaitement",
    "4.7": "Conduite de véhicules et utilisation de machines",
    "4.8": "Effets indésirables",
    "4.9": "Surdosage",
    "5.1": "Propriétés pharmacodynamiques",
    "SMR": "Avis de la HAS : service médical rendu (SMR)",
    "ASMR": "Avis de la HAS : amélioration du service médical rendu (ASMR)",
}

TAILLE_CHUNK = 900
RECOUVREMENT = 120


def lire_sections(chemin: Path = RCP_JSONL) -> list[dict]:
    with chemin.open(encoding="utf-8") as f:
        return [json.loads(ligne) for ligne in f]


def decouper(sections: list[dict]) -> list[Document]:
    """Un Document par chunk, avec de quoi citer la source dans ses métadonnées."""
    decoupeur = RecursiveCharacterTextSplitter(
        chunk_size=TAILLE_CHUNK, chunk_overlap=RECOUVREMENT,
        separators=["\n", ". ", " ; ", ", ", " ", ""], keep_separator="end")

    documents, deja_vus = [], set()
    for s in sections:
        if s["numero"] not in RUBRIQUES:
            continue
        rubrique = RUBRIQUES[s["numero"]]
        # L'en-tête rappelle le médicament et la rubrique : un chunk isolé reste
        # compréhensible, pour la recherche comme pour le LLM.
        entete = f"{s['denomination']} ({s['substance']}) - {rubrique}"
        for i, morceau in enumerate(decoupeur.split_text(s["texte"])):
            # Les dosages d'une même substance répètent souvent le même texte.
            empreinte = hashlib.md5(f"{s['substance']}|{s['numero']}|{morceau}".encode()).hexdigest()
            if empreinte in deja_vus:
                continue
            deja_vus.add(empreinte)
            documents.append(Document(
                page_content=f"{entete}\n{morceau.strip()}",
                id=f"{s['cis']}-{s['numero']}-{i}",
                metadata={
                    "cis": s["cis"],
                    "denomination": s["denomination"],
                    "substance": s["substance"],
                    "classe_therapeutique": s["classe_therapeutique"],
                    "rubrique_numero": s["numero"],
                    "rubrique": rubrique,
                    "source": s["source"],
                    "date_maj": s.get("date_maj", ""),
                    "url": s["url"],
                }))
    return documents
