"""Graphe LangGraph de l'agent.

    garde-fou ──► routeur ──► outils ──► vérification ──► fiche
        │            ▲                        │
        ▼            └──── insuffisant ───────┘
      refus

Usage :
    python -m src.agent.graphe "Je suis à Lyon 3e demain, je présente un anticoagulant. ..."
"""

from __future__ import annotations

import operator
import re
import sys
from typing import Annotated, Literal

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from src.agent import prompts
from src.tools.outils import OUTILS

MAX_BOUCLES = 1  # nombre maximal de retours vérification -> routeur


class Etat(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    etapes: Annotated[list[str], operator.add]  # raisonnement affiché dans l'interface
    sources: list[dict]                         # sources citées dans la dernière réponse
    categorie: str
    boucles: int
    manque: str


class Triage(BaseModel):
    categorie: Literal["terrain", "conseil_patient", "hors_sujet"]
    raison: str = Field(description="Justification en une phrase")


class Verification(BaseModel):
    suffisant: bool
    manque: str = Field(default="", description="Ce qui manque pour répondre, sinon vide")


# ------------------------------------------------------------ utilitaires

def _tour_courant(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Messages depuis la dernière question de l'utilisateur (incluse)."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i:]
    return messages


def _question(messages: list[AnyMessage]) -> str:
    return str(_tour_courant(messages)[0].content)


def _historique(messages: list[AnyMessage], nb: int = 4) -> str:
    """Les derniers échanges, pour comprendre une question de suivi."""
    echanges = [m for m in messages if isinstance(m, HumanMessage)
                or (isinstance(m, AIMessage) and not m.tool_calls)]
    lignes = [f"{'Utilisateur' if isinstance(m, HumanMessage) else 'Assistant'} : "
              f"{str(m.content)[:500]}" for m in echanges[-nb - 1:-1]]
    return "\n".join(lignes)


def construire_dossier(messages: list[AnyMessage]) -> tuple[str, list[dict]]:
    """Résultats d'outils du tour courant, numérotés pour être cités.

    Renvoie (texte du dossier, liste des sources numérotées).
    """
    blocs, sources = [], []
    for m in _tour_courant(messages):
        if not isinstance(m, ToolMessage):
            continue
        if m.name == "rechercher_medicament" and m.artifact:
            for e in m.artifact:
                if any(s.get("texte") == e["texte"] for s in sources):
                    continue
                date = f", mis à jour le {e['date_maj']}" if e["date_maj"] else ""
                sources.append({"numero": len(sources) + 1, "type": "medicament",
                                "titre": f"{e['denomination']}, {e['rubrique']} "
                                         f"({e['source']}{date})",
                                "url": e["url"], "texte": e["texte"]})
                s = sources[-1]
                blocs.append(f"[{s['numero']}] {s['titre']}\nURL : {s['url']}\n{s['texte']}")
        elif m.name in ("chercher_professionnels", "prioriser_visites") \
                and isinstance(m.artifact, dict) and m.artifact.get("total"):
            a = m.artifact
            sources.append({"numero": len(sources) + 1, "type": "annuaire",
                            "titre": f"Annuaire Santé (RPPS) : {a['specialite']}, "
                                     f"{a.get('zone', a['lieu'])}",
                            "url": "https://annuaire.sante.fr", "texte": str(m.content)})
            s = sources[-1]
            blocs.append(f"[{s['numero']}] {s['titre']}\nURL : {s['url']}\n{s['texte']}")
        else:
            blocs.append(f"Résultat de {m.name} : {m.content}")
    return "\n\n".join(blocs), sources


def ajouter_sources(texte: str, sources: list[dict]) -> tuple[str, list[dict]]:
    """Ajoute à la réponse la liste des sources réellement citées.

    La liste est construite par le code, pas par le LLM : les titres et les URL
    sont donc exacts. Renvoie (réponse complétée, sources citées).
    """
    cites = {int(n) for groupe in re.findall(r"\[(\d+(?:\s*[,;-]\s*\d+)*)\]", texte)
             for n in re.findall(r"\d+", groupe)}
    # L'annuaire est toujours listé : le LLM cite rarement la source d'une liste de noms.
    retenues = [s for s in sources if s["numero"] in cites or s["type"] == "annuaire"]
    if not retenues:
        return texte, []
    lignes = [f"- [{s['numero']}] {s['titre']} : {s['url']}" for s in retenues]
    return texte.rstrip() + "\n\n**Sources**\n" + "\n".join(lignes), retenues


# ------------------------------------------------------------------ graphe

def construire_graphe(llm, checkpointer=None):
    """Assemble le graphe autour d'un LLM (injecté pour pouvoir le remplacer en test)."""
    outils = {o.name: o for o in OUTILS}
    llm_triage = llm.with_structured_output(Triage)
    llm_outils = llm.bind_tools(OUTILS)
    llm_verification = llm.with_structured_output(Verification)

    def garde_fou(etat: Etat) -> dict:
        messages = etat["messages"]
        contexte = _historique(messages)
        demande = (f"Échanges précédents :\n{contexte}\n\n" if contexte else "") + \
            f"Dernière demande : {_question(messages)}"
        triage = llm_triage.invoke([SystemMessage(prompts.GARDE_FOU), HumanMessage(demande)])
        if triage.categorie == "terrain":
            etape = "Garde-fou : demande acceptée."
        else:
            etape = f"Garde-fou : demande refusée ({triage.categorie}). {triage.raison}"
        return {"categorie": triage.categorie, "boucles": 0, "manque": "", "sources": [],
                "etapes": [etape]}

    def refus(etat: Etat) -> dict:
        texte = (prompts.REFUS_PATIENT if etat["categorie"] == "conseil_patient"
                 else prompts.REFUS_HORS_SUJET)
        return {"messages": [AIMessage(texte)]}

    def routeur(etat: Etat) -> dict:
        complement = (prompts.COMPLEMENT_BOUCLE.format(manque=etat["manque"])
                      if etat.get("manque") else "")
        systeme = SystemMessage(prompts.ROUTEUR.format(complement=complement))
        reponse = llm_outils.invoke([systeme, *etat["messages"]])
        if reponse.tool_calls:
            appels = ", ".join(f"{a['name']}({', '.join(map(str, a['args'].values()))})"
                               for a in reponse.tool_calls)
            return {"messages": [reponse], "etapes": [f"Routeur : {appels}"]}
        if isinstance(etat["messages"][-1], ToolMessage):
            # Rien de plus à chercher : la fiche sera rédigée avec le dossier existant.
            return {"etapes": ["Routeur : aucune recherche supplémentaire."]}
        return {"messages": [reponse], "etapes": ["Routeur : réponse directe, sans outil."]}

    def executer_outils(etat: Etat) -> dict:
        resultats, etapes = [], []
        for appel in etat["messages"][-1].tool_calls:
            try:
                message = outils[appel["name"]].invoke(appel)
            except Exception as erreur:  # l'erreur est rendue au LLM, qui peut corriger l'appel
                message = ToolMessage(f"Erreur de l'outil : {erreur}", name=appel["name"],
                                      tool_call_id=appel["id"])
            resultats.append(message)
            artifact = getattr(message, "artifact", None)
            if appel["name"] == "rechercher_medicament":
                bilan = f"{len(artifact or [])} passage(s)"
            else:
                bilan = f"{(artifact or {}).get('total', 0)} professionnel(s)"
            etapes.append(f"Outil {appel['name']} : {bilan}")
        return {"messages": resultats, "etapes": etapes}

    def verification(etat: Etat) -> dict:
        dossier, _ = construire_dossier(etat["messages"])
        avis = llm_verification.invoke([
            SystemMessage(prompts.VERIFICATION),
            HumanMessage(f"QUESTION : {_question(etat['messages'])}\n\nRÉSULTATS :\n{dossier}")])
        if avis.suffisant:
            return {"manque": "", "etapes": ["Vérification : informations suffisantes."]}
        if etat.get("boucles", 0) >= MAX_BOUCLES:
            return {"manque": "", "etapes": [
                f"Vérification : il manque encore « {avis.manque} », limite de recherches atteinte."]}
        return {"manque": avis.manque, "boucles": etat.get("boucles", 0) + 1,
                "etapes": [f"Vérification : insuffisant, nouvelle recherche ({avis.manque})"]}

    def fiche(etat: Etat) -> dict:
        dossier, sources = construire_dossier(etat["messages"])
        contexte = _historique(etat["messages"])
        demande = (f"Échanges précédents :\n{contexte}\n\n" if contexte else "") + \
            f"QUESTION : {_question(etat['messages'])}\n\nDOSSIER :\n{dossier}"
        reponse = llm.invoke([SystemMessage(prompts.FICHE), HumanMessage(demande)])
        texte, sources = ajouter_sources(str(reponse.content), sources)
        return {"messages": [AIMessage(texte)], "sources": sources,
                "etapes": ["Rédaction de la réponse avec ses sources."]}

    def apres_garde_fou(etat: Etat) -> str:
        return "routeur" if etat["categorie"] == "terrain" else "refus"

    def apres_routeur(etat: Etat) -> str:
        dernier = etat["messages"][-1]
        if isinstance(dernier, AIMessage) and dernier.tool_calls:
            return "outils"
        return "fiche" if isinstance(dernier, ToolMessage) else END

    def apres_verification(etat: Etat) -> str:
        return "routeur" if etat.get("manque") else "fiche"

    graphe = StateGraph(Etat)
    graphe.add_node("garde_fou", garde_fou)
    graphe.add_node("refus", refus)
    graphe.add_node("routeur", routeur)
    graphe.add_node("outils", executer_outils)
    graphe.add_node("verification", verification)
    graphe.add_node("fiche", fiche)

    graphe.add_edge(START, "garde_fou")
    graphe.add_conditional_edges("garde_fou", apres_garde_fou, ["routeur", "refus"])
    graphe.add_conditional_edges("routeur", apres_routeur, ["outils", "fiche", END])
    graphe.add_edge("outils", "verification")
    graphe.add_conditional_edges("verification", apres_verification, ["routeur", "fiche"])
    graphe.add_edge("refus", END)
    graphe.add_edge("fiche", END)
    return graphe.compile(checkpointer=checkpointer)


def creer_agent():
    """Agent prêt à l'emploi : LLM Mistral et mémoire de conversation."""
    from src.agent.llm import creer_llm

    return construire_graphe(creer_llm(), checkpointer=MemorySaver())


def repondre(agent, question: str, conversation: str = "defaut") -> dict:
    """Pose une question à l'agent. Renvoie la réponse, les étapes et les sources."""
    config = {"configurable": {"thread_id": conversation}}
    deja = len(agent.get_state(config).values.get("etapes", []))
    etat = agent.invoke({"messages": [HumanMessage(question)]}, config)
    return {"reponse": str(etat["messages"][-1].content), "etapes": etat["etapes"][deja:],
            "sources": etat.get("sources", [])}


def repondre_en_flux(agent, question: str, conversation: str = "defaut"):
    """Comme `repondre`, mais produit chaque étape dès qu'elle est franchie.

    Génère des chaînes (les étapes), puis en dernier le dictionnaire résultat.
    Utile pour l'interface : une question peut prendre plusieurs minutes quand
    le débit du LLM est limité.
    """
    config = {"configurable": {"thread_id": conversation}}
    etapes: list[str] = []
    for mise_a_jour in agent.stream({"messages": [HumanMessage(question)]}, config,
                                    stream_mode="updates"):
        for sortie in mise_a_jour.values():
            for etape in (sortie or {}).get("etapes", []):
                etapes.append(etape)
                yield etape
    etat = agent.get_state(config).values
    yield {"reponse": str(etat["messages"][-1].content), "etapes": etapes,
           "sources": etat.get("sources", [])}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    question = " ".join(sys.argv[1:]) or (
        "Je suis à Lyon 3e demain, je présente un anticoagulant. Quels cardiologues visiter "
        "et que dois-je savoir sur le produit ?")
    resultat = repondre(creer_agent(), question)
    print("\n".join(f"  · {e}" for e in resultat["etapes"]))
    print("\n" + resultat["reponse"])
