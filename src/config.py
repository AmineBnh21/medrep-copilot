"""Chemins et constantes partagés par tout le projet."""

from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

DATA = RACINE / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"

# BDPM
BDPM_RAW = RAW / "bdpm"
BDPM_HTML = BDPM_RAW / "html"
MEDICAMENTS_CSV = PROCESSED / "medicaments.csv"
RCP_JSONL = PROCESSED / "rcp_sections.jsonl"

# RAG : base vectorielle Chroma et modèle d'embeddings (local, multilingue, sans clé API)
CHROMA_DIR = DATA / "chroma"
COLLECTION = "bdpm_rcp"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# RPPS
PROFESSIONNELS_CSV = PROCESSED / "professionnels.csv"
RPPS_SQLITE = PROCESSED / "rpps.sqlite"

# Périmètre de la démo : le département du Rhône (région lyonnaise)
DEPARTEMENT = "69"

# Identification polie auprès des serveurs open data
USER_AGENT = "assistant-terrain-sante/0.1 (prototype etudiant, open data)"
