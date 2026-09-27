"""
Accès au modèle de langage local (Qwen2.5-1.5B-Instruct via transformers).

Trois choix importants :
1. Chargement UNE SEULE FOIS, au démarrage du service (lifespan dans
   main.py), jamais à l'import : importer ce module dans un test ne
   télécharge pas 3 Go de poids.
2. Inférence dans un thread (asyncio.to_thread) : le calcul est long et
   bloquant ; le déléguer libère la boucle d'événements, qui continue de
   servir les autres requêtes (accueil, appels Django...).
3. Une seule inférence à la fois (verrou) : le pipeline n'est pas prévu
   pour être appelé en parallèle et, sur CPU, deux générations
   simultanées sont chacune deux fois plus lentes. Une requête qui attend
   trop longtemps son tour abandonne et bascule sur les règles.
"""
import asyncio
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class ModeleIndisponible(Exception):
    """Le modèle n'est pas chargé, ou est trop occupé pour répondre à temps."""


_pipeline: Any = None
_verrou = asyncio.Lock()


def charger_modele() -> None:
    """Appelé au démarrage. En cas d'échec, le service tourne en mode dégradé (règles seules)."""
    global _pipeline
    try:
        from transformers import pipeline  # import lourd, uniquement si on charge vraiment le modèle

        _pipeline = pipeline(
            "text-generation",
            model=settings.model_name,
            device_map="auto",
            token=settings.huggingface_api_key,
        )
        logger.info("Modèle %s chargé.", settings.model_name)
    except Exception:
        logger.exception("Impossible de charger le modèle : l'assistant fonctionnera avec les règles seules.")
        _pipeline = None


def modele_disponible() -> bool:
    return _pipeline is not None


async def generer(
    messages: list[dict[str, str]],
    max_new_tokens: int = 150,
    temperature: float | None = None,
) -> str:
    """
    Génère la réponse de l'assistant pour une conversation au format chat.
    temperature=None -> décodage glouton (déterministe), idéal pour
    l'extraction JSON ; une température > 0 donne des réponses plus
    naturelles pour la conversation libre.
    """
    if _pipeline is None:
        raise ModeleIndisponible("Modèle non chargé.")

    options: dict[str, Any] = {"max_new_tokens": max_new_tokens, "return_full_text": False}
    if temperature:
        options.update(do_sample=True, temperature=temperature, top_p=0.9)
    else:
        options.update(do_sample=False)

    try:
        await asyncio.wait_for(_verrou.acquire(), timeout=settings.model_queue_timeout)
    except TimeoutError:
        raise ModeleIndisponible("File d'attente du modèle saturée.")
    try:
        resultat = await asyncio.to_thread(_pipeline, messages, **options)
    finally:
        _verrou.release()

    genere = resultat[0]["generated_text"]
    # Selon la version de transformers, on reçoit le texte seul ou la
    # conversation complète (dont le dernier message est la réponse).
    return genere[-1]["content"] if isinstance(genere, list) else str(genere)
