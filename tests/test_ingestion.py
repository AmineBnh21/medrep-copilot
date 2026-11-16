"""Tests des fonctions d'ingestion (sans accès réseau)."""

from src.ingestion.bdpm import _detecteur, extraire_rcp, normaliser
from src.ingestion.referentiel import CLASSES, groupe_specialite, substances
from src.ingestion.rpps import filtrer_flux, nettoyer

PAGE = """
<div id="tabpanel-rcp-panel"><nav><li><a href="#x">4.3. Contre-indications</a></li></nav>
<p class="DateNotif">ANSM - Mis à jour le : 14/11/2024</p>
<p class="AmmAnnexeTitre1"><a name="RcpDonneesCliniques"><span>4. DONNEES CLINIQUES</span></a></p>
<p class="AmmAnnexeTitre2"><a name="RcpIndicTherap"><span>4.1. Indications thérapeutiques</span></a></p>
<p class=AmmCorpsTexte>Prévention des accidents vasculaires cérébraux.</p>
<p class="AmmAnnexeTitre2"><a name="RcpContreIndic"><span>4.3. Contre-indications</span></a></p>
<p class=AmmListePuces1><span>·<span>&nbsp;&nbsp;&nbsp;</span></span>Saignement évolutif.</p>
<table><tr><td><p>Classe</p></td><td><p>Fréquence</p></td></tr></table>
</div>
<div id="tabpanel-notice-panel"><p class="AmmCorpsTexte">Texte de la notice.</p></div>
"""


def test_extraire_rcp_decoupe_par_rubrique():
    date, rubriques = extraire_rcp(PAGE)
    assert date == "14/11/2024"
    assert [(r["numero"], r["titre"]) for r in rubriques] == [
        ("4.1", "Indications thérapeutiques"),
        ("4.3", "Contre-indications"),
    ]
    assert rubriques[0]["texte"] == "Prévention des accidents vasculaires cérébraux."
    assert rubriques[1]["texte"] == "- Saignement évolutif.\nClasse | Fréquence"


def test_extraire_rcp_ignore_la_notice_et_les_pages_sans_rcp():
    _, rubriques = extraire_rcp(PAGE)
    assert all("notice" not in r["texte"] for r in rubriques)
    assert extraire_rcp("<html><p>Pas de RCP</p></html>") == ("", [])


def test_detecteur_ne_depend_pas_de_l_ordre_des_mots():
    assert _detecteur("acétylsalicylate de lysine")(normaliser("DL-LYSINE (ACÉTYLSALICYLATE DE)"))
    assert _detecteur("acide alendronique")(normaliser("ALENDRONIQUE (ACIDE)"))
    assert not _detecteur("prednisolone")(normaliser("MÉTHYLPREDNISOLONE"))
    assert not _detecteur("estradiol")(normaliser("ÉTHINYLESTRADIOL"))


def test_groupe_specialite():
    assert groupe_specialite("Qualifié en Médecine Générale") == "Médecine générale"
    assert groupe_specialite("Méd cardiovasculaire opt card interventionnelle") == "Cardiologie"
    assert groupe_specialite("Chirurgie urologique") == "Urologie"
    assert groupe_specialite("Neurologie") == "Neurologie"  # ne doit pas devenir « Urologie »
    assert groupe_specialite("Chirurgie thoracique et cardio-vasculaire") == (
        "Chirurgie thoracique et cardio-vasculaire")


def test_referentiel_sans_doublon():
    noms = [s for s, _ in substances()]
    assert len(noms) == len(set(noms))
    assert all(d["specialites"] for d in CLASSES.values())


ENTETE = ("Identifiant PP|Libellé civilité d'exercice|Nom d'exercice|Prénom d'exercice|"
          "Libellé profession|Libellé catégorie professionnelle|Libellé type savoir-faire|"
          "Libellé savoir-faire|Libellé mode exercice|Raison sociale site|"
          "Libellé secteur d'activité|Numéro Voie (coord. structure)|"
          "Libellé type de voie (coord. structure)|Libellé Voie (coord. structure)|"
          "Code postal (coord. structure)|Code commune (coord. structure)|"
          "Libellé commune (coord. structure)")


def _ligne(rpps, profession, categorie, specialite, cp, insee, commune):
    return (f"{rpps}|Docteur|DUPONT|ANNE|{profession}|{categorie}|S|{specialite}|Salarié|"
            f"HOPITAL TEST|Hôpital|5|Place|D'ARSONVAL|{cp}|{insee}|{commune}")


def test_filtrer_et_nettoyer_rpps():
    lignes = [
        ENTETE,
        _ligne("1", "Médecin", "Civil", "Cardiologie et maladies vasculaires",
               "69437", "69383", "Lyon 3e  Arrondissement"),          # CEDEX de Lyon 3e
        _ligne("1", "Médecin", "Civil", "Cardiologie et maladies vasculaires",
               "69437", "69383", "Lyon 3e  Arrondissement"),          # doublon
        _ligne("2", "Pharmacien", "Civil", "", "69100", "69266", "Villeurbanne"),
        _ligne("3", "Médecin", "Civil", "Pédiatrie", "75012", "75112", "Paris"),   # hors Rhône
        _ligne("4", "Infirmier", "Civil", "", "69003", "69383", "Lyon"),           # profession
        _ligne("5", "Médecin", "Etudiant", "", "69003", "69383", "Lyon"),          # étudiant
    ]
    df = nettoyer(filtrer_flux(iter(lignes)))

    assert sorted(df["rpps"]) == ["1", "2"]
    cardio = df[df["rpps"] == "1"].iloc[0]
    assert (cardio["ville"], cardio["arrondissement"]) == ("Lyon", "3")
    assert cardio["specialite_groupe"] == "Cardiologie"
    assert cardio["adresse"] == "5 Place D'ARSONVAL"
    pharmacien = df[df["rpps"] == "2"].iloc[0]
    assert pharmacien["specialite_groupe"] == "Pharmacien"
    assert (pharmacien["ville"], pharmacien["arrondissement"]) == ("Villeurbanne", "")
