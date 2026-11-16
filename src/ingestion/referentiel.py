"""Référentiel de la démo : médicaments courants et spécialités médicales concernées.

Le sous-ensemble de la BDPM est construit à partir de cette liste de substances
actives (DCI), regroupées par classe thérapeutique. Chaque classe indique les
spécialités médicales les plus concernées : cela sert au score de priorisation.

Les libellés « savoir-faire » du RPPS ont de nombreuses variantes (réformes
successives, options) : `groupe_specialite` les ramène à un nom unique.
"""

import re
import unicodedata

MG = "Médecine générale"
CARDIO = "Cardiologie"
VASCULAIRE = "Médecine vasculaire"
ENDOC = "Endocrinologie-diabétologie"
PNEUMO = "Pneumologie"
GASTRO = "Gastro-entérologie et hépatologie"
PSY = "Psychiatrie"
NEURO = "Neurologie"
RHUMATO = "Rhumatologie"
GERIATRIE = "Gériatrie"
ALLERGO = "Allergologie"
DERMATO = "Dermatologie et vénéréologie"
UROLOGIE = "Urologie"
GYNECO = "Gynécologie"
INFECTIO = "Maladies infectieuses et tropicales"
PEDIATRIE = "Pédiatrie"
NEPHRO = "Néphrologie"
ANESTH = "Anesthésie-réanimation"

# classe thérapeutique -> (spécialités concernées, substances actives)
CLASSES: dict[str, dict] = {
    "anticoagulant": {
        "specialites": [CARDIO, VASCULAIRE, MG, GERIATRIE],
        "substances": ["apixaban", "rivaroxaban", "dabigatran", "warfarine", "fluindione",
                       "acénocoumarol", "énoxaparine", "tinzaparine"],
    },
    "antiagrégant plaquettaire": {
        "specialites": [CARDIO, VASCULAIRE, NEURO, MG],
        "substances": ["clopidogrel", "ticagrélor", "prasugrel", "acétylsalicylate de lysine"],
    },
    "antihypertenseur": {
        "specialites": [CARDIO, MG, NEPHRO, GERIATRIE],
        "substances": ["ramipril", "périndopril", "énalapril", "losartan", "valsartan",
                       "irbésartan", "candésartan", "amlodipine", "lercanidipine",
                       "hydrochlorothiazide", "indapamide"],
    },
    "bêtabloquant": {
        "specialites": [CARDIO, MG],
        "substances": ["bisoprolol", "nébivolol", "aténolol", "propranolol", "métoprolol"],
    },
    "traitement de l'insuffisance cardiaque et antiarythmique": {
        "specialites": [CARDIO, MG, GERIATRIE],
        "substances": ["furosémide", "spironolactone", "amiodarone", "flécaïnide",
                       "digoxine", "sacubitril", "ivabradine"],
    },
    "hypolipémiant": {
        "specialites": [CARDIO, ENDOC, MG],
        "substances": ["atorvastatine", "rosuvastatine", "simvastatine", "pravastatine",
                       "ézétimibe", "fénofibrate"],
    },
    "antidiabétique": {
        "specialites": [ENDOC, MG, NEPHRO],
        "substances": ["metformine", "gliclazide", "glimépiride", "sitagliptine",
                       "vildagliptine", "dapagliflozine", "empagliflozine", "liraglutide",
                       "sémaglutide", "dulaglutide", "insuline glargine", "insuline asparte"],
    },
    "hormone thyroïdienne": {
        "specialites": [ENDOC, MG],
        "substances": ["lévothyroxine"],
    },
    "antalgique": {
        "specialites": [MG, RHUMATO, ANESTH, GERIATRIE],
        "substances": ["paracétamol", "tramadol", "codéine", "morphine", "oxycodone",
                       "néfopam"],
    },
    "anti-inflammatoire non stéroïdien": {
        "specialites": [MG, RHUMATO],
        "substances": ["ibuprofène", "kétoprofène", "diclofénac", "naproxène", "célécoxib"],
    },
    "corticoïde": {
        "specialites": [MG, RHUMATO, PNEUMO, ALLERGO],
        "substances": ["prednisolone", "prednisone", "méthylprednisolone", "bétaméthasone"],
    },
    "traitement de la goutte et de l'ostéoporose": {
        "specialites": [RHUMATO, MG, GERIATRIE],
        "substances": ["allopurinol", "fébuxostat", "colchicine", "acide alendronique",
                       "cholécalciférol"],
    },
    "antibiotique": {
        "specialites": [MG, INFECTIO, PEDIATRIE, PNEUMO],
        "substances": ["amoxicilline", "azithromycine", "clarithromycine", "ciprofloxacine",
                       "lévofloxacine", "doxycycline", "cefpodoxime", "ceftriaxone",
                       "fosfomycine", "pristinamycine", "métronidazole"],
    },
    "antiviral et antifongique": {
        "specialites": [MG, INFECTIO, DERMATO],
        "substances": ["valaciclovir", "aciclovir", "oseltamivir", "fluconazole",
                       "terbinafine"],
    },
    "traitement de l'asthme et de la BPCO": {
        "specialites": [PNEUMO, MG, ALLERGO, PEDIATRIE],
        "substances": ["salbutamol", "terbutaline", "budésonide", "fluticasone",
                       "formotérol", "salmétérol", "tiotropium", "montélukast"],
    },
    "antihistaminique": {
        "specialites": [ALLERGO, MG, DERMATO, PEDIATRIE],
        "substances": ["cétirizine", "lévocétirizine", "desloratadine", "loratadine",
                       "bilastine"],
    },
    "traitement gastro-intestinal": {
        "specialites": [GASTRO, MG],
        "substances": ["oméprazole", "ésoméprazole", "pantoprazole", "lansoprazole",
                       "dompéridone", "métoclopramide", "lopéramide", "macrogol",
                       "mésalazine"],
    },
    "antidépresseur": {
        "specialites": [PSY, MG],
        "substances": ["sertraline", "escitalopram", "paroxétine", "fluoxétine",
                       "venlafaxine", "duloxétine", "mirtazapine", "amitriptyline"],
    },
    "anxiolytique et hypnotique": {
        "specialites": [PSY, MG, GERIATRIE],
        "substances": ["alprazolam", "bromazépam", "oxazépam", "diazépam", "zolpidem",
                       "zopiclone", "hydroxyzine"],
    },
    "antipsychotique et thymorégulateur": {
        "specialites": [PSY],
        "substances": ["rispéridone", "olanzapine", "quétiapine", "aripiprazole",
                       "lithium"],
    },
    "antiépileptique et antiparkinsonien": {
        "specialites": [NEURO, PSY, MG],
        "substances": ["lévétiracétam", "lamotrigine", "valproate de sodium", "prégabaline",
                       "gabapentine", "carbamazépine", "lévodopa", "pramipexole"],
    },
    "traitement de la migraine et de la maladie d'Alzheimer": {
        "specialites": [NEURO, MG, GERIATRIE],
        "substances": ["sumatriptan", "zolmitriptan", "donépézil", "mémantine"],
    },
    "traitement urologique": {
        "specialites": [UROLOGIE, MG, GERIATRIE],
        "substances": ["tamsulosine", "alfuzosine", "finastéride", "dutastéride",
                       "solifénacine", "tadalafil", "sildénafil"],
    },
    "contraception et traitement hormonal": {
        "specialites": [GYNECO, MG],
        "substances": ["lévonorgestrel", "désogestrel", "estradiol", "progestérone"],
    },
}


def substances() -> list[tuple[str, str]]:
    """Liste à plat des couples (substance, classe thérapeutique)."""
    return [(s, classe) for classe, d in CLASSES.items() for s in d["substances"]]


# Regroupement des libellés RPPS : (motif sur le libellé sans accents, groupe)
_GROUPES = [
    (r"medecine generale", MG),
    (r"^(cardiologie|med cardiovasculaire)", CARDIO),
    (r"^endocrinologie", ENDOC),
    (r"^(urologie|chirurgie urologique)", UROLOGIE),
    (r"^anesthesie", ANESTH),
    (r"^(psychiatrie|neuro-psychiatrie)", PSY),
    (r"^pediatrie", PEDIATRIE),
    (r"^nephrologie", NEPHRO),
    (r"^gyneco", GYNECO),
    (r"^hematologie", "Hématologie"),
    (r"^oncologie", "Oncologie"),
    (r"^ophtalmologie", "Ophtalmologie"),
    (r"^(oto-rhino|o\.r\.l)", "ORL"),
    (r"^biologie medicale", "Biologie médicale"),
    (r"^(radio-diagnostic|radiologie)", "Radiologie"),
    (r"^medecine interne", "Médecine interne"),
    (r"^sante publique", "Santé publique"),
]


def sans_accents(texte: str) -> str:
    return unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower().strip()


def groupe_specialite(libelle: str) -> str:
    """Nom harmonisé d'une spécialité RPPS (ex. « Qualifié en Médecine Générale »)."""
    norm = sans_accents(libelle)
    for motif, groupe in _GROUPES:
        if re.search(motif, norm):
            return groupe
    return libelle.strip().capitalize() if libelle.isupper() else libelle.strip()
