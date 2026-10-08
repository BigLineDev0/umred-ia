"""
Unique point de contact avec l'API Django. Toutes les fonctions reçoivent
le token de l'utilisateur et le retransmettent : Django reste seul juge des
permissions (FastAPI n'a aucun accès direct à la base de données).
"""
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class DjangoAPIError(Exception):
    """Erreur inattendue du backend (indisponible, 5xx, token refusé...)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Django a répondu {status_code} : {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass
class ResultatAction:
    """
    Résultat d'une action d'écriture (créer, annuler). Contrairement aux
    lectures, un 4xx n'est pas une panne ici mais une réponse métier à
    présenter à l'utilisateur (conflit 409 avec alternatives, règle de
    gestion non respectée...), d'où ce type au lieu d'une exception.
    """

    status_code: int
    data: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    @property
    def detail(self) -> str:
        return self.data.get("detail") or "Une erreur est survenue."


# Un seul client HTTP pour toute la durée de vie du service : il réutilise
# les connexions TCP (keep-alive) au lieu d'en ouvrir une par appel.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(base_url=settings.django_api_url, timeout=settings.django_api_timeout)
    return _client


async def fermer_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _extraire_detail(resp: httpx.Response) -> dict[str, Any]:
    """
    DRF renvoie ses erreurs sous plusieurs formes : {"detail": "..."},
    une liste ["msg1", ...] (ValidationError levée dans la vue) ou un dict
    par champ {"date": ["msg"]}. On ramène tout à un dict avec une clé
    "detail" lisible, pour que le reste du code n'ait qu'un cas à gérer.
    """
    try:
        data = resp.json()
    except ValueError:
        return {"detail": f"Réponse inattendue du serveur (HTTP {resp.status_code})."}

    if isinstance(data, list):
        return {"detail": str(data[0]) if data else "Une erreur est survenue.", "erreurs": data}
    if isinstance(data, dict) and "detail" not in data and not resp.is_success:
        messages = [f"{m}" for valeurs in data.values() for m in (valeurs if isinstance(valeurs, list) else [valeurs])]
        data["detail"] = " ".join(messages) or "Une erreur est survenue."
    return data if isinstance(data, dict) else {"resultat": data}


async def _requete(methode: str, chemin: str, token: str, **kwargs: Any) -> httpx.Response:
    try:
        return await _get_client().request(
            methode, chemin, headers={"Authorization": f"Bearer {token}"}, **kwargs
        )
    except httpx.RequestError as exc:
        # On journalise la cause technique, mais on n'expose jamais l'URL
        # interne de Django à l'utilisateur final.
        logger.error("Django injoignable (%s %s) : %s", methode, chemin, exc)
        raise DjangoAPIError(503, "Le service principal est injoignable.") from exc


async def _lire(chemin: str, token: str, params: dict[str, Any] | None = None) -> Any:
    resp = await _requete("GET", chemin, token, params=params)
    if not resp.is_success:
        raise DjangoAPIError(resp.status_code, _extraire_detail(resp)["detail"])
    return resp.json()


async def _agir(chemin: str, token: str, json: dict[str, Any] | None = None) -> ResultatAction:
    resp = await _requete("POST", chemin, token, json=json)
    if resp.status_code in (401, 403) or resp.status_code >= 500:
        raise DjangoAPIError(resp.status_code, _extraire_detail(resp)["detail"])
    return ResultatAction(resp.status_code, _extraire_detail(resp))


# --- Équipements ---

async def get_equipements(token: str) -> list[dict]:
    return await _lire("/equipements/", token)


async def get_alerte_usure(token: str, equipement_id: int) -> dict | None:
    """
    L'alerte d'usure est une information de confort : si elle échoue, on ne
    bloque surtout pas la réservation, on renvoie simplement "pas d'alerte".
    """
    try:
        data = await _lire(f"/equipements/{equipement_id}/alerte_usure/", token)
    except DjangoAPIError:
        logger.warning("Alerte d'usure indisponible pour l'équipement %s", equipement_id)
        return None
    return data if data.get("niveau") else None


# --- Réservations ---

async def get_reservations(
    token: str, *, tous: bool = False, a_venir: bool = False,
    date_debut: str | None = None, date_fin: str | None = None, statut: str | None = None,
) -> list[dict]:
    """
    Sans option : uniquement MES réservations (règle appliquée par Django).
    tous=True : toutes les réservations de l'établissement — Django répond
    403 si l'utilisateur n'est pas administrateur, ce n'est donc pas un
    contournement possible. Les filtres de période et de statut sont
    appliqués par Django.
    """
    params: dict[str, Any] = {}
    if tous:
        params["all"] = "true"
    if a_venir:
        params["a_venir"] = "true"
    params.update({k: v for k, v in {"date_debut": date_debut, "date_fin": date_fin, "statut": statut}.items() if v})
    return await _lire("/reservations/", token, params=params)


# Seules les réservations acquises occupent un créneau : une demande en
# attente ne bloque personne (Django départage la file à la validation).
STATUTS_BLOQUANTS = {"VALIDEE", "TERMINEE"}


async def get_creneaux_occupes(token: str, equipement_id: int, date_debut: str, date_fin: str) -> list[dict]:
    creneaux = await _lire(
        "/reservations/creneaux_occupes/", token,
        params={"equipement": equipement_id, "date_debut": date_debut, "date_fin": date_fin},
    )
    return [c for c in creneaux if c.get("statut", "VALIDEE") in STATUTS_BLOQUANTS]


async def get_horaires(token: str) -> tuple[str, str, dict[int, tuple[bool, str, str]]] | None:
    """
    Horaires d'ouverture configurés par l'établissement (SaaS : chacun a
    les siens). Renvoie (ouverture_globale, fermeture_globale, horaires_par_jour)
    où horaires_par_jour = {jour_semaine: (ferme, ouverture, fermeture)}
    (jour 0 = lundi). Information de confort : en cas d'échec, l'appelant
    garde les horaires par défaut.
    """
    try:
        organisation = await _lire("/organisations/courante/", token)
        par_jour = {
            h["jour"]: (bool(h.get("ferme")), h["heure_ouverture"][:5], h["heure_fermeture"][:5])
            for h in organisation.get("horaires") or []
        }
        return organisation["heure_ouverture"][:5], organisation["heure_fermeture"][:5], par_jour
    except Exception:
        logger.warning("Horaires de l'établissement indisponibles : horaires par défaut utilisés.")
        return None


async def verifier_reservation(token: str, payload: dict) -> ResultatAction:
    """
    Vérification par Django d'une demande SANS l'enregistrer : disponibilité
    réelle, conflits, créneaux et équipements alternatifs, statut que prendra
    la demande (validée d'office ou en attente) et sa raison. C'est Django,
    et non l'assistant, qui décide qu'un créneau est libre.
    """
    return await _agir("/reservations/verifier/", token, json=payload)


async def creer_reservation(token: str, payload: dict) -> ResultatAction:
    return await _agir("/reservations/", token, json=payload)


async def annuler_reservation(token: str, reservation_id: int) -> ResultatAction:
    return await _agir(f"/reservations/{reservation_id}/annuler/", token)


# --- Maintenances ---

async def get_maintenances(token: str, *, equipement_id: int | None = None, statut: str | None = None) -> list[dict]:
    params: dict[str, Any] = {}
    if equipement_id is not None:
        params["equipement"] = equipement_id
    if statut:
        params["statut"] = statut
    return await _lire("/maintenances/", token, params=params)


# --- Pilotage ---

async def get_indicateurs(token: str, date_debut: str | None = None, date_fin: str | None = None) -> dict:
    """Indicateurs d'aide à la décision ; Django vérifie que l'utilisateur y a droit."""
    params = {k: v for k, v in {"date_debut": date_debut, "date_fin": date_fin}.items() if v}
    return await _lire("/pilotage/indicateurs/", token, params=params)
