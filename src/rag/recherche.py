"""Recherche dans les RCP : similarité vectorielle + filtres déduits de la question.

Une recherche purement vectorielle confond facilement deux médicaments proches
(les RCP se ressemblent tous). On repère donc d'abord, dans la question, le
médicament, la classe thérapeutique et la rubrique visés, puis on restreint la
recherche vectorielle à ce périmètre.

Usage :
    python -m src.rag.recherche "Quelles sont les contre-indications du rivaroxaban ?"
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

from src.config import MEDICAMENTS_CSV
from src.ingestion.referentiel import CLASSES, sans_accents
from src.rag.index import ouvrir_base

# mots de la question -> rubriques du RCP
MOTS_RUBRIQUES = [
    (r"contre[- ]?indi", ["4.3"]),
    (r"interaction|associ(er|ation)|en meme temps|avec (de l'|l'|le |la |du )", ["4.5"]),
    (r"posologie|\bdoses?\b|\bdosage|combien de|\bprises?\b|administr", ["4.2"]),
    (r"enceinte|grossesse|allait", ["4.6"]),
    (r"effets? (indesirables?|secondaires?)|tolerance", ["4.8"]),
    (r"surdosage|antidote", ["4.9"]),
    (r"condui(te|re)|vigilance", ["4.7"]),
    (r"precaution|mise en garde|insuffisan(ce|t) (renal|hepat)|surveillance", ["4.4"]),
    (r"\bsmr\b|\basmr\b|\bhas\b|service medical|rembours", ["SMR", "ASMR"]),
    (r"(?<!contre-)(?<!contre )\bindi(cation|que)|a quoi sert|pour quel", ["4.1"]),
    (r"mecanisme|efficacite|etude|pharmacodynami", ["5.1"]),
]


@dataclass
class Extrait:
    """Un passage retrouvé, avec de quoi citer sa source."""

    texte: str
    denomination: str
    substance: str
    rubrique: str
    source: str
    url: str
    date_maj: str
    score: float

    def citation(self) -> str:
        date = f", mis à jour le {self.date_maj}" if self.date_maj else ""
        return f"{self.denomination}, {self.rubrique} [{self.source}{date}] {self.url}"


@lru_cache(maxsize=1)
def _lexique() -> tuple[dict[str, str], dict[str, str]]:
    """Noms reconnus dans une question : substances et marques -> substance, classes."""
    meds = pd.read_csv(MEDICAMENTS_CSV, dtype=str).fillna("")
    noms = {sans_accents(s): s for s in meds["substance"].unique()}
    for med in meds.itertuples():
        marque = sans_accents(re.match(r"[^\d,]+", med.denomination).group().strip())
        # Les génériques portent le nom de la substance suivi du laboratoire.
        if len(marque) > 3 and not marque.startswith(sans_accents(med.substance).split()[0]):
            noms.setdefault(marque, med.substance)
    classes = {sans_accents(c): c for c in CLASSES}
    return noms, classes


def analyser(question: str) -> dict:
    """Médicaments, classes thérapeutiques et rubriques mentionnés dans la question."""
    q = sans_accents(question)
    noms, classes = _lexique()
    substances = list(dict.fromkeys(
        s for nom, s in noms.items() if re.search(rf"\b{re.escape(nom)}\b", q)))
    trouvees = [c for nom, c in classes.items() if re.search(rf"\b{re.escape(nom)}s?\b", q)]
    rubriques = list(dict.fromkeys(n for motif, nums in MOTS_RUBRIQUES
                                   if re.search(motif, q) for n in nums))
    return {"substances": substances, "classes": trouvees, "rubriques": rubriques}


def _filtre(champs: dict[str, list[str]]) -> dict | None:
    conditions = [{cle: {"$in": valeurs}} for cle, valeurs in champs.items() if valeurs]
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def rechercher(question: str, k: int = 5) -> list[Extrait]:
    """Les k passages les plus pertinents pour la question, sources comprises."""
    cible = analyser(question)
    medicament = ({"substance": cible["substances"]} if cible["substances"]
                  else {"classe_therapeutique": cible["classes"]})

    # Du filtre le plus précis au plus large, jusqu'à obtenir k passages.
    essais = []
    for filtre in (_filtre({**medicament, "rubrique_numero": cible["rubriques"]}),
                   _filtre(medicament), None):
        if filtre not in essais:
            essais.append(filtre)

    base = ouvrir_base()
    resultats: list = []
    for filtre in essais:
        trouves = base.similarity_search_with_relevance_scores(question, k=k, filter=filtre)
        vus = {doc.id for doc, _ in resultats}
        resultats += [(doc, score) for doc, score in trouves if doc.id not in vus]
        if len(resultats) >= k:
            break

    return [Extrait(texte=doc.page_content, denomination=doc.metadata["denomination"],
                    substance=doc.metadata["substance"], rubrique=doc.metadata["rubrique"],
                    source=doc.metadata["source"], url=doc.metadata["url"],
                    date_maj=doc.metadata["date_maj"], score=round(score, 3))
            for doc, score in resultats[:k]]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    question = " ".join(sys.argv[1:]) or "Quelles sont les contre-indications du rivaroxaban ?"
    print(analyser(question))
    for extrait in rechercher(question):
        print(f"\n[{extrait.score}] {extrait.citation()}\n{extrait.texte[:400]}")
