"""
Accès au modèle de langage local (Qwen2.5-1.5B-Instruct via transformers).

Quatre choix importants :
1. Chargement UNE SEULE FOIS, au démarrage du service (lifespan dans
   main.py), jamais à l'import : importer ce module dans un test ne
   télécharge pas 3 Go de poids.
2. Inférence dans un thread (asyncio.to_thread) : le calcul est long et
   bloquant ; le déléguer libère la boucle d'événements, qui continue de
   servir les autres requêtes (accueil, appels Django...).
3. Une seule inférence à la fois (verrou) : le modèle n'est pas prévu
   pour être appelé en parallèle et, sur CPU, deux générations
   simultanées sont chacune deux fois plus lentes. Une requête qui attend
   trop longtemps son tour abandonne et bascule sur les règles.
4. Prompts système mis en cache : sur CPU, l'essentiel du temps passait à
   relire le prompt d'extraction (~800 tokens d'exemples) à chaque
   message. Ce prompt ne change jamais : son calcul (cache clé/valeur du
   modèle) est fait une fois au démarrage puis réutilisé, seul le message
   de l'utilisateur reste à lire. Mesuré : 60-75 s -> ~10 s par extraction.
"""
import asyncio
import copy
import logging
import threading
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class ModeleIndisponible(Exception):
    """Le modèle n'est pas chargé, ou est trop occupé pour répondre à temps."""


_modele: Any = None
_tokenizer: Any = None
# Texte du prompt système -> (tokens du préfixe, cache clé/valeur calculé).
_prefixes: dict[str, tuple[Any, Any]] = {}
_verrou = asyncio.Lock()
# Levé une fois le modèle chargé ET les prompts système calculés.
_modele_pret = threading.Event()


def _calculer_prefixe(prompt_systeme: str) -> None:
    """
    Calcule le cache du message système seul : c'est le début commun de
    toutes les conversations qui utilisent ce prompt.
    """
    import torch
    from transformers import DynamicCache

    ids = _tokenizer.apply_chat_template(
        [{"role": "system", "content": prompt_systeme}], return_tensors="pt", return_dict=True
    )["input_ids"]
    with torch.no_grad():
        # logits_to_keep=1 : seuls les états internes nous intéressent ;
        # sans cela, les scores des ~800 tokens sur tout le vocabulaire
        # coûtent ~500 Mo de mémoire pour rien.
        sortie = _modele(ids, past_key_values=DynamicCache(), use_cache=True, logits_to_keep=1)
    _prefixes[prompt_systeme] = (ids, sortie.past_key_values)


def charger_modele(prompts_systeme: list[str] = ()) -> None:
    """
    Appelé au démarrage. Le modèle n'est déclaré disponible qu'une fois les
    prompts système calculés : d'ici là, les requêtes passent par les règles
    au lieu d'attendre. En cas d'échec, le service tourne en mode dégradé.
    """
    global _modele, _tokenizer
    try:
        # Import lourd, uniquement si on charge vraiment le modèle.
        from transformers import AutoModelForCausalLM, AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained(settings.model_name, token=settings.huggingface_api_key)
        # dtype="auto" : bfloat16 pour Qwen. Mesuré sur CPU AVX2 : float32
        # lit le prompt plus vite mais génère deux fois plus lentement ;
        # avec le cache des prompts, bfloat16 est le plus rapide.
        modele = AutoModelForCausalLM.from_pretrained(
            settings.model_name, dtype="auto", token=settings.huggingface_api_key
        )
        modele.eval()
        _modele = modele
        for prompt in prompts_systeme:
            _calculer_prefixe(prompt)
        logger.info("Modèle %s chargé (%d prompts système en cache).", settings.model_name, len(_prefixes))
    except Exception:
        logger.exception("Impossible de charger le modèle : l'assistant fonctionnera avec les règles seules.")
        _modele = None
        return
    _modele_pret.set()


def modele_disponible() -> bool:
    return _modele_pret.is_set()


def _generer_sync(messages: list[dict[str, str]], options: dict[str, Any]) -> str:
    import torch

    ids = _tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
    )["input_ids"]

    prefixe = _prefixes.get(messages[0]["content"]) if messages[0]["role"] == "system" else None
    if prefixe is not None:
        ids_prefixe, cache = prefixe
        n = ids_prefixe.shape[1]
        # Le cache n'est valable que si la conversation commence exactement
        # par les mêmes tokens ; sinon on calcule tout, sans cache.
        if ids.shape[1] > n and torch.equal(ids[0, :n], ids_prefixe[0]):
            # Copie : la génération complète le cache, l'original doit rester intact.
            options = {**options, "past_key_values": copy.deepcopy(cache)}

    with torch.no_grad():
        sortie = _modele.generate(
            ids,
            attention_mask=torch.ones_like(ids),
            pad_token_id=_tokenizer.eos_token_id,
            **options,
        )
    return _tokenizer.decode(sortie[0, ids.shape[1]:], skip_special_tokens=True)


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
    if not modele_disponible():
        raise ModeleIndisponible("Modèle non chargé.")

    options: dict[str, Any] = {"max_new_tokens": max_new_tokens}
    if temperature:
        options.update(do_sample=True, temperature=temperature, top_p=0.9)
    else:
        options.update(do_sample=False, temperature=None, top_p=None, top_k=None)

    try:
        await asyncio.wait_for(_verrou.acquire(), timeout=settings.model_queue_timeout)
    except TimeoutError:
        raise ModeleIndisponible("File d'attente du modèle saturée.")
    try:
        return await asyncio.to_thread(_generer_sync, messages, options)
    finally:
        _verrou.release()
