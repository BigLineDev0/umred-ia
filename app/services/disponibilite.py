from datetime import date, datetime, timedelta

from app.services.django_client import get_creneaux_occupes

# Hypothèse assumée : les laboratoires sont ouverts de 8h à 20h. À remplacer
# le jour où Django exposera de vrais horaires d'ouverture par laboratoire.
HEURE_OUVERTURE = "08:00"
HEURE_FERMETURE = "20:00"


def calculer_creneaux_libres(
    occupes: list[dict], jours: list[str], maintenant: datetime | None = None
) -> dict[str, list[tuple[str, str]]]:
    """
    Calcul par différence (« balayage ») : pour chaque jour on part de
    l'ouverture, on parcourt les réservations triées par heure de début et
    on garde chaque trou entre le curseur et la réservation suivante.
    Le curseur avance jusqu'à la fin de chaque réservation (max() gère les
    réservations qui se chevauchent).

    Les heures sont comparées en tant que chaînes "HH:MM" : c'est correct
    car le format est à largeur fixe (« 09:00 » < « 10:00 »).

    Pour aujourd'hui, le curseur démarre à l'heure actuelle : on ne propose
    jamais un créneau déjà passé.
    """
    maintenant = maintenant or datetime.now()
    aujourd_hui, heure_actuelle = maintenant.date().isoformat(), maintenant.strftime("%H:%M")

    resultat: dict[str, list[tuple[str, str]]] = {}
    for jour in jours:
        if jour < aujourd_hui:
            resultat[jour] = []
            continue
        occupes_du_jour = sorted((o for o in occupes if str(o["date"]) == jour), key=lambda o: o["heure_debut"])
        curseur = max(HEURE_OUVERTURE, heure_actuelle) if jour == aujourd_hui else HEURE_OUVERTURE
        libres = []
        for creneau in occupes_du_jour:
            debut, fin = creneau["heure_debut"][:5], creneau["heure_fin"][:5]
            if debut > curseur:
                libres.append((curseur, min(debut, HEURE_FERMETURE)))
            curseur = max(curseur, fin)
        if curseur < HEURE_FERMETURE:
            libres.append((curseur, HEURE_FERMETURE))
        resultat[jour] = [(d, f) for d, f in libres if d < f]
    return resultat


def chevauche(occupes: list[dict], heure_debut: str, heure_fin: str) -> bool:
    """
    Deux intervalles [a, b[ et [c, d[ se chevauchent si a < d ET c < b.
    C'est exactement la condition utilisée par Django pour les conflits.
    """
    return any(o["heure_debut"][:5] < heure_fin and o["heure_fin"][:5] > heure_debut for o in occupes)


async def est_disponible(token: str, equipement_id: int, date_iso: str, heure_debut: str, heure_fin: str) -> bool:
    occupes = await get_creneaux_occupes(token, equipement_id, date_iso, date_iso)
    return not chevauche(occupes, heure_debut, heure_fin)


def jours_de_la_periode(periode: str, depart: date | None = None) -> list[str]:
    depart = depart or date.today()
    nombre_jours = 7 if periode == "semaine" else 1
    return [(depart + timedelta(days=i)).isoformat() for i in range(nombre_jours)]
