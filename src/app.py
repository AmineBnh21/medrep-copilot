"""Interface Streamlit de l'Assistant Terrain Santé.

Usage :
    streamlit run src/app.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pour « import src... »

from src.agent.graphe import creer_agent, repondre_en_flux  # noqa: E402
from src.config import CHROMA_DIR, RPPS_SQLITE  # noqa: E402

EXEMPLES = [
    "Je suis à Lyon 3e demain, je présente le rivaroxaban. Quels cardiologues visiter et "
    "que dois-je savoir sur le produit ?",
    "Quelles sont les interactions médicamenteuses de la warfarine ?",
    "Quels endocrinologues exercent à Villeurbanne ?",
    "Quel est le SMR du Previscan selon la HAS ?",
]

st.set_page_config(page_title="Assistant Terrain Santé", page_icon="🩺", layout="centered")


@st.cache_resource(show_spinner="Chargement de l'agent...")
def agent():
    return creer_agent()


def afficher_details(etapes: list[str], sources: list[dict]) -> None:
    """Étapes de raisonnement et passages sources, repliés sous la réponse."""
    if etapes:
        with st.expander(f"Raisonnement ({len(etapes)} étapes)"):
            for etape in etapes:
                st.markdown(f"- {etape}")
    if sources:
        with st.expander(f"Sources ({len(sources)})"):
            for s in sources:
                st.markdown(f"**[{s['numero']}] [{s['titre']}]({s['url']})**")
                st.caption(s["texte"][:700] + ("..." if len(s["texte"]) > 700 else ""))


# ---------------------------------------------------------------- en-tête

st.title("🩺 Assistant Terrain Santé")
st.caption("Préparation de visites pour délégués médicaux, à partir de l'open data santé "
           "(Annuaire Santé RPPS, BDPM, avis HAS). Périmètre de la démo : le Rhône.")

with st.sidebar:
    st.subheader("Exemples de questions")
    for exemple in EXEMPLES:
        if st.button(exemple, use_container_width=True):
            st.session_state.question_exemple = exemple
    if st.button("Nouvelle conversation", type="primary", use_container_width=True):
        st.session_state.pop("echanges", None)
        st.session_state.pop("conversation", None)
    st.divider()
    st.caption("Prototype de démonstration, pas un outil validé médicalement. Aucun conseil "
               "pour un patient : s'adresser à un professionnel de santé.")

if not (CHROMA_DIR.exists() and RPPS_SQLITE.exists()):
    st.error("Données absentes. Lancer d'abord : `python -m src.ingestion.bdpm`, "
             "`python -m src.ingestion.rpps`, `python -m src.tools.annuaire --charger` "
             "et `python -m src.rag.index`.")
    st.stop()

# ----------------------------------------------------------- conversation

st.session_state.setdefault("echanges", [])
st.session_state.setdefault("conversation", str(uuid.uuid4()))

for echange in st.session_state.echanges:
    with st.chat_message(echange["role"]):
        st.markdown(echange["texte"])
        afficher_details(echange.get("etapes", []), echange.get("sources", []))

question = st.chat_input("Votre question (ex. : cardiologues à Lyon 3e, contre-indications...)")
question = question or st.session_state.pop("question_exemple", None)

if question:
    st.session_state.echanges.append({"role": "user", "texte": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        resultat = None
        try:
            # Les étapes s'affichent au fur et à mesure : avec un débit LLM limité,
            # une question peut prendre plusieurs minutes.
            with st.status("Analyse de la question...", expanded=True) as suivi:
                for evenement in repondre_en_flux(agent(), question,
                                                  st.session_state.conversation):
                    if isinstance(evenement, dict):
                        resultat = evenement
                    else:
                        st.write(evenement)
                        suivi.update(label=evenement[:80])
                suivi.update(label="Réponse prête", state="complete", expanded=False)
        except Exception as erreur:
            # Le fil de la conversation peut être resté à mi-chemin : on repart d'un fil neuf.
            st.session_state.conversation = str(uuid.uuid4())
            st.error(f"La question n'a pas pu être traitée : {erreur}")
            st.stop()
        st.markdown(resultat["reponse"])
        afficher_details(resultat["etapes"], resultat["sources"])

    st.session_state.echanges.append({"role": "assistant", "texte": resultat["reponse"],
                                      "etapes": resultat["etapes"],
                                      "sources": resultat["sources"]})
