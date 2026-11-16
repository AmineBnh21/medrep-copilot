"""Outil annuaire : recherche de professionnels de santé dans le RPPS (SQLite).

Usage :
    python -m src.tools.annuaire --charger              # CSV -> SQLite
    python -m src.tools.annuaire cardiologue "Lyon 3e"  # recherche
"""

from __future__ import annotations

import re
import sqlite3
import sys

import pandas as pd

from src.config import PROFESSIONNELS_CSV, RPPS_SQLITE
from src.ingestion import referentiel as ref
from src.ingestion.referentiel import sans_accents

# Façons courantes de nommer un praticien -> spécialité regroupée du RPPS
SYNONYMES = {
    "generaliste": ref.MG, "medecin traitant": ref.MG, "medecin de famille": ref.MG,
    "cardiologue": ref.CARDIO, "angiologue": ref.VASCULAIRE, "phlebologue": ref.VASCULAIRE,
    "diabetologue": ref.ENDOC, "endocrinologue": ref.ENDOC,
    "pneumologue": ref.PNEUMO, "gastro": ref.GASTRO, "hepatologue": ref.GASTRO,
    "psychiatre": ref.PSY, "neurologue": ref.NEURO, "rhumatologue": ref.RHUMATO,
    "geriatre": ref.GERIATRIE, "allergologue": ref.ALLERGO, "dermatologue": ref.DERMATO,
    "urologue": ref.UROLOGIE, "gynecologue": ref.GYNECO, "infectiologue": ref.INFECTIO,
    "pediatre": ref.PEDIATRIE, "nephrologue": ref.NEPHRO, "anesthesiste": ref.ANESTH,
    "pharmacie": "Pharmacien", "officine": "Pharmacien",
    "ophtalmo": "Ophtalmologie", "orl": "ORL", "radiologue": "Radiologie",
    "oncologue": "Oncologie", "cancerologue": "Oncologie", "interniste": "Médecine interne",
}


def charger_sqlite() -> int:
    """Charge le CSV des professionnels dans SQLite, avec des colonnes de recherche."""
    df = pd.read_csv(PROFESSIONNELS_CSV, dtype=str).fillna("")
    df["specialite_norm"] = df["specialite_groupe"].map(sans_accents)
    df["ville_norm"] = df["ville"].map(sans_accents)
    with sqlite3.connect(RPPS_SQLITE) as con:
        df.to_sql("professionnels", con, if_exists="replace", index=False)
        con.execute("CREATE INDEX idx_recherche ON professionnels "
                    "(specialite_norm, ville_norm, arrondissement)")
    return len(df)


def _connexion() -> sqlite3.Connection:
    if not RPPS_SQLITE.exists():
        charger_sqlite()
    con = sqlite3.connect(RPPS_SQLITE)
    con.row_factory = sqlite3.Row
    return con


def specialites_disponibles() -> list[str]:
    with _connexion() as con:
        return [l[0] for l in con.execute(
            "SELECT DISTINCT specialite_groupe FROM professionnels ORDER BY 1")]


def resoudre_specialite(texte: str) -> str | None:
    """« cardiologues » -> « Cardiologie ». None si la spécialité est inconnue."""
    t = sans_accents(texte)
    for synonyme, groupe in SYNONYMES.items():
        if re.search(rf"\b{synonyme}", t):
            return groupe
    disponibles = {sans_accents(s): s for s in specialites_disponibles()}
    if t in disponibles:
        return disponibles[t]
    # « cardiologie interventionnelle », « dermato » : on compare les débuts de mots
    racine = t.split()[0][:7] if t else ""
    proches = [s for norm, s in disponibles.items() if racine and norm.startswith(racine)]
    return min(proches, key=len) if proches else None


def resoudre_lieu(texte: str) -> tuple[str, str]:
    """« Lyon 3e », « Lyon 3ème arrondissement », « 69003 » -> (« lyon », « 3 »)."""
    t = sans_accents(texte)
    m = re.fullmatch(r"6900([1-9])", t)
    if m:
        return "lyon", m.group(1)
    m = re.search(r"lyon\s*(\d)\s*(er|e|eme)?\b", t)
    if m:
        return "lyon", m.group(1)
    return re.sub(r"\s+", " ", t), ""


def chercher_professionnels(specialite: str, ville: str, limite: int = 20) -> dict:
    """Professionnels d'une spécialité dans une ville du Rhône.

    `ville` accepte un arrondissement (« Lyon 3e »). Renvoie la spécialité et le
    lieu réellement utilisés, le nombre total de professionnels et les
    `limite` premiers, chacun avec ses lieux d'exercice.
    """
    groupe = resoudre_specialite(specialite)
    if groupe is None:
        return {"erreur": f"Spécialité inconnue : « {specialite} ».",
                "specialites_disponibles": specialites_disponibles()}
    nom_ville, arrondissement = resoudre_lieu(ville)

    requete = ("SELECT rpps, titre, nom, prenom, specialite, mode_exercice, structure, "
               "secteur_activite, adresse, code_postal, ville, arrondissement "
               "FROM professionnels WHERE specialite_norm = ? AND ville_norm = ?")
    parametres = [sans_accents(groupe), nom_ville]
    if arrondissement:
        requete += " AND arrondissement = ?"
        parametres.append(arrondissement)
    with _connexion() as con:
        lignes = con.execute(requete + " ORDER BY nom, prenom", parametres).fetchall()

    professionnels: dict[str, dict] = {}
    for l in lignes:
        pro = professionnels.setdefault(l["rpps"], {
            "rpps": l["rpps"], "nom": f"{l['titre']} {l['prenom'].title()} {l['nom']}".strip(),
            "specialite": l["specialite"], "lieux": []})
        pro["lieux"].append({
            "structure": l["structure"], "type": l["secteur_activite"],
            "mode_exercice": l["mode_exercice"],
            "adresse": f"{l['adresse']}, {l['code_postal']} {l['ville']}".strip(", ")})

    lieu = f"Lyon {arrondissement}e" if arrondissement else ville.strip()
    return {"specialite": groupe, "lieu": lieu, "total": len(professionnels),
            "professionnels": list(professionnels.values())[:limite]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:] == ["--charger"]:
        print(f"{charger_sqlite()} lignes -> {RPPS_SQLITE}")
    else:
        specialite, ville = (sys.argv[1:] + ["cardiologue", "Lyon 3e"])[-2:]
        resultat = chercher_professionnels(specialite, ville)
        if "erreur" in resultat:
            sys.exit(resultat["erreur"])
        print(f"{resultat['total']} professionnel(s) : {resultat['specialite']}, {resultat['lieu']}")
        for pro in resultat["professionnels"]:
            print(f"- {pro['nom']} ({pro['specialite']})")
            for lieu in pro["lieux"]:
                print(f"    {lieu['structure']} [{lieu['mode_exercice']}] {lieu['adresse']}")
