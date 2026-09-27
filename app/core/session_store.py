"""
Mémoire de conversation : pour chaque session, on retient l'ÉTAPE du
dialogue en cours (ex. « en attente de confirmation ») et le CONTEXTE
nécessaire pour la poursuivre (ex. la réservation prête à être envoyée).

C'est une machine à états : une seule étape active à la fois, ce qui
évite les incohérences d'une série de drapeaux booléens indépendants
(deux "attentes" actives en même temps, drapeau jamais remis à zéro...).

Limites assumées pour la V1 : stockage en mémoire, donc perdu au
redémarrage et propre à un seul processus. Le passage à Redis (une clé
par session avec expiration) ne changerait que ce module.
"""
import time
from collections import OrderedDict
from enum import StrEnum
from typing import Any

from app.core.config import settings

HISTORIQUE_MAX_MESSAGES = 6


class Etape(StrEnum):
    COLLECTE_RESERVATION = "collecte_reservation"
    SELECTION_EQUIPEMENT = "selection_equipement"
    SELECTION_DISPONIBILITE = "selection_disponibilite"
    CONFIRMATION_RESERVATION = "confirmation_reservation"
    SELECTION_ALTERNATIVE = "selection_alternative"
    SELECTION_ANNULATION = "selection_annulation"
    CONFIRMATION_ANNULATION = "confirmation_annulation"


# OrderedDict utilisé comme cache LRU : la session la moins récemment
# utilisée est toujours en tête, ce qui rend la purge peu coûteuse.
_sessions: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def cle_session(user_id: int, session_id: str) -> str:
    """
    La clé combine l'utilisateur ET l'identifiant fourni par le navigateur.
    Sans cela, quelqu'un qui devinerait (ou volerait) le session_id d'un
    autre utilisateur pourrait reprendre sa conversation et confirmer une
    action préparée pour lui.
    """
    return f"{user_id}:{session_id}"


def _nouvelle_session() -> dict[str, Any]:
    return {"etape": None, "contexte": {}, "historique": [], "derniere_activite": time.monotonic()}


def _purger(maintenant: float) -> None:
    ttl = settings.session_ttl_minutes * 60
    # Sessions expirées : elles sont forcément en tête (ordre LRU).
    while _sessions:
        premiere = next(iter(_sessions.values()))
        if maintenant - premiere["derniere_activite"] <= ttl:
            break
        _sessions.popitem(last=False)
    # Borne dure sur la mémoire : protège contre un client qui créerait
    # des milliers de sessions pour épuiser la RAM du serveur.
    while len(_sessions) > settings.session_max:
        _sessions.popitem(last=False)


def get_session(cle: str) -> dict[str, Any]:
    maintenant = time.monotonic()
    _purger(maintenant)
    session = _sessions.get(cle)
    if session is None:
        session = _sessions[cle] = _nouvelle_session()
    _sessions.move_to_end(cle)
    session["derniere_activite"] = maintenant
    return session


def definir_etape(session: dict[str, Any], etape: Etape, **contexte: Any) -> None:
    session["etape"] = etape
    session["contexte"] = contexte


def terminer_etape(session: dict[str, Any]) -> None:
    session["etape"] = None
    session["contexte"] = {}


def ajouter_historique(session: dict[str, Any], message: str, reponse: str) -> None:
    """Garde les derniers échanges pour donner du contexte au modèle conversationnel."""
    historique = session["historique"]
    historique.append({"role": "user", "content": message})
    historique.append({"role": "assistant", "content": reponse})
    del historique[:-HISTORIQUE_MAX_MESSAGES]


def reinitialiser_sessions() -> None:
    """Utilisé par les tests."""
    _sessions.clear()
