"""Tests de l'outil annuaire et de l'analyse des questions."""

import pytest

from src.config import MEDICAMENTS_CSV, PROFESSIONNELS_CSV
from src.tools.annuaire import chercher_professionnels, resoudre_lieu, resoudre_specialite

donnees_rpps = pytest.mark.skipif(not PROFESSIONNELS_CSV.exists(),
                                  reason="données absentes (python -m src.ingestion.rpps)")
donnees_bdpm = pytest.mark.skipif(not MEDICAMENTS_CSV.exists(),
                                  reason="données absentes (python -m src.ingestion.bdpm)")


@pytest.mark.parametrize("texte, attendu", [
    ("Lyon 3e", ("lyon", "3")),
    ("lyon 3ème arrondissement", ("lyon", "3")),
    ("Lyon 1er", ("lyon", "1")),
    ("69007", ("lyon", "7")),
    ("Lyon", ("lyon", "")),
    ("Sainte-Foy-lès-Lyon", ("sainte-foy-les-lyon", "")),
])
def test_resoudre_lieu(texte, attendu):
    assert resoudre_lieu(texte) == attendu


@donnees_rpps
@pytest.mark.parametrize("texte, attendu", [
    ("cardiologues", "Cardiologie"),
    ("médecin généraliste", "Médecine générale"),
    ("diabétologue", "Endocrinologie-diabétologie"),
    ("Pharmacien", "Pharmacien"),
    ("dermato", "Dermatologie et vénéréologie"),
    ("astrologue", None),
])
def test_resoudre_specialite(texte, attendu):
    assert resoudre_specialite(texte) == attendu


@donnees_rpps
def test_chercher_professionnels():
    resultat = chercher_professionnels("cardiologue", "Lyon 3e", limite=5)
    assert resultat["specialite"] == "Cardiologie" and resultat["lieu"] == "Lyon 3e"
    assert resultat["total"] >= len(resultat["professionnels"]) > 0
    assert all("69" in lieu["adresse"] for p in resultat["professionnels"] for lieu in p["lieux"])
    # un arrondissement est un sous-ensemble de la ville
    assert chercher_professionnels("cardiologue", "Lyon")["total"] > resultat["total"]
    assert "erreur" in chercher_professionnels("astrologue", "Lyon")


@donnees_bdpm
@pytest.mark.parametrize("question, substances, classes, rubriques", [
    ("Quelles sont les contre-indications du rivaroxaban ?", ["rivaroxaban"], [], ["4.3"]),
    ("Dans quelles indications utiliser le Previscan ?", ["fluindione"], [], ["4.1"]),
    ("Que dois-je savoir sur un anticoagulant ?", [], ["anticoagulant"], []),
    ("Interactions entre warfarine et amiodarone ?", ["warfarine", "amiodarone"], [], ["4.5"]),
])
def test_analyser(question, substances, classes, rubriques):
    from src.rag.recherche import analyser

    cible = analyser(question)
    assert sorted(cible["substances"]) == sorted(substances)
    assert cible["classes"] == classes
    assert cible["rubriques"] == rubriques


@donnees_rpps
@donnees_bdpm
def test_score_de_priorisation():
    from src.tools.score import prioriser

    resultat = prioriser("rivaroxaban", "Lyon 3e")
    assert resultat["classe"] == "anticoagulant"
    pros = resultat["professionnels"]
    scores = [p["score"] for p in pros]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 100 for s in scores)
    # Le premier est un cardiologue de la zone demandée, et le score est justifié.
    assert pros[0]["specialite"] == "Cardiologie"
    assert "Lyon 3e : 25/25 (zone demandée)" in pros[0]["explication"]
    # À spécialité et zone égales, un libéral passe devant un salarié.
    assert pros[0]["mode_exercice"] == "Libéral" and "exercice libéral : 20/20" in pros[0]["explication"]
    assert len({p["rpps"] for p in pros}) == len(pros)  # un professionnel n'apparaît qu'une fois


@donnees_rpps
@donnees_bdpm
def test_score_composantes():
    from src.tools.score import classe_du_produit, prioriser

    assert classe_du_produit("Previscan") == "anticoagulant"
    assert classe_du_produit("un antidiabétique") == "antidiabétique"
    # Avec une spécialité imposée, seuls ces professionnels sont classés.
    cardio = prioriser("anticoagulant", "Lyon 3e", "cardiologue", limite=200)
    assert {p["specialite"] for p in cardio["professionnels"]} == {"Cardiologie"}
    # Un arrondissement voisin rapporte moins de points de proximité.
    voisins = [p for p in cardio["professionnels"] if "arrondissement voisin" in p["explication"]]
    assert voisins and all(": 12/25" in p["explication"] for p in voisins)
    # Le score est la somme des quatre composantes affichées.
    import re
    for p in cardio["professionnels"]:
        assert p["score"] == sum(int(n) for n in re.findall(r": (\d+)/\d+", p["explication"]))
    assert "erreur" in prioriser("produit inconnu", "Lyon")
