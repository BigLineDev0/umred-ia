"""
Exécution d'un scénario de la campagne sur l'application FastAPI réelle
(TestClient), date de référence figée, faux Django de la campagne.
Utilisé par pytest (tests/campagne/test_campagne.py, sans modèle) et par
scripts/campagne_assistant.py (avec ou sans Qwen, plusieurs passes).
"""
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from .donnees import DATE_REFERENCE, FauxDjangoCampagne, jeton


@dataclass
class Observation:
    """Ce que voit une vérification : réponses, journal du faux Django, mesures par message."""
    reponses: list[dict]
    faux: FauxDjangoCampagne
    mesures: list[dict]


@dataclass
class Cas:
    id: str
    categorie: str
    messages: list[str]
    verifier: Callable[[Observation], list[str]]
    utilisateur: int = 1
    preparer: Callable[[FauxDjangoCampagne], None] | None = None
    # Déroulé particulier (concurrence, expiration...) : remplace l'envoi séquentiel.
    jouer: Callable[["Banc"], list[dict]] | None = None
    bloque: str | None = None


@dataclass
class Resultat:
    id: str
    categorie: str
    statut: str  # "reussi", "echoue", "bloque"
    raisons: list[str] = field(default_factory=list)
    reponses: list[dict] = field(default_factory=list)
    mesures: list[dict] = field(default_factory=list)


class Banc:
    """Application, faux Django et horloge figée pour UN scénario."""

    def __init__(self, faux: FauxDjangoCampagne):
        from fastapi.testclient import TestClient

        from app.core import metriques, rate_limit, session_store
        from app.services import django_client
        from main import app

        self.faux = faux
        self.app = app
        self.metriques = metriques
        session_store.reinitialiser_sessions()
        rate_limit._requetes.clear()
        django_client._client = httpx.AsyncClient(base_url="http://django.test/api", transport=httpx.MockTransport(faux))
        self.client = TestClient(app)
        self.mesures: list[dict] = []

    def envoyer(self, message: str, utilisateur: int = 1, session: str = "campagne-session") -> dict:
        avant = len(self.metriques._historique)
        debut = time.perf_counter()
        r = self.client.post("/api/chat", json={"session_id": session, "message": message},
                             headers={"Authorization": f"Bearer {jeton(utilisateur)}"})
        duree = (time.perf_counter() - debut) * 1000
        corps = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"detail": r.text}
        corps["_http"] = r.status_code
        nouvelles = list(self.metriques._historique)[avant:]
        m = nouvelles[-1] if nouvelles else None
        self.mesures.append({
            "message": message, "ms": round(duree, 1), "http": r.status_code,
            "chemin": m.chemin if m else None, "intention": m.intention if m else None,
            "appels_llm": m.appels["llm"] if m else 0, "llm_ms": round(m.durees_ms.get("llm", 0)) if m else 0,
            "tokens": m.tokens_generes if m else 0, "appels_django": m.appels["django"] if m else 0,
            "django_ms": round(m.durees_ms.get("django", 0), 1) if m else 0, "erreur": m.erreur if m else None,
        })
        return corps


def executer(cas: Cas) -> Resultat:
    if cas.bloque:
        return Resultat(cas.id, cas.categorie, "bloque", [cas.bloque])
    from app.core import temps
    horloge_reelle = temps.maintenant
    temps.maintenant = lambda: DATE_REFERENCE
    try:
        return _executer(cas)
    finally:
        temps.maintenant = horloge_reelle


def _executer(cas: Cas) -> Resultat:
    faux = FauxDjangoCampagne()
    if cas.preparer:
        cas.preparer(faux)
    banc = Banc(faux)
    try:
        if cas.jouer:
            reponses = cas.jouer(banc)
        else:
            reponses = [banc.envoyer(m, cas.utilisateur) for m in cas.messages]
        raisons = cas.verifier(Observation(reponses, faux, banc.mesures))
    except Exception as exc:  # un plantage est un échec du scénario, pas de la campagne
        reponses, raisons = [], [f"exception : {type(exc).__name__}: {exc}"]
    return Resultat(cas.id, cas.categorie, "echoue" if raisons else "reussi", raisons,
                    [{"reponse": r.get("reponse"), "type": r.get("type"), "intention": r.get("intention")} for r in reponses],
                    banc.mesures)


def resume(reponse: dict[str, Any]) -> str:
    return f"[{reponse.get('type')}] {(reponse.get('reponse') or reponse.get('detail') or '')[:160]!r}"
