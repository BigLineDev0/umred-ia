import asyncio
from transformers import pipeline

MODEL_NAME = "Qwen/Qwen2.5-1.5B-Instruct"

# Chargé UNE SEULE FOIS, au démarrage du service — pas à chaque requête.
# Charger un modèle de ce poids prend plusieurs secondes ; le refaire à
# chaque message rendrait l'assistant totalement inutilisable.
_pipeline = pipeline("text-generation", model=MODEL_NAME, device_map="auto")


async def generer(messages: list[dict], max_new_tokens: int = 150, do_sample: bool = False) -> str:
    """
    Exécute l'inférence dans un thread à part : c'est le correctif du
    bug de blocage. `asyncio.to_thread` délègue ce calcul lourd à un
    thread du pool, libérant immédiatement la boucle d'événements
    FastAPI pour continuer à traiter d'autres requêtes en parallèle.
    """
    resultat = await asyncio.to_thread(
        _pipeline, messages, max_new_tokens=max_new_tokens, do_sample=do_sample
    )
    generated = resultat[0]["generated_text"]
    return generated[-1]["content"] if isinstance(generated, list) else generated