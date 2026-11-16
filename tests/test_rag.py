"""Évaluation du RAG sur 10 questions types.

Pour chaque question, on vérifie que les passages retrouvés viennent du bon
médicament et de la bonne rubrique du RCP, et que chaque passage cite sa source.

Nécessite la base vectorielle : python -m src.rag.index

Usage :
    pytest tests/test_rag.py        # tests
    python -m tests.test_rag        # tableau de résultats lisible
"""

import sys

import pytest

from src.config import CHROMA_DIR

# (question, substance attendue, rubrique attendue)
QUESTIONS = [
    ("Quelles sont les contre-indications du rivaroxaban ?", "rivaroxaban", "4.3"),
    ("Dans quelles indications le dabigatran est-il utilisé ?", "dabigatran", "4.1"),
    ("Quelles sont les interactions médicamenteuses de la warfarine ?", "warfarine", "4.5"),
    ("Peut-on prescrire de l'atorvastatine à une femme enceinte ?", "atorvastatine", "4.6"),
    ("Quelle est la posologie de la metformine chez l'adulte ?", "metformine", "4.2"),
    ("Quels sont les effets indésirables fréquents de l'amlodipine ?", "amlodipine", "4.8"),
    ("Que faire en cas de surdosage en paracétamol ?", "paracétamol", "4.9"),
    ("Le zolpidem a-t-il un effet sur la conduite automobile ?", "zolpidem", "4.7"),
    ("Quelles précautions avec le ramipril en cas d'insuffisance rénale ?", "ramipril", "4.4"),
    ("Quel est le SMR du Previscan selon la HAS ?", "fluindione", "SMR"),
]

pytestmark = pytest.mark.skipif(not CHROMA_DIR.exists(),
                                reason="base vectorielle absente (python -m src.rag.index)")


def _numero(extrait) -> str:
    from src.rag.chunking import RUBRIQUES
    return next(n for n, titre in RUBRIQUES.items() if titre == extrait.rubrique)


@pytest.mark.parametrize("question, substance, rubrique", QUESTIONS)
def test_question_type(question, substance, rubrique):
    from src.rag.recherche import rechercher

    extraits = rechercher(question, k=5)
    assert extraits, "aucun passage retrouvé"
    assert all(e.url.startswith("https://") and e.source for e in extraits), "source manquante"
    assert all(e.substance == substance for e in extraits), "passage d'un autre médicament"
    assert _numero(extraits[0]) == rubrique, "le premier passage n'est pas dans la bonne rubrique"


if __name__ == "__main__":
    from src.rag.recherche import rechercher

    sys.stdout.reconfigure(encoding="utf-8")
    reussies = 0
    for question, substance, rubrique in QUESTIONS:
        premier = rechercher(question, k=5)[0]
        ok = premier.substance == substance and _numero(premier) == rubrique
        reussies += ok
        print(f"{'OK ' if ok else 'KO '} {question}\n     -> {premier.citation()}")
    print(f"\n{reussies}/{len(QUESTIONS)} questions : bon médicament et bonne rubrique en tête")
