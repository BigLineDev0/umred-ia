"""Règles de lecture des réservations, sans aucun appel réseau (donc testables facilement)."""
from datetime import datetime

from app.core.constantes import STATUTS_RESERVATION_ACTIFS


def reservations_actives_a_venir(reservations: list[dict], maintenant: datetime | None = None) -> list[dict]:
    """
    Réservations encore valables (en attente ou validées) et pas encore
    terminées, triées de la plus proche à la plus lointaine. Une réservation
    d'aujourd'hui dont l'heure de fin est passée est exclue.
    """
    maintenant = maintenant or datetime.now()
    aujourd_hui, heure = maintenant.date().isoformat(), maintenant.strftime("%H:%M")
    resultat = [
        r for r in reservations
        if r["statut"] in STATUTS_RESERVATION_ACTIFS
        and (r["date"] > aujourd_hui or (r["date"] == aujourd_hui and r["heure_fin"][:5] > heure))
    ]
    return sorted(resultat, key=lambda r: (r["date"], r["heure_debut"]))


def apercu_du_jour(reservations: list[dict], maintenant: datetime | None = None) -> dict:
    """
    Données du message d'accueil : nombre de réservations actives du jour
    et la prochaine à venir (celle qui n'a pas encore commencé).
    """
    maintenant = maintenant or datetime.now()
    aujourd_hui, heure = maintenant.date().isoformat(), maintenant.strftime("%H:%M")
    du_jour = [r for r in reservations if r["date"] == aujourd_hui and r["statut"] in STATUTS_RESERVATION_ACTIFS]
    a_venir = sorted((r for r in du_jour if r["heure_debut"][:5] >= heure), key=lambda r: r["heure_debut"])
    return {"total_du_jour": len(du_jour), "prochaine": a_venir[0] if a_venir else None}
