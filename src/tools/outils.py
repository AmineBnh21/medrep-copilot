"""Outils exposés au LLM de l'agent.

Chaque outil renvoie un texte lisible par le LLM et, en « artifact », les
données structurées (pour afficher les sources dans l'interface).
"""

from __future__ import annotations

from langchain_core.tools import tool

from src.rag.recherche import rechercher
from src.tools.annuaire import chercher_professionnels as _chercher_professionnels
from src.tools.score import prioriser

MAX_PROFESSIONNELS = 15


@tool(response_format="content_and_artifact")
def chercher_professionnels(specialite: str, ville: str) -> tuple[str, dict]:
    """Cherche des professionnels de santé dans l'annuaire RPPS (département du Rhône).

    Args:
        specialite: spécialité ou métier, par exemple « cardiologue », « médecin
            généraliste », « pharmacien », « endocrinologie ».
        ville: commune du Rhône, avec l'arrondissement pour Lyon si la question le
            précise, par exemple « Lyon 3e », « Villeurbanne », « Bron ».
    """
    resultat = _chercher_professionnels(specialite, ville, limite=MAX_PROFESSIONNELS)
    if "erreur" in resultat:
        disponibles = ", ".join(resultat["specialites_disponibles"])
        return f"{resultat['erreur']} Spécialités disponibles : {disponibles}", resultat
    if resultat["total"] == 0:
        return (f"Aucun professionnel trouvé pour {resultat['specialite']} à {resultat['lieu']}. "
                "Vérifier le nom de la commune ou élargir la zone (ex. « Lyon »)."), resultat

    lignes = [f"{resultat['total']} professionnel(s) en {resultat['specialite']} à "
              f"{resultat['lieu']} (source : Annuaire Santé RPPS)."]
    if resultat["total"] > len(resultat["professionnels"]):
        lignes[0] += f" Les {len(resultat['professionnels'])} premiers :"
    for pro in resultat["professionnels"]:
        lieux = " ; ".join(f"{l['structure'] or 'Cabinet'} ({l['mode_exercice']}), {l['adresse']}"
                           for l in pro["lieux"])
        lignes.append(f"- {pro['nom']}, {pro['specialite']} : {lieux}")
    return "\n".join(lignes), resultat


@tool(response_format="content_and_artifact")
def rechercher_medicament(question: str) -> tuple[str, list[dict]]:
    """Cherche une information sur un médicament dans les RCP de la BDPM et les avis HAS.

    À utiliser pour les indications, contre-indications, interactions, posologie,
    effets indésirables, grossesse, SMR/ASMR. Poser une question précise et ne
    viser qu'un sujet par appel, par exemple « contre-indications du rivaroxaban »
    puis « interactions du rivaroxaban ».

    Args:
        question: question en français nommant le médicament (substance ou marque)
            ou la classe thérapeutique (ex. « anticoagulant »).
    """
    extraits = rechercher(question, k=4)
    if not extraits:
        return "Aucun passage trouvé dans la base.", []
    blocs = [f"Passage : {e.texte}\nSource : {e.citation()}" for e in extraits]
    return "\n\n".join(blocs), [vars(e) for e in extraits]


@tool(response_format="content_and_artifact")
def prioriser_visites(produit: str, ville: str, specialite: str = "") -> tuple[str, dict]:
    """Classe les professionnels à visiter en priorité pour présenter un produit.

    À utiliser dès que la question porte sur une visite pour un médicament ou une
    classe thérapeutique. Le score sur 100 combine la spécialité (concernée par le
    produit ?), la proximité (zone demandée ou arrondissement voisin), le mode
    d'exercice (libéral ou salarié) et la densité (confrères à la même adresse).

    Args:
        produit: médicament, substance ou classe thérapeutique présentée, par
            exemple « rivaroxaban », « Previscan », « anticoagulant ».
        ville: commune du Rhône, avec l'arrondissement pour Lyon (« Lyon 3e »).
        specialite: facultatif, pour ne classer qu'une spécialité (« cardiologue »).
            Laisser vide pour cibler toutes les spécialités concernées par le produit.
    """
    resultat = prioriser(produit, ville, specialite, limite=10)
    if "erreur" in resultat:
        return resultat["erreur"], resultat
    if resultat["total"] == 0:
        return f"Aucun professionnel ciblé trouvé ({resultat['zone']}).", resultat
    lignes = [f"{resultat['total']} professionnel(s) ciblé(s) ({resultat['specialite']}) pour "
              f"{produit}, {resultat['zone']} (source : Annuaire Santé RPPS). "
              f"Les {len(resultat['professionnels'])} premiers par score de priorité :"]
    for rang, pro in enumerate(resultat["professionnels"], 1):
        lignes.append(f"{rang}. {pro['nom']}, {pro['specialite']} - "
                      f"{pro['structure'] or 'Cabinet'}, {pro['adresse']}\n"
                      f"   Score {pro['score']}/100 = {pro['explication']}")
    return "\n".join(lignes), resultat


OUTILS = [chercher_professionnels, prioriser_visites, rechercher_medicament]
