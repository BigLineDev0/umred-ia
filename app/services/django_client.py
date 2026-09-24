from datetime import date, datetime

import httpx
from app.core.config import settings


async def get_equipements(token: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.django_api_url}/equipements/",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def creer_reservation(token: str, payload: dict) -> dict:
    """
    Django peut répondre soit avec un dict structuré (cas du conflit 409,
    voir _reponse_conflit côté Django), soit avec une simple LISTE de
    messages d'erreur (cas d'une ValidationError classique de DRF, ex.
    équipement invalide). On normalise les deux formes en un seul dict,
    pour que le reste du pipeline n'ait jamais à se soucier de laquelle
    des deux formes Django a choisi de renvoyer.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.django_api_url}/reservations/", json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            data = resp.json()
        except Exception:
            data = {}

        if isinstance(data, list):
            data = {
                'detail': data[0] if data else "Une erreur est survenue.",
                'erreurs': data,
            }

        data['_status_code'] = resp.status_code
        return data
    
async def get_mes_reservations(token: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{settings.django_api_url}/reservations/", headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return resp.json()


async def annuler_reservation(token: str, reservation_id: int) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{settings.django_api_url}/reservations/{reservation_id}/annuler/", headers={"Authorization": f"Bearer {token}"})
        return resp.json() if resp.status_code < 400 else {"erreur": resp.json()}


async def get_maintenances(token: str, equipement_id: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.django_api_url}/maintenances/",
            params={"equipement": equipement_id, "statut": "PLANIFIEE"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def get_creneaux_occupes(token: str, equipement_id: int, date_debut: str, date_fin: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.django_api_url}/reservations/creneaux_occupes/",
            params={"equipement": equipement_id, "date_debut": date_debut, "date_fin": date_fin},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()
    
    
async def get_toutes_maintenances(token: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{settings.django_api_url}/maintenances/", headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return resp.json()

async def get_reservations_stats(token: str, tous: bool = False) -> list[dict]:
    params = {"all": "true"} if tous else {}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{settings.django_api_url}/reservations/", params=params, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return resp.json()
    

async def get_reservation_creneaux(token: str, equipement_id: int, date: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.django_api_url}/reservations/creneaux_occupes/",
            params={"equipement": equipement_id, "date_debut": date, "date_fin": date},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def est_disponible(token: str, equipement_id: int, date: str, heure_debut: str, heure_fin: str) -> bool:
    occupes = await get_reservation_creneaux(token, equipement_id, date)
    for o in occupes:
        if o['heure_debut'][:5] < heure_fin and o['heure_fin'][:5] > heure_debut:
            return False
    return True


async def creer_reservation(token: str, payload: dict) -> dict:
    """
    Renvoie TOUJOURS le JSON de la réponse, succès (201) ou conflit (409)
    inclus — c'est à l'appelant de distinguer selon le contenu, puisque le
    409 n'est plus une vraie erreur mais une réponse structurée à
    présenter à l'utilisateur (alternatives, priorité).
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.django_api_url}/reservations/", json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        data = resp.json()
        data['_status_code'] = resp.status_code
        return data
    
async def get_alerte_usure(token: str, equipement_id: int) -> dict | None:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.django_api_url}/equipements/{equipement_id}/alerte_usure/",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if data.get('niveau') else None
    
async def get_apercu_accueil(token: str) -> dict:
    reservations = await get_mes_reservations(token)
    aujourd_hui = date.today().isoformat()  # date, pas datetime.date
    du_jour = [r for r in reservations if r['date'] == aujourd_hui and r['statut'] in ['VALIDEE', 'EN_ATTENTE']]

    prochaine = None
    if du_jour:
        maintenant = datetime.now().time()  # datetime, pas datetime.datetime
        a_venir = [r for r in du_jour if datetime.strptime(r['heure_debut'][:5], '%H:%M').time() >= maintenant]
        if a_venir:
            prochaine = sorted(a_venir, key=lambda r: r['heure_debut'])[0]

    return {"total_du_jour": len(du_jour), "prochaine": prochaine}