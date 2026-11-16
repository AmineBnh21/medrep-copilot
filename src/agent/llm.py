"""Accès au LLM (Mistral)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

from src.config import RACINE

MODELE_PAR_DEFAUT = "mistral-small-latest"


def creer_llm():
    """LLM de l'agent. La clé est lue dans le fichier .env (MISTRAL_API_KEY)."""
    from langchain_mistralai import ChatMistralAI

    load_dotenv(RACINE / ".env")
    if not os.getenv("MISTRAL_API_KEY"):
        raise RuntimeError(
            "Clé Mistral absente : copier .env.example en .env puis renseigner MISTRAL_API_KEY "
            "(clé gratuite sur https://console.mistral.ai).")
    # Certains comptes Mistral sont limités à quelques requêtes par minute :
    # LLM_REQUETES_PAR_MINUTE espace alors les appels au lieu d'échouer en 429.
    limiteur = None
    par_minute = float(os.getenv("LLM_REQUETES_PAR_MINUTE") or 0)
    if par_minute:
        from langchain_core.rate_limiters import InMemoryRateLimiter

        limiteur = InMemoryRateLimiter(requests_per_second=par_minute / 60,
                                       check_every_n_seconds=0.5, max_bucket_size=1)
    return ChatMistralAI(model=os.getenv("LLM_MODEL") or MODELE_PAR_DEFAUT,
                         temperature=0, max_retries=5, rate_limiter=limiteur)
