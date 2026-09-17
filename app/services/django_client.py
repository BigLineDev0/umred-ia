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
    Appelle le MÊME endpoint que le formulaire Angular — aucune règle
    métier dupliquée ici. Le conflit de créneau, le statut selon le rôle,
    tout est déjà géré côté Django.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.django_api_url}/reservations/",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code >= 400:
            return {"erreur": resp.json()}
        return resp.json()
    
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