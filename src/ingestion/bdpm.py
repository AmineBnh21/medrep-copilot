"""Ingestion de la BDPM : sous-ensemble de médicaments courants + textes des RCP.

Étapes :
  1. télécharger les fichiers tabulaires de la BDPM ;
  2. sélectionner les spécialités commercialisées des substances du référentiel ;
  3. récupérer la page de chaque spécialité et en extraire les rubriques du RCP
     (indications, contre-indications, interactions, ...).

Sorties :
  data/processed/medicaments.csv     une ligne par spécialité
  data/processed/rcp_sections.jsonl  une ligne par rubrique de texte, avec sa source

Usage :
    python -m src.ingestion.bdpm            # tout
    python -m src.ingestion.bdpm --sans-rcp # tables uniquement
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import time
import unicodedata

import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from src.config import BDPM_HTML, BDPM_RAW, MEDICAMENTS_CSV, PROCESSED, RCP_JSONL, USER_AGENT
from src.ingestion.referentiel import substances

BASE = "https://base-donnees-publique.medicaments.gouv.fr"
MAX_PAR_SUBSTANCE = 3

# fichier -> colonnes (les fichiers BDPM n'ont pas d'en-tête)
FICHIERS = {
    "CIS_bdpm.txt": ["cis", "denomination", "forme", "voies", "statut_amm", "type_procedure",
                     "etat_commercialisation", "date_amm", "statut_bdm", "num_autorisation_ue",
                     "titulaire", "surveillance_renforcee"],
    "CIS_COMPO_bdpm.txt": ["cis", "element", "code_substance", "substance", "dosage",
                           "reference_dosage", "nature", "num_liaison"],
    "CIS_CIP_bdpm.txt": ["cis", "cip7", "presentation", "statut_admin", "etat_commercialisation",
                         "date_commercialisation", "cip13", "agrement_collectivites",
                         "taux_remboursement", "prix_1", "prix_2", "prix_3",
                         "indications_remboursement"],
    "CIS_CPD_bdpm.txt": ["cis", "condition"],
    "CIS_GENER_bdpm.txt": ["id_groupe", "libelle_groupe", "cis", "type_generique", "tri"],
    "CIS_HAS_SMR_bdpm.txt": ["cis", "code_dossier", "motif", "date_avis", "valeur", "libelle"],
    "CIS_HAS_ASMR_bdpm.txt": ["cis", "code_dossier", "motif", "date_avis", "valeur", "libelle"],
}


def url_fiche(cis: str) -> str:
    return f"{BASE}/medicament/{cis}/extrait"


def normaliser(texte: str) -> str:
    """Majuscules sans accents, pour comparer les noms de substances."""
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return sans_accents.upper().strip()


# ---------------------------------------------------------------- 1. tables

def telecharger_tables(session: requests.Session, forcer: bool = False) -> None:
    BDPM_RAW.mkdir(parents=True, exist_ok=True)
    for nom in FICHIERS:
        cible = BDPM_RAW / nom
        if cible.exists() and not forcer:
            continue
        r = session.get(f"{BASE}/download/file/{nom}", timeout=120)
        r.raise_for_status()
        cible.write_bytes(r.content)


def lire_table(nom: str) -> pd.DataFrame:
    colonnes = FICHIERS[nom]
    df = pd.read_csv(BDPM_RAW / nom, sep="\t", header=None, names=colonnes, dtype=str,
                     encoding="cp1252", encoding_errors="replace", quoting=3,
                     index_col=False, on_bad_lines="skip").fillna("")
    return df.apply(lambda col: col.str.strip())


# ------------------------------------------------------------- 2. sélection

def _detecteur(substance: str):
    """Teste si un nom BDPM contient tous les mots de la substance.

    La BDPM écrit par exemple « DL-LYSINE (ACÉTYLSALICYLATE DE) » ou
    « ALENDRONIQUE (ACIDE) » : l'ordre des mots n'est pas fiable.
    """
    mots = [m for m in re.split(r"[\s']+", normaliser(substance)) if m not in ("DE", "D")]
    motifs = [re.compile(rf"\b{re.escape(m)}\b") for m in mots]
    return lambda nom: all(m.search(nom) for m in motifs)

def selectionner(max_par_substance: int = MAX_PAR_SUBSTANCE) -> pd.DataFrame:
    """Spécialités commercialisées contenant une substance du référentiel."""
    cis = lire_table("CIS_bdpm.txt")
    cis = cis[(cis["statut_amm"] == "Autorisation active")
              & (cis["etat_commercialisation"] == "Commercialisée")
              & ~cis["type_procedure"].str.contains("homéo", case=False)]

    compo = lire_table("CIS_COMPO_bdpm.txt")
    compo = compo[compo["cis"].isin(cis["cis"])].copy()
    compo["substance_norm"] = compo["substance"].map(normaliser)
    nb_substances = compo[compo["nature"] == "SA"].groupby("cis")["code_substance"].nunique()

    gener = lire_table("CIS_GENER_bdpm.txt").drop_duplicates("cis").set_index("cis")

    candidats = cis.set_index("cis")
    candidats["nb_substances"] = nb_substances.reindex(candidats.index).fillna(1).astype(int)
    candidats["princeps"] = gener["type_generique"].reindex(candidats.index).eq("0")
    # Les AMM européennes centralisées n'ont pas leur RCP dans la BDPM.
    candidats["centralisee"] = candidats["type_procedure"].str.contains("centralis", case=False)

    # Nom commercial = début de la dénomination, avant le dosage.
    candidats["marque"] = candidats["denomination"].str.extract(r"^([^\d,]+)")[0].str.strip()

    choisis, deja_pris = [], set()
    for substance, classe in substances():
        trouves = compo.loc[compo["substance_norm"].map(_detecteur(substance)), "cis"].unique()
        lot = candidats.loc[[c for c in trouves if c not in deja_pris]]
        lot = lot.sort_values(["centralisee", "nb_substances", "princeps", "denomination"],
                              ascending=[True, True, False, True])
        # Une spécialité par marque d'abord : les dosages d'une même marque
        # partagent presque le même RCP.
        lot = lot.assign(rang_marque=lot.groupby("marque").cumcount())
        lot = lot.sort_values("rang_marque", kind="stable").head(max_par_substance)
        for code, ligne in lot.iterrows():
            deja_pris.add(code)
            choisis.append({"cis": code, "substance": substance, "classe_therapeutique": classe,
                            **ligne[["denomination", "forme", "voies", "titulaire",
                                     "type_procedure", "date_amm"]].to_dict()})
        if lot.empty:
            print(f"  ! aucune spécialité trouvée pour : {substance}")

    df = pd.DataFrame(choisis)
    return enrichir(df, compo)


def enrichir(df: pd.DataFrame, compo: pd.DataFrame) -> pd.DataFrame:
    """Ajoute composition, conditions de prescription, remboursement et avis HAS."""
    sa = compo[compo["nature"] == "SA"].copy()
    sa["libelle"] = (sa["substance"] + " " + sa["dosage"]).str.strip()
    composition = sa.groupby("cis")["libelle"].agg(lambda s: " + ".join(dict.fromkeys(s)))

    cpd = lire_table("CIS_CPD_bdpm.txt").groupby("cis")["condition"].agg(" ; ".join)

    cip = lire_table("CIS_CIP_bdpm.txt")
    cip["taux_remboursement"] = cip["taux_remboursement"].str.replace(" ", "")
    taux = cip[cip["taux_remboursement"] != ""].groupby("cis")["taux_remboursement"].first()

    def dernier_avis(nom: str) -> pd.Series:
        avis = lire_table(nom)
        avis = avis[avis["date_avis"].str.fullmatch(r"\d{8}")].sort_values("date_avis")
        return avis.groupby("cis")["valeur"].last()

    df["composition"] = df["cis"].map(composition).fillna("")
    df["conditions_prescription"] = df["cis"].map(cpd).fillna("")
    df["taux_remboursement"] = df["cis"].map(taux).fillna("")
    df["smr"] = df["cis"].map(dernier_avis("CIS_HAS_SMR_bdpm.txt")).fillna("")
    df["asmr"] = df["cis"].map(dernier_avis("CIS_HAS_ASMR_bdpm.txt")).fillna("")
    df["url"] = df["cis"].map(url_fiche)
    return df


# ------------------------------------------------------------------ 3. RCP

def telecharger_fiche(session: requests.Session, cis: str, pause: float = 0.3) -> str:
    """Page HTML d'une spécialité, avec cache local compressé."""
    BDPM_HTML.mkdir(parents=True, exist_ok=True)
    cache = BDPM_HTML / f"{cis}.html.gz"
    if cache.exists():
        return gzip.decompress(cache.read_bytes()).decode("utf-8", errors="replace")
    r = session.get(url_fiche(cis), timeout=60)
    r.raise_for_status()
    cache.write_bytes(gzip.compress(r.content))
    time.sleep(pause)
    return r.content.decode("utf-8", errors="replace")


CLASSES_TITRE = {"AmmAnnexeTitre", "AmmAnnexeTitre1", "AmmAnnexeTitre2"}
RE_NUMERO = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$")


def _texte(element) -> str:
    texte = element.get_text(" ", strip=True).replace("\xa0", " ")
    texte = re.sub(r"\s+", " ", texte).strip()
    return re.sub(r"^[·•o§-]\s+", "- ", texte)  # puces Word


def extraire_rcp(html: str) -> tuple[str, list[dict]]:
    """Découpe le RCP d'une page en rubriques numérotées.

    Renvoie (date de mise à jour, [{"numero", "titre", "texte"}, ...]).
    """
    debut = html.find('id="tabpanel-rcp-panel"')
    if debut == -1:
        return "", []
    fin = html.find('id="tabpanel-notice-panel"', debut)
    bloc = html[debut: fin if fin != -1 else len(html)]
    depart = bloc.find('<a name="Rcp')
    if depart == -1:
        return "", []

    date = re.search(r"Mis à jour le\s*:\s*(\d{2}/\d{2}/\d{4})", bloc)
    soup = BeautifulSoup(bloc[bloc.rfind("<p", 0, depart):], "lxml")

    rubriques: list[dict] = []
    for el in soup.find_all(["p", "tr", "li"]):
        if el.name in ("p", "li") and el.find_parent("tr"):
            continue  # déjà pris en compte par la ligne du tableau
        if el.name == "li" and el.find("p"):
            continue
        if el.name == "tr":
            cellules = [_texte(c) for c in el.find_all(["td", "th"], recursive=False)]
            texte = " | ".join(c for c in cellules if c)
        else:
            texte = _texte(el)
        if not texte:
            continue

        classes = set(el.get("class") or [])
        if el.name == "p" and classes & CLASSES_TITRE and el.find("a", attrs={"name": True}):
            m = RE_NUMERO.match(texte)
            rubriques.append({"numero": m.group(1) if m else "",
                              "titre": m.group(2) if m else texte, "lignes": []})
        elif rubriques:
            rubriques[-1]["lignes"].append(texte)

    resultat = [{"numero": r["numero"], "titre": r["titre"], "texte": "\n".join(r["lignes"])}
                for r in rubriques if r["lignes"]]
    return (date.group(1) if date else ""), resultat


def sections_has(cis_retenus: set[str]) -> dict[str, list[dict]]:
    """Avis de la HAS (SMR et ASMR) sous forme de rubriques de texte."""
    sorties: dict[str, list[dict]] = {}
    for nom, sigle in (("CIS_HAS_SMR_bdpm.txt", "SMR"), ("CIS_HAS_ASMR_bdpm.txt", "ASMR")):
        avis = lire_table(nom)
        avis = avis[avis["cis"].isin(cis_retenus) & avis["date_avis"].str.fullmatch(r"\d{8}")]
        for cis, groupe in avis.sort_values("date_avis", ascending=False).groupby("cis"):
            lignes = [
                f"Avis du {a.date_avis[6:]}/{a.date_avis[4:6]}/{a.date_avis[:4]} ({a.motif}) - "
                f"{sigle} {a.valeur} : {re.sub(r'<[^>]+>', ' ', a.libelle).strip()}"
                for a in groupe.head(3).itertuples()
            ]
            sorties.setdefault(cis, []).append({
                "numero": sigle, "source": "HAS",
                "titre": f"Avis de la HAS : {sigle}", "texte": "\n".join(lignes)})
    return sorties


def construire_sections(medicaments: pd.DataFrame, session: requests.Session) -> list[dict]:
    has = sections_has(set(medicaments["cis"]))
    sections = []
    for med in tqdm(list(medicaments.itertuples()), desc="RCP", mininterval=5):
        commun = {"cis": med.cis, "denomination": med.denomination, "substance": med.substance,
                  "classe_therapeutique": med.classe_therapeutique, "url": med.url}
        try:
            date, rubriques = extraire_rcp(telecharger_fiche(session, med.cis))
        except requests.RequestException as erreur:
            print(f"  ! {med.cis} {med.denomination} : {erreur}")
            date, rubriques = "", []
        for r in rubriques:
            sections.append({**commun, "source": "RCP (ANSM)", "date_maj": date, **r})
        for r in has.get(med.cis, []):
            sections.append({**commun, "date_maj": "", **r})
    return sections


# --------------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sans-rcp", action="store_true", help="ne pas récupérer les RCP")
    parser.add_argument("--forcer", action="store_true", help="retélécharger les tables")
    args = parser.parse_args()

    PROCESSED.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    telecharger_tables(session, forcer=args.forcer)
    medicaments = selectionner()
    print(f"{len(medicaments)} spécialités, {medicaments['substance'].nunique()} substances, "
          f"{medicaments['classe_therapeutique'].nunique()} classes")

    if not args.sans_rcp:
        sections = construire_sections(medicaments, session)
        avec_rcp = {s["cis"] for s in sections if s["source"].startswith("RCP")}
        medicaments["rcp_disponible"] = medicaments["cis"].isin(avec_rcp)
        with RCP_JSONL.open("w", encoding="utf-8") as f:
            for s in sections:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print(f"{len(sections)} rubriques, RCP disponible pour {len(avec_rcp)} spécialités "
              f"-> {RCP_JSONL}")

    medicaments.to_csv(MEDICAMENTS_CSV, index=False, encoding="utf-8")
    print(f"-> {MEDICAMENTS_CSV}")


if __name__ == "__main__":
    main()
