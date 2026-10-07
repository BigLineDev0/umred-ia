from datetime import date, datetime, timedelta

from app.core import temps
from app.services.django_client import get_creneaux_occupes

# Horaires par défaut, identiques à ceux de Django (Organisation) ; les
# horaires réels de l'établissement sont lus via get_horaires() quand ils
# sont disponibles.
HEURE_OUVERTURE = "08:00"
HEURE_FERMETURE = "19:00"


def calculer_creneaux_libres(
    occupes: list[dict], jours: list[str], maintenant: datetime | None = None,
    ouverture: str = HEURE_OUVERTURE, fermeture: str = HEURE_FERMETURE,
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
    maintenant = maintenant or temps.maintenant()
    aujourd_hui, heure_actuelle = maintenant.date().isoformat(), maintenant.strftime("%H:%M")

    resultat: dict[str, list[tuple[str, str]]] = {}
    for jour in jours:
        if jour < aujourd_hui:
            resultat[jour] = []
            continue
        occupes_du_jour = sorted((o for o in occupes if str(o["date"]) == jour), key=lambda o: o["heure_debut"])
        curseur = max(ouverture, heure_actuelle) if jour == aujourd_hui else ouverture
        libres = []
        for creneau in occupes_du_jour:
            debut, fin = creneau["heure_debut"][:5], creneau["heure_fin"][:5]
            if debut > curseur:
                libres.append((curseur, min(debut, fermeture)))
            curseur = max(curseur, fin)
        if curseur < fermeture:
            libres.append((curseur, fermeture))
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
    """
    « semaine » : les 7 prochains jours ; « semaine_prochaine » : du lundi
    au dimanche suivants ; « debut_semaine » : lundi -> mercredi (de cette
    semaine si l'on est lundi ou mardi, sinon de la suivante).
    """
    depart = depart or temps.aujourd_hui()
    if periode == "semaine_prochaine":
        lundi = depart + timedelta(days=7 - depart.weekday())
        return [(lundi + timedelta(days=i)).isoformat() for i in range(7)]
    if periode == "debut_semaine":
        lundi = depart - timedelta(days=depart.weekday()) if depart.weekday() <= 1 else depart + timedelta(days=7 - depart.weekday())
        return [(lundi + timedelta(days=i)).isoformat() for i in range(3) if lundi + timedelta(days=i) >= depart]
    nombre_jours = 7 if periode == "semaine" else 1
    return [(depart + timedelta(days=i)).isoformat() for i in range(nombre_jours)]


def bornes_semaine(periode: str, depart: date | None = None) -> tuple[str, str]:
    """« Cette semaine » : d'aujourd'hui à dimanche ; « la semaine prochaine » : lundi -> dimanche suivants."""
    depart = depart or temps.aujourd_hui()
    if periode == "semaine_prochaine":
        lundi = depart + timedelta(days=7 - depart.weekday())
        return lundi.isoformat(), (lundi + timedelta(days=6)).isoformat()
    return depart.isoformat(), (depart + timedelta(days=6 - depart.weekday())).isoformat()


# Bornes des moments de la journée, rognées ensuite aux horaires réels de
# l'établissement (un labo qui ouvre à 7h30 a un « matin » de 7h30 à 12h).
MOMENTS = {"matin": ("00:00", "12:00"), "apres_midi": ("14:00", "23:59"), "soir": ("17:00", "23:59"), "journee": ("00:00", "23:59")}
LIBELLES_MOMENTS = {"matin": "le matin", "apres_midi": "l'après-midi", "soir": "en fin de journée", "journee": "sur la journée"}


def fenetre_moment(moment: str | None, ouverture: str = HEURE_OUVERTURE, fermeture: str = HEURE_FERMETURE) -> tuple[str, str]:
    debut, fin = MOMENTS.get(moment or "journee", MOMENTS["journee"])
    return max(debut, ouverture), min(fin, fermeture)


def restreindre(libres: list[tuple[str, str]], fenetre: tuple[str, str]) -> list[tuple[str, str]]:
    """Intersection des créneaux libres avec une fenêtre (« le matin »)."""
    resultat = [(max(d, fenetre[0]), min(f, fenetre[1])) for d, f in libres]
    return [(d, f) for d, f in resultat if d < f]


def _minutes(heure: str) -> int:
    return int(heure[:2]) * 60 + int(heure[3:5])


def _heure(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def decouper_en_creneaux(libres: list[tuple[str, str]], duree_minutes: int = 120, maximum: int = 4) -> list[tuple[str, str]]:
    """
    Propositions concrètes à partir des plages libres : « 8h–10h, 10h–12h »
    plutôt que « libre de 8h à 12h ». Une plage plus courte que la durée
    visée est proposée telle quelle. Ce ne sont que des PROPOSITIONS : le
    créneau choisi est revérifié par Django avant toute réservation.
    """
    creneaux = []
    for debut, fin in libres:
        d, f = _minutes(debut), _minutes(fin)
        if f - d < duree_minutes:
            creneaux.append((debut, fin))
        while f - d >= duree_minutes:
            creneaux.append((_heure(d), _heure(d + duree_minutes)))
            d += duree_minutes
    return creneaux[:maximum]
