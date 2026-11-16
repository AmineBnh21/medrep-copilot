# Assistant Terrain Santé

> Agent IA (LangGraph + RAG) qui aide les délégués médicaux à préparer leurs visites à partir de l'open data santé (RPPS, BDPM).

**Statut :** prototype exploratoire, en cours de développement.

---

## 1. Le problème

Un délégué médical prépare chaque jour ses visites : quels professionnels de santé voir en priorité, et quelles informations fiables transmettre sur un produit. Ces informations sont dispersées entre plusieurs sources publiques et la préparation prend du temps.

## 2. La solution

Un assistant conversationnel qui, à partir d'une seule question, sait :

1. **Trouver les professionnels de santé** d'un secteur (annuaire RPPS).
2. **Les prioriser** avec un score simple et explicable.
3. **Répondre sur un médicament** (indications, contre-indications, interactions) avec les **sources citées** (BDPM).
4. **Générer une fiche de visite** : qui voir en premier et les messages clés.

### Exemple de question

> « Je suis à Lyon 3e demain, je présente un anticoagulant. Quels cardiologues visiter et que dois-je savoir sur le produit ? »

## 3. Fonctionnement de A à Z

Le projet se déroule en deux temps. **Hors ligne**, on prépare une fois les données (ingestion, index). **En ligne**, l'agent répond à chaque question en s'appuyant uniquement sur ces données.

```
 HORS LIGNE (une fois)                          EN LIGNE (à chaque question)

 BDPM ──► ingestion/bdpm.py ──► medicaments.csv           Question
                              └► rcp_sections.jsonl           │
                                      │                       ▼
                               rag/index.py              garde-fou ──► refus
                                      ▼                       │
                               base Chroma  ◄──────────┐      ▼
                                                       ├── routeur ◄──────────┐
 RPPS ──► ingestion/rpps.py ──► professionnels.csv     │      │               │
                                      │                │      ▼               │
                             tools/annuaire.py         └── outils        insuffisant
                                      ▼                       │               │
                                rpps.sqlite  ◄────────────────┤               │
                                                              ▼               │
                                                        vérification ─────────┘
                                                              │
                                                              ▼
                                                     fiche + sources
```

### 3.1 Préparer les médicaments (`src/ingestion/bdpm.py`)

1. **Télécharger les tables de la BDPM** : spécialités, compositions, conditions de prescription, remboursement, génériques, avis HAS (SMR et ASMR).
2. **Sélectionner les médicaments courants.** Le fichier `src/ingestion/referentiel.py` liste 155 substances actives regroupées en 24 classes thérapeutiques (anticoagulant, antidiabétique, antibiotique, ...). Pour chaque substance, on garde au plus 3 spécialités commercialisées, en privilégiant des marques différentes, car les dosages d'une même marque partagent presque le même texte. Résultat : **458 spécialités**.
3. **Récupérer le RCP.** Les tables ne contiennent pas les indications ni les contre-indications : elles sont dans le RCP (résumé des caractéristiques du produit), publié sur la page de chaque médicament. Le script télécharge cette page (avec un cache local) et la découpe en rubriques numérotées : 4.1 indications, 4.2 posologie, 4.3 contre-indications, 4.5 interactions, etc.
4. **Ajouter les avis HAS** (SMR, ASMR) comme rubriques supplémentaires.

Sorties : `medicaments.csv` (une ligne par spécialité) et `rcp_sections.jsonl` (12 148 rubriques, chacune avec le nom du médicament, la date de mise à jour et l'URL de la page source).

### 3.2 Préparer les professionnels (`src/ingestion/rpps.py`)

1. **Lire le fichier national du RPPS en flux** (830 Mo) depuis data.gouv.fr, sans le stocker : seules les lignes utiles sont gardées.
2. **Filtrer** : médecins et pharmaciens civils exerçant dans le Rhône.
3. **Harmoniser** :
   - les spécialités : le RPPS a de nombreuses variantes (« Spécialiste en Médecine Générale », « Qualifié en Médecine Générale »...) ramenées à un nom unique ;
   - le lieu : pour Lyon, l'arrondissement est lu dans le code commune INSEE, car le code postal peut être un CEDEX ;
   - le mode d'exercice : libéral ou salarié.

Sortie : `professionnels.csv`, soit **11 830 professionnels**, puis `rpps.sqlite` pour les recherches rapides.

### 3.3 Construire la base de recherche (`src/rag/`)

1. **Découpage** (`chunking.py`) : seules les rubriques utiles à un délégué sont gardées (4.1 à 4.9, 5.1, SMR, ASMR). Chaque rubrique est coupée en morceaux d'environ 900 caractères. Chaque morceau commence par un en-tête, par exemple « RIVAROXABAN EVOLUGEN 10 mg (rivaroxaban) - Contre-indications », pour rester compréhensible hors contexte.
2. **Embeddings** (`embeddings.py`) : chaque morceau est transformé en vecteur par un modèle multilingue qui tourne en local sur CPU. Aucune donnée ne quitte la machine et aucune clé n'est nécessaire.
3. **Index** (`index.py`) : les **18 164 morceaux** et leurs métadonnées (médicament, substance, classe, rubrique, source, URL) sont stockés dans Chroma.

### 3.4 Chercher dans les RCP (`src/rag/recherche.py`)

Une recherche purement vectorielle confond facilement deux médicaments proches, car tous les RCP se ressemblent. La recherche se fait donc en deux temps :

1. **Analyser la question** pour y repérer :
   - le médicament : substance ou marque (« Previscan » est reconnu comme fluindione) ;
   - la classe thérapeutique (« un anticoagulant ») ;
   - la rubrique visée (« contre-indications » renvoie à la 4.3, « femme enceinte » à la 4.6).
2. **Chercher par similarité** dans ce périmètre seulement. Si le filtre est trop strict pour trouver assez de passages, il est élargi : d'abord sans la rubrique, puis sans filtre.

Chaque passage retrouvé garde sa source : médicament, rubrique, date de mise à jour, URL.

### 3.5 Les trois outils de l'agent (`src/tools/`)

| Outil | Rôle | Source |
|---|---|---|
| `rechercher_medicament(question)` | passages du RCP ou des avis HAS répondant à la question | base Chroma |
| `chercher_professionnels(specialite, ville)` | liste des professionnels d'une spécialité dans une commune ou un arrondissement | SQLite |
| `prioriser_visites(produit, ville, specialite)` | professionnels à voir en priorité pour un produit, avec un score | SQLite + référentiel |

Les outils comprennent le langage courant : « cardiologue » devient Cardiologie, « Lyon 3e » ou « 69003 » devient le 3e arrondissement.

**Le score de priorisation** (`score.py`) note chaque professionnel sur 100 et affiche toujours sa justification :

| Critère | Points | Règle |
|---|---|---|
| Spécialité | 40 | rang de la spécialité parmi celles concernées par la classe du produit (40, 32, 24, 16) |
| Proximité | 25 | 25 dans la zone demandée, 12 dans un arrondissement de Lyon limitrophe |
| Mode d'exercice | 20 | libéral : 20 (décide seul de ses prescriptions, plus facile à rencontrer), salarié : 5 |
| Densité | 15 | confrères de la même spécialité à la même adresse (2 : 4 pts, 3 : 7, 5 : 11, 10 et plus : 15) |

Exemple de justification : « Cardiologie : 40/40 ; Lyon 3e : 25/25 (zone demandée) ; exercice libéral : 20/20 ; 2 confrères de cette spécialité à cette adresse : 4/15 », soit 89/100.

Le lien entre un produit et les spécialités à cibler vient du référentiel : un anticoagulant vise d'abord la cardiologie, puis la médecine vasculaire, la médecine générale et la gériatrie.

### 3.6 L'agent (`src/agent/graphe.py`)

L'agent est un graphe LangGraph de cinq nœuds. Le LLM (Mistral) décide et rédige ; les faits viennent toujours des outils.

1. **Garde-fou.** Le LLM classe la demande : préparation de visite, conseil pour un patient, ou hors sujet. Une demande de conseil médical individuel (« ma mère prend du Previscan, peut-elle... ») est refusée avec un message fixe qui renvoie vers un médecin ou un pharmacien. Aucun outil n'est alors appelé.
2. **Routeur.** Le LLM choisit les outils à appeler et leurs arguments. Pour une visite, il lance en parallèle le classement des professionnels et trois recherches sur le produit : indications, contre-indications, interactions. S'il manque une information indispensable (ville, spécialité), il la demande au lieu de la deviner.
3. **Outils.** Les appels sont exécutés et leurs résultats ajoutés à la conversation.
4. **Vérification.** Le LLM compare la question et les résultats. S'il manque une partie importante, il décrit ce manque et le graphe reboucle vers le routeur, une fois au maximum.
5. **Fiche.** Le LLM rédige la réponse à partir du « dossier » : les résultats d'outils, numérotés [1], [2], ... Il a pour consigne de n'utiliser que ce dossier et de citer le numéro de chaque source.

**Les sources sont ajoutées par le code, pas par le LLM** : après la rédaction, le programme repère les numéros cités et écrit lui-même la liste finale avec les titres et les URL exacts. Le modèle ne peut donc pas inventer ou tronquer un lien.

L'agent garde la mémoire de la conversation, ce qui permet les questions de suivi (« et dans le 6e ? »).

### 3.7 L'interface (`src/app.py`)

Une page de chat Streamlit. Pendant le traitement, les étapes s'affichent en direct (garde-fou, outils appelés, vérification). Sous chaque réponse, deux volets se déplient : le raisonnement suivi par l'agent et les passages sources avec leur lien.

### 3.8 Exemple complet

Question : « Je suis à Lyon 3e demain, je présente le rivaroxaban. Quels cardiologues visiter et que dois-je savoir sur le produit ? »

| Étape | Ce qui se passe |
|---|---|
| Garde-fou | demande acceptée : préparation de visite |
| Routeur | `prioriser_visites(rivaroxaban, Lyon 3e, cardiologue)` et trois `rechercher_medicament` (indications, contre-indications, interactions) |
| Outils | cardiologues classés par score ; 4 passages du RCP par recherche |
| Vérification | informations suffisantes |
| Fiche | « Qui voir » avec score et justification, « Messages clés », « Points de vigilance », puis les sources avec leur URL |

### 3.9 Ce qui garantit la fiabilité

- **Pas de connaissance propre du LLM** : il répond à partir des passages retrouvés, et dit quand le dossier ne permet pas de répondre.
- **Sources vérifiables** : chaque information renvoie à une rubrique datée du RCP, avec son lien.
- **Garde-fou** contre les conseils médicaux individuels.
- **Tests** (`pytest`, 41 tests) :
  - l'ingestion, sur de petits exemples ;
  - les outils et le score, sur les vraies données ;
  - le RAG, sur 10 questions types (bon médicament et bonne rubrique en tête) ;
  - le graphe, avec un LLM simulé qui rejoue des réponses prévues : cela valide les transitions (refus, reboucle bornée, mémoire), pas la qualité du modèle.

## 4. Stack technique

| Rôle | Outil |
|---|---|
| Orchestration de l'agent | LangGraph |
| Composants LLM / RAG | LangChain |
| Base vectorielle | Chroma |
| Embeddings | `paraphrase-multilingual-MiniLM-L12-v2` via fastembed (local, CPU, sans clé API) |
| Données tabulaires | pandas + SQLite |
| Interface | Streamlit |
| LLM | Mistral (API), modèle réglable dans `.env` |

## 5. Données utilisées

| Source | Contenu | Lien |
|---|---|---|
| BDPM | Base de données publique des médicaments (notices, compositions, indications) | https://base-donnees-publique.medicaments.gouv.fr |
| Annuaire Santé (RPPS) | Professionnels de santé : spécialité, lieu d'exercice | https://annuaire.sante.fr |

Pour la démo, on garde un **sous-ensemble** : 458 spécialités de médicaments courants (155 substances) et les 11 830 médecins et pharmaciens du Rhône.

**Données et RGPD :** uniquement des données publiques en open data, aucune donnée patient.

## 6. Structure du dépôt

```
assistant-terrain-sante/
├── data/
│   ├── raw/            # fichiers bruts téléchargés (non versionnés)
│   └── processed/      # données nettoyées
├── src/
│   ├── config.py       # chemins et constantes
│   ├── ingestion/
│   │   ├── referentiel.py  # substances, classes thérapeutiques, spécialités ciblées
│   │   ├── bdpm.py         # médicaments + rubriques des RCP
│   │   └── rpps.py         # professionnels du Rhône
│   ├── rag/
│   │   ├── chunking.py     # découpage des rubriques
│   │   ├── embeddings.py   # modèle d'embeddings local
│   │   ├── index.py        # construction de la base Chroma
│   │   └── recherche.py    # analyse de la question + recherche filtrée
│   ├── tools/
│   │   ├── annuaire.py     # recherche dans le RPPS (SQLite)
│   │   ├── score.py        # score de priorisation
│   │   └── outils.py       # les trois outils exposés au LLM
│   ├── agent/
│   │   ├── graphe.py       # graphe LangGraph (nœuds, état, transitions)
│   │   ├── prompts.py      # consignes données au LLM
│   │   └── llm.py          # connexion à Mistral
│   └── app.py          # interface Streamlit
├── notebooks/          # exploration des données
├── tests/              # tests unitaires, 10 questions types du RAG, scénarios de l'agent
├── .env.example        # modèle de configuration (clé API)
├── requirements.txt
└── README.md
```

## 7. Déroulement du projet

### Étape 1 : Données (vendredi)
- [x] Créer le dépôt et la structure de dossiers
- [x] Télécharger la BDPM et l'extraction de l'Annuaire Santé
- [x] Filtrer un sous-ensemble (médicaments courants, région lyonnaise)

### Étape 2 : RAG sur la BDPM (samedi)
- [x] Nettoyer les textes et les découper en chunks
- [x] Créer les embeddings et la base Chroma
- [x] Tester 10 questions types et vérifier que les sources sont citées (`tests/test_rag.py` : 10/10)

### Étape 3 : Agent LangGraph (dimanche)
- [x] Charger le RPPS dans SQLite et créer l'outil `chercher_professionnels(specialite, ville)`
- [x] Créer l'outil RAG `rechercher_medicament(question)`
- [x] Construire le graphe : routeur, outils, vérification, réponse
- [x] Ajouter le garde-fou (refus des conseils médicaux pour un patient)

### Étape 4 : Interface et priorisation (lundi)
- [x] Interface Streamlit : zone de chat, sources, étapes de raisonnement visibles
- [x] Score de priorisation simple et explicable (spécialité, proximité, mode d'exercice, densité)
- [x] Génération de la fiche de visite

### Étape 5 : Finitions (mardi)
- [ ] Compléter ce README (captures d'écran, exemples)
- [ ] Enregistrer un GIF ou une courte vidéo de démo
- [ ] Ajouter le projet au CV et à LinkedIn

### Étape 6 : Préparation de l'entretien (mercredi)
- [ ] Pitch de 2 minutes : problème, approche, résultat, limites
- [ ] Répéter la démo de bout en bout

**Règle de priorité :** si le temps manque, le score de priorisation passe en bonus. La démo et le README restent obligatoires.

## 8. Installation (Windows)

```powershell
git clone https://github.com/AmineBnh21/assistant-terrain-sante.git
cd assistant-terrain-sante
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # puis renseigner MISTRAL_API_KEY (clé gratuite sur console.mistral.ai)
```

Préparer les données (à faire une fois, environ 40 minutes au total) :

```powershell
python -m src.ingestion.bdpm          # médicaments courants + rubriques des RCP (environ 10 minutes)
python -m src.ingestion.rpps          # médecins et pharmaciens du Rhône (environ 5 minutes)
python -m src.tools.annuaire --charger  # professionnels dans SQLite
python -m src.rag.index               # découpage + embeddings + base Chroma (environ 20 minutes sur CPU)
pytest                                # 41 tests, dont les 10 questions types du RAG
```

| Fichier produit | Contenu |
|---|---|
| `data/processed/medicaments.csv` | une ligne par spécialité : substance, classe thérapeutique, composition, conditions de prescription, remboursement, SMR/ASMR |
| `data/processed/rcp_sections.jsonl` | une ligne par rubrique du RCP (4.1 indications, 4.3 contre-indications, 4.5 interactions, ...) et par avis HAS, avec l'URL source |
| `data/processed/professionnels.csv` | une ligne par professionnel, spécialité et lieu d'exercice (ville, arrondissement de Lyon) |
| `data/processed/rpps.sqlite` | les professionnels dans SQLite, pour l'outil annuaire |
| `data/chroma/` | base vectorielle des rubriques cliniques du RCP et des avis HAS |

Essayer les briques sans LLM :

```powershell
python -m src.rag.recherche "Quelles sont les contre-indications du rivaroxaban ?"
python -m src.tools.annuaire cardiologue "Lyon 3e"
python -m src.tools.score rivaroxaban "Lyon 3e"
python -m tests.test_rag       # résultat des 10 questions types
```

Interroger l'agent en ligne de commande (clé Mistral requise) :

```powershell
python -m src.agent.graphe "Je suis à Lyon 3e demain, je présente un anticoagulant. Quels cardiologues visiter et que dois-je savoir sur le produit ?"
```

La liste des substances retenues et le lien classe thérapeutique → spécialités médicales sont dans `src/ingestion/referentiel.py`.

Lancer l'interface :

```powershell
streamlit run src/app.py
```

Si le compte Mistral est limité en débit (erreur 429 « Rate limit exceeded »), choisir dans `.env` un modèle autorisé et indiquer le débit permis ; les appels sont alors espacés au lieu d'échouer :

```
LLM_MODEL=ministral-14b-latest
LLM_REQUETES_PAR_MINUTE=2
```

## 9. Limites connues

- Prototype de démonstration, pas un outil validé médicalement.
- Le score de priorisation est volontairement simple (pas de données d'activité réelles) : des confrères d'une même adresse obtiennent le même score.
- Une question demande au moins 4 appels au LLM : avec un compte Mistral limité à 2 requêtes par minute, une réponse prend 2 à 3 minutes.
- Les réponses dépendent de la qualité du découpage et de la recherche dans la BDPM.
- Les médicaments autorisés par procédure européenne centralisée (ex. Eliquis) n'ont pas leur RCP dans la BDPM : seuls leurs avis HAS sont indexés.

## 10. Pistes d'amélioration

- Interface vocale (speech-to-text puis text-to-speech) pour une utilisation en mobilité.
- Évaluation automatique du RAG (fidélité, pertinence des sources).
- Intégration d'autres sources open data santé (data.gouv.fr, Ameli).
- Moteur de Next Best Action basé sur l'historique des visites.

## Auteur

**Mohamed Amine BENHAMAID**, étudiant en Master 2 Intelligence Artificielle, Université Claude Bernard Lyon 1
[LinkedIn](https://www.linkedin.com/in/mohamed-amine-benhamaid) · [GitHub](https://github.com/AmineBnh21)
