"""
Mesure de la latence de l'assistant, étape par étape, sans jamais
enregistrer le contenu des messages ni le jeton de l'utilisateur.

Chaque requête /chat ouvre une `Mesure` (portée par une ContextVar, donc
propre à la requête, y compris dans le thread d'inférence lancé par
asyncio.to_thread qui copie le contexte). Les modules instrumentés y
ajoutent leur durée : appels à Django, génération du modèle, extraction.
Les 1000 dernières mesures restent en mémoire pour calculer médiane et
95e percentile (GET /api/metriques, réservé aux administrateurs).
"""
import logging
import statistics
import time
from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

HISTORIQUE_MAX = 1000


@dataclass
class Mesure:
    debut: float = field(default_factory=time.perf_counter)
    durees_ms: dict[str, float] = field(default_factory=dict)
    appels: Counter = field(default_factory=Counter)
    tokens_generes: int = 0
    # "regles" (aucun appel au modèle), "modele", ou "etape" (réponse à une
    # question posée au tour précédent : choix, oui/non).
    chemin: str = "regles"
    intention: str | None = None
    erreur: str | None = None
    total_ms: float = 0.0

    def ajouter(self, categorie: str, duree_s: float) -> None:
        self.durees_ms[categorie] = self.durees_ms.get(categorie, 0.0) + duree_s * 1000
        self.appels[categorie] += 1


_courante: ContextVar[Mesure | None] = ContextVar("mesure_assistant", default=None)
_historique: deque[Mesure] = deque(maxlen=HISTORIQUE_MAX)


def courante() -> Mesure | None:
    return _courante.get()


@contextmanager
def mesurer_requete():
    """Ouvre la mesure d'une requête, puis la journalise et l'archive."""
    mesure = Mesure()
    jeton = _courante.set(mesure)
    try:
        yield mesure
    finally:
        _courante.reset(jeton)
        mesure.total_ms = (time.perf_counter() - mesure.debut) * 1000
        _historique.append(mesure)
        # Une ligne par message : intention et durées seulement, aucun contenu.
        logger.info(
            "chat chemin=%s intention=%s total_ms=%.0f llm_ms=%.0f appels_llm=%d tokens=%d django_ms=%.0f "
            "appels_django=%d%s",
            mesure.chemin, mesure.intention, mesure.total_ms, mesure.durees_ms.get("llm", 0), mesure.appels["llm"],
            mesure.tokens_generes, mesure.durees_ms.get("django", 0), mesure.appels["django"],
            f" erreur={mesure.erreur}" if mesure.erreur else "",
        )


@contextmanager
def chronometrer(categorie: str):
    """Ajoute la durée du bloc à la mesure en cours (sans effet hors requête)."""
    debut = time.perf_counter()
    try:
        yield
    finally:
        mesure = _courante.get()
        if mesure is not None:
            mesure.ajouter(categorie, time.perf_counter() - debut)


def noter(**attributs) -> None:
    mesure = _courante.get()
    if mesure is not None:
        for nom, valeur in attributs.items():
            setattr(mesure, nom, valeur)


def noter_tokens(nombre: int) -> None:
    mesure = _courante.get()
    if mesure is not None:
        mesure.tokens_generes += nombre


def _percentile_95(valeurs: list[float]) -> float | None:
    if not valeurs:
        return None
    if len(valeurs) == 1:
        return valeurs[0]
    return statistics.quantiles(valeurs, n=20, method="inclusive")[-1]


def _resume(mesures: list[Mesure]) -> dict:
    totaux = [m.total_ms for m in mesures]
    return {
        "requetes": len(mesures),
        "mediane_ms": round(statistics.median(totaux)) if totaux else None,
        "p95_ms": round(_percentile_95(totaux)) if totaux else None,
    }


def statistiques() -> dict:
    """Agrégats sur les dernières requêtes (aucune donnée personnelle)."""
    mesures = list(_historique)
    par_chemin = {chemin: _resume([m for m in mesures if m.chemin == chemin])
                  for chemin in sorted({m.chemin for m in mesures})}
    appels_llm = sum(m.appels["llm"] for m in mesures)
    return {
        **_resume(mesures),
        "par_chemin": par_chemin,
        "appels_llm": appels_llm,
        "appels_llm_par_requete": round(appels_llm / len(mesures), 3) if mesures else None,
        "tokens_generes": sum(m.tokens_generes for m in mesures),
        "appels_django_par_requete": round(sum(m.appels["django"] for m in mesures) / len(mesures), 2) if mesures else None,
        "erreurs": dict(Counter(m.erreur for m in mesures if m.erreur)),
    }


def reinitialiser() -> None:
    """Utilisé par les tests."""
    _historique.clear()
