"""Score de priorisation des visites : simple et explicable (sur 100).

    spécialité      (40) : la spécialité du professionnel est-elle concernée par le produit ?
    proximité       (25) : exerce-t-il dans la zone demandée ou juste à côté ?
    mode d'exercice (20) : un libéral décide seul de ses prescriptions et se visite
                           plus facilement qu'un salarié d'établissement
    densité         (15) : combien de confrères de la même spécialité à la même adresse ?
                           (un seul déplacement, plusieurs visites)

Le score n'utilise que des données publiques : pas de volumes de prescription
ni d'historique de visites.

Usage :
    python -m src.tools.score rivaroxaban "Lyon 3e"
    python -m src.tools.score anticoagulant "Lyon 3e" cardiologue
"""

from __future__ import annotations

import sqlite3
import sys

import pandas as pd

from src.config import MEDICAMENTS_CSV
from src.ingestion.referentiel import CLASSES, sans_accents
from src.tools.annuaire import _connexion, resoudre_lieu, resoudre_specialite

POINTS_SPECIALITE = [40, 32, 24, 16]  # selon le rang de la spécialité pour la classe
POINTS_ZONE, POINTS_VOISIN = 25, 12
POINTS_MODE = {"Libéral": 20, "Salarié": 5}  # autres modes (bénévole) : 0
PALIERS_DENSITE = [(10, 15), (5, 11), (3, 7), (2, 4)]  # (nb à la même adresse, points)

# Arrondissements de Lyon limitrophes
VOISINS_LYON = {"1": "246", "2": "1357", "3": "2678", "4": "19", "5": "29",
                "6": "13", "7": "238", "8": "37", "9": "45"}


def classe_du_produit(produit: str) -> str | None:
    """Classe thérapeutique d'un médicament, d'une substance ou d'une classe nommée."""
    from src.rag.recherche import analyser

    cible = analyser(produit)
    if cible["classes"]:
        return cible["classes"][0]
    if cible["substances"]:
        meds = pd.read_csv(MEDICAMENTS_CSV, dtype=str)
        lignes = meds.loc[meds["substance"] == cible["substances"][0], "classe_therapeutique"]
        return lignes.iloc[0] if len(lignes) else None
    return None


def _points_densite(nb: int) -> int:
    return next((points for seuil, points in PALIERS_DENSITE if nb >= seuil), 0)


def prioriser(produit: str, ville: str, specialite: str = "", limite: int = 10) -> dict:
    """Professionnels à visiter en priorité pour un produit, dans une zone.

    `produit` : médicament, substance ou classe thérapeutique.
    `specialite` : facultatif, pour ne garder qu'une spécialité.
    """
    classe = classe_du_produit(produit)
    filtre = resoudre_specialite(specialite) if specialite else None
    if specialite and filtre is None:
        return {"erreur": f"Spécialité inconnue : « {specialite} »."}
    if classe is None and filtre is None:
        return {"erreur": f"Produit inconnu : « {produit} ». Indiquer un médicament de la base, "
                          f"une classe thérapeutique ({', '.join(CLASSES)}) ou une spécialité."}

    # Spécialités ciblées et leurs points
    if classe:
        cibles = dict(zip(CLASSES[classe]["specialites"], POINTS_SPECIALITE))
        if filtre:
            cibles = {filtre: cibles.get(filtre, 0)}
    else:
        cibles = {filtre: POINTS_SPECIALITE[0]}

    nom_ville, arrondissement = resoudre_lieu(ville)
    voisins = VOISINS_LYON.get(arrondissement, "")
    requete = (f"SELECT * FROM professionnels WHERE ville_norm = ? AND specialite_norm IN "
               f"({','.join('?' * len(cibles))})")
    parametres = [nom_ville, *map(sans_accents, cibles)]
    if arrondissement:
        zones = [arrondissement, *voisins]
        requete += f" AND arrondissement IN ({','.join('?' * len(zones))})"
        parametres += zones
    with _connexion() as con:
        con.row_factory = sqlite3.Row
        lignes = [dict(l) for l in con.execute(requete, parametres)]

    # Densité : professionnels distincts de la même spécialité à la même adresse
    par_adresse: dict[tuple, set] = {}
    for l in lignes:
        cle = (l["specialite_groupe"], l["adresse"], l["code_postal"])
        par_adresse.setdefault(cle, set()).add(l["rpps"])

    meilleurs: dict[str, dict] = {}
    for l in lignes:
        groupe = l["specialite_groupe"]
        pts_spe = cibles[groupe]
        dans_zone = not arrondissement or l["arrondissement"] == arrondissement
        pts_zone = POINTS_ZONE if dans_zone else POINTS_VOISIN
        nb = len(par_adresse[(groupe, l["adresse"], l["code_postal"])])
        pts_densite = _points_densite(nb)
        pts_mode = POINTS_MODE.get(l["mode_exercice"], 0)

        lieu = (f"Lyon {l['arrondissement']}e" if l["arrondissement"] else l["ville"])
        rang = list(cibles).index(groupe) + 1
        raisons = [
            (f"{groupe} : {pts_spe}/40" + (f" (spécialité n°{rang} pour : {classe})"
                                          if classe and not filtre else "")),
            f"{lieu} : {pts_zone}/25 ({'zone demandée' if dans_zone else 'arrondissement voisin'})",
            f"exercice {l['mode_exercice'].lower()} : {pts_mode}/20",
            (f"{nb} confrères de cette spécialité à cette adresse : {pts_densite}/15" if nb > 1
             else "seul de sa spécialité à cette adresse : 0/15"),
        ]
        candidat = {
            "rpps": l["rpps"], "score": pts_spe + pts_zone + pts_mode + pts_densite,
            "nb_adresse": nb,
            "nom": f"{l['titre']} {l['prenom'].title()} {l['nom']}".strip(),
            "specialite": groupe, "structure": l["structure"],
            "mode_exercice": l["mode_exercice"],
            "adresse": f"{l['adresse']}, {l['code_postal']} {l['ville']}".strip(", "),
            "explication": " ; ".join(raisons),
        }
        # Un professionnel à plusieurs lieux d'exercice : on garde le mieux noté.
        if candidat["score"] > meilleurs.get(l["rpps"], {"score": -1})["score"]:
            meilleurs[l["rpps"]] = candidat

    classement = sorted(meilleurs.values(),
                        key=lambda c: (-c["score"], -c["nb_adresse"], c["adresse"], c["nom"]))
    lieu = f"Lyon {arrondissement}e" if arrondissement else ville.strip()
    return {"produit": produit, "classe": classe, "lieu": lieu,
            "zone": (f"{lieu} et arrondissements voisins" if arrondissement else lieu),
            "specialite": filtre or ", ".join(cibles), "total": len(classement),
            "professionnels": classement[:limite]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    arguments = sys.argv[1:] or ["rivaroxaban", "Lyon 3e"]
    resultat = prioriser(*arguments)
    if "erreur" in resultat:
        sys.exit(resultat["erreur"])
    print(f"{resultat['total']} professionnels ciblés ({resultat['specialite']}) "
          f"pour {resultat['produit']}, {resultat['zone']}")
    for pro in resultat["professionnels"]:
        print(f"{pro['score']:>3}  {pro['nom']} - {pro['structure']}, {pro['adresse']}\n"
              f"     {pro['explication']}")
