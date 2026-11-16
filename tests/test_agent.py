"""Tests du graphe de l'agent avec un LLM simulé (aucun appel réseau).

Le LLM simulé rejoue des réponses écrites à l'avance : on teste ainsi les
transitions du graphe (garde-fou, boucle de vérification, fiche), pas la
qualité du modèle. Les outils, eux, sont réels.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver

from src.agent import prompts
from src.agent.graphe import (MAX_BOUCLES, Triage, Verification, construire_graphe, repondre,
                              repondre_en_flux)
from src.config import CHROMA_DIR, PROFESSIONNELS_CSV

pytestmark = pytest.mark.skipif(
    not (CHROMA_DIR.exists() and PROFESSIONNELS_CSV.exists()),
    reason="données absentes (voir README, « Préparer les données »)")


class LLMSimule:
    """Rejoue, dans l'ordre, les réponses prévues pour chaque type d'appel."""

    def __init__(self, triages=(), routages=(), verifications=(), redactions=()):
        self.files = {"triage": list(triages), "routage": list(routages),
                      "verification": list(verifications), "redaction": list(redactions)}
        self.recus = {nom: [] for nom in self.files}

    def _suivant(self, nom, messages):
        self.recus[nom].append(messages)
        return self.files[nom].pop(0)

    def with_structured_output(self, schema):
        nom = "triage" if schema is Triage else "verification"
        return RunnableLambda(lambda messages: self._suivant(nom, messages))

    def bind_tools(self, outils):
        return RunnableLambda(lambda messages: self._suivant("routage", messages))

    def invoke(self, messages):
        return self._suivant("redaction", messages)


def appel(nom, numero, **args):
    return {"name": nom, "args": args, "id": f"appel_{numero}", "type": "tool_call"}


TERRAIN = Triage(categorie="terrain", raison="préparation de visite")


def test_fiche_de_visite_avec_les_deux_outils():
    llm = LLMSimule(
        triages=[TERRAIN],
        routages=[AIMessage("", tool_calls=[
            appel("chercher_professionnels", 1, specialite="cardiologue", ville="Lyon 3e"),
            appel("rechercher_medicament", 2, question="contre-indications du rivaroxaban")])],
        verifications=[Verification(suffisant=True)],
        redactions=[AIMessage("## Fiche de visite\n... [2]")])
    agent = construire_graphe(llm, checkpointer=MemorySaver())

    resultat = repondre(agent, "Je suis à Lyon 3e, quels cardiologues voir pour le rivaroxaban ?")

    assert resultat["reponse"].startswith("## Fiche de visite")
    # Sont listées l'annuaire [1] et la seule source citée [2], avec leur URL exacte.
    assert [s["numero"] for s in resultat["sources"]] == [1, 2]
    assert resultat["sources"][0]["type"] == "annuaire"
    source = resultat["sources"][1]
    assert source["type"] == "medicament" and "Contre-indications" in source["titre"]
    assert f"- [2] {source['titre']} : {source['url']}" in resultat["reponse"]
    assert source["url"].startswith("https://base-donnees-publique.medicaments.gouv.fr/")
    # Le rédacteur reçoit le dossier numéroté, pas ses propres connaissances.
    dossier = llm.recus["redaction"][0][1].content
    assert "[1] Annuaire Santé (RPPS) : Cardiologie, Lyon 3e" in dossier
    assert "Contre-indications" in dossier
    assert any(e.startswith("Routeur") for e in resultat["etapes"])


def test_conseil_patient_refuse_sans_appeler_d_outil():
    llm = LLMSimule(triages=[Triage(categorie="conseil_patient", raison="cas individuel")])
    agent = construire_graphe(llm, checkpointer=MemorySaver())

    resultat = repondre(agent, "Mon père prend du Previscan, peut-il prendre de l'aspirine ?")

    assert resultat["reponse"] == prompts.REFUS_PATIENT
    assert resultat["sources"] == []
    assert llm.recus["routage"] == []


def test_verification_insuffisante_reboucle_vers_le_routeur():
    llm = LLMSimule(
        triages=[TERRAIN],
        routages=[
            AIMessage("", tool_calls=[
                appel("rechercher_medicament", 1, question="indications du rivaroxaban")]),
            AIMessage("", tool_calls=[
                appel("rechercher_medicament", 2, question="contre-indications du rivaroxaban")]),
        ],
        verifications=[Verification(suffisant=False, manque="les contre-indications"),
                       Verification(suffisant=True)],
        redactions=[AIMessage("Réponse [1]")])
    agent = construire_graphe(llm, checkpointer=MemorySaver())

    resultat = repondre(agent, "Indications et contre-indications du rivaroxaban ?")

    assert any("insuffisant" in e for e in resultat["etapes"])
    # Au second passage, le routeur est informé de ce qui manque.
    assert "les contre-indications" in llm.recus["routage"][1][0].content
    # Le dossier remis au rédacteur réunit les deux recherches.
    dossier = llm.recus["redaction"][0][1].content
    assert "Indications thérapeutiques" in dossier and "Contre-indications" in dossier
    assert [s["numero"] for s in resultat["sources"]] == [1]


def test_la_boucle_de_verification_est_bornee():
    recherche = lambda n: AIMessage("", tool_calls=[  # noqa: E731
        appel("rechercher_medicament", n, question=f"posologie du ramipril {n}")])
    llm = LLMSimule(
        triages=[TERRAIN],
        routages=[recherche(n) for n in range(1, 6)],
        verifications=[Verification(suffisant=False, manque="tout")] * 5,
        redactions=[AIMessage("Réponse partielle")])
    agent = construire_graphe(llm, checkpointer=MemorySaver())

    resultat = repondre(agent, "Posologie du ramipril ?")

    assert resultat["reponse"] == "Réponse partielle"
    assert len(llm.recus["routage"]) == MAX_BOUCLES + 1  # premier passage + reboucles


def test_reponse_directe_puis_question_de_suivi():
    llm = LLMSimule(
        triages=[TERRAIN, TERRAIN],
        routages=[AIMessage("Bonjour ! Pour quelle ville préparez-vous votre visite ?"),
                  AIMessage("", tool_calls=[
                      appel("chercher_professionnels", 1, specialite="pneumologue", ville="Bron")])],
        verifications=[Verification(suffisant=True)],
        redactions=[AIMessage("Voici les pneumologues de Bron [1]")])
    agent = construire_graphe(llm, checkpointer=MemorySaver())

    premier = repondre(agent, "Bonjour")
    assert premier["reponse"].startswith("Bonjour") and premier["sources"] == []

    second = repondre(agent, "Les pneumologues à Bron")
    assert second["sources"][0]["titre"] == "Annuaire Santé (RPPS) : Pneumologie, Bron"
    assert second["reponse"].endswith("Pneumologie, Bron : https://annuaire.sante.fr")
    assert "Routeur : réponse directe, sans outil." not in second["etapes"]
    # L'historique envoyé au routeur reste une alternance valide pour l'API Mistral.
    historique = llm.recus["routage"][1][1:]
    assert [type(m) for m in historique] == [HumanMessage, AIMessage, HumanMessage]
    assert not any(isinstance(m, ToolMessage) for m in historique)


def test_repondre_en_flux_donne_les_etapes_puis_le_resultat():
    llm = LLMSimule(
        triages=[TERRAIN],
        routages=[AIMessage("", tool_calls=[
            appel("chercher_professionnels", 1, specialite="pneumologue", ville="Bron")])],
        verifications=[Verification(suffisant=True)],
        redactions=[AIMessage("Voici les pneumologues de Bron [1]")])
    agent = construire_graphe(llm, checkpointer=MemorySaver())

    evenements = list(repondre_en_flux(agent, "Les pneumologues à Bron"))

    *etapes, resultat = evenements
    assert etapes[0] == "Garde-fou : demande acceptée."
    assert etapes[-1] == "Rédaction de la réponse avec ses sources."
    assert resultat["etapes"] == etapes
    assert resultat["reponse"].startswith("Voici les pneumologues de Bron [1]")
