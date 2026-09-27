import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, status

from app.core.config import settings
from app.core.security import Utilisateur, get_current_user

FENETRE_SECONDES = 60

_requetes: dict[int, deque[float]] = defaultdict(deque)


def limiter_debit(user: Utilisateur = Depends(get_current_user)) -> Utilisateur:
    """
    Fenêtre glissante d'une minute, par utilisateur (et non par IP : derrière
    le réseau de l'université, tout le monde partage souvent la même IP).
    On garde l'horodatage des requêtes récentes ; au-delà du quota -> 429.
    """
    maintenant = time.monotonic()
    historique = _requetes[user.id]
    while historique and maintenant - historique[0] > FENETRE_SECONDES:
        historique.popleft()

    if len(historique) >= settings.rate_limit_par_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de messages envoyés, merci de patienter quelques secondes.",
        )

    historique.append(maintenant)
    return user
