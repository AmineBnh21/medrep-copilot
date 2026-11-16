"""Ingestion de l'Annuaire Santé (RPPS) : professionnels du Rhône.

Le fichier national (environ 830 Mo) est lu en flux depuis data.gouv.fr et
filtré à la volée : seules les lignes utiles à la démo sont écrites sur disque.

Usage :
    python -m src.ingestion.rpps
"""

from __future__ import annotations

import csv
import io
import sys

import pandas as pd
import requests
from tqdm import tqdm

from src.config import DEPARTEMENT, PROCESSED, PROFESSIONNELS_CSV, USER_AGENT
from src.ingestion.referentiel import groupe_specialite

# Lien stable data.gouv.fr vers « ps-libreacces-personne-activite.txt »
URL_RPPS = "https://www.data.gouv.fr/api/1/datasets/r/fffda7e9-0ea2-4c35-bba0-4496f3af935d"

PROFESSIONS = {"Médecin", "Pharmacien"}

# colonne du fichier source -> nom de colonne en sortie
COLONNES = {
    "Identifiant PP": "rpps",
    "Libellé civilité d'exercice": "titre",
    "Nom d'exercice": "nom",
    "Prénom d'exercice": "prenom",
    "Libellé profession": "profession",
    "Libellé type savoir-faire": "type_savoir_faire",
    "Libellé savoir-faire": "specialite",
    "Libellé mode exercice": "mode_exercice",
    "Raison sociale site": "structure",
    "Libellé secteur d'activité": "secteur_activite",
    "Numéro Voie (coord. structure)": "numero_voie",
    "Libellé type de voie (coord. structure)": "type_voie",
    "Libellé Voie (coord. structure)": "voie",
    "Code postal (coord. structure)": "code_postal",
    "Code commune (coord. structure)": "code_commune",
    "Libellé commune (coord. structure)": "commune",
}


def filtrer_flux(lignes, departement: str = DEPARTEMENT) -> list[dict]:
    """Garde les médecins et pharmaciens civils exerçant dans le département."""
    lecteur = csv.reader(lignes, delimiter="|", quoting=csv.QUOTE_NONE)
    entete = next(lecteur)
    idx = {nom: entete.index(nom) for nom in COLONNES}
    i_cp = idx["Code postal (coord. structure)"]
    i_insee = idx["Code commune (coord. structure)"]
    i_prof = idx["Libellé profession"]
    i_cat = entete.index("Libellé catégorie professionnelle")

    gardees = []
    for champs in lecteur:
        if len(champs) < len(entete):
            continue
        if not (champs[i_cp].startswith(departement) or champs[i_insee].startswith(departement)):
            continue
        if champs[i_prof] not in PROFESSIONS or champs[i_cat] != "Civil":
            continue
        gardees.append({sortie: champs[i].strip() for source, sortie in COLONNES.items()
                        for i in [idx[source]]})
    return gardees


def nettoyer(lignes: list[dict]) -> pd.DataFrame:
    """Une ligne par (professionnel, spécialité, lieu d'exercice)."""
    df = pd.DataFrame(lignes, dtype=str).fillna("")

    # Le pharmacien n'a pas de savoir-faire : on utilise sa profession.
    df.loc[df["specialite"] == "", "specialite"] = df["profession"]

    df["adresse"] = (
        (df["numero_voie"] + " " + df["type_voie"] + " " + df["voie"])
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    df = df.drop(columns=["numero_voie", "type_voie", "voie"])
    df = harmoniser(df)

    df = df.drop_duplicates(subset=["rpps", "specialite", "structure", "adresse", "code_postal"])
    return df.sort_values(["code_postal", "specialite", "nom", "prenom"]).reset_index(drop=True)


def harmoniser(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute la spécialité regroupée, la ville et l'arrondissement de Lyon."""
    df["specialite_groupe"] = df["specialite"].map(groupe_specialite)
    # Le RPPS écrit « Lib,indép,artis,com » pour l'exercice libéral.
    df["mode_exercice"] = df["mode_exercice"].replace({"Lib,indép,artis,com": "Libéral"})

    # Lyon : la commune vaut « Lyon » ou « Lyon 3e  Arrondissement » et le code postal
    # peut être un CEDEX. On lit l'arrondissement dans le code commune INSEE
    # (69381 à 69389), sinon dans le code postal (69001 à 69009).
    par_insee = df["code_commune"].str.fullmatch(r"6938[1-9]")
    par_cp = df["code_postal"].str.fullmatch(r"6900[1-9]")
    lyon = par_insee | par_cp | (df["code_commune"] == "69123")
    df["ville"] = df["commune"].str.replace(r"\s+", " ", regex=True).str.strip()
    df.loc[lyon, "ville"] = "Lyon"
    df["arrondissement"] = ""
    df.loc[par_cp, "arrondissement"] = df["code_postal"].str[-1]
    df.loc[par_insee, "arrondissement"] = df["code_commune"].str[-1]
    return df


def telecharger_et_filtrer(url: str = URL_RPPS) -> pd.DataFrame:
    with requests.get(url, stream=True, timeout=120, headers={"User-Agent": USER_AGENT}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)) or None
        r.raw.decode_content = True
        with tqdm.wrapattr(r.raw, "read", total=total, unit="o", unit_scale=True,
                           desc="RPPS", file=sys.stderr, mininterval=5) as flux:
            texte = io.TextIOWrapper(flux, encoding="utf-8", errors="replace", newline="")
            lignes = filtrer_flux(texte)
    return nettoyer(lignes)


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    df = telecharger_et_filtrer()
    df.to_csv(PROFESSIONNELS_CSV, index=False, encoding="utf-8")
    print(f"{len(df)} lignes, {df['rpps'].nunique()} professionnels -> {PROFESSIONNELS_CSV}")
    print(df["specialite_groupe"].value_counts().head(15).to_string())


if __name__ == "__main__":
    main()
