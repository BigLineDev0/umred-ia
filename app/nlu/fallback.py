import re
import dateparser
from dateparser.search import search_dates

INTENTIONS = {
    "reserver": ["réserve", "réserver", "réservation de", "je veux réserver", "prendre le"],
    "annuler": ["annule", "annuler", "dernière", "derniere"],
    "consulter_mes_reservations": ["mes réservations", "mes demandes", "prochaines réservations"],
    "consulter_disponibilite": ["disponible", "disponibilité", "libre", "créneaux", "quels équipements sont disponibles"],
    "maintenance": ["prochaine maintenance", "maintenance de", "maintenance du"],
    "statistiques": ["combien", "statistique", "nombre de"],
    "mode_emploi": ["comment utiliser", "mode d'emploi", "notice"],
    "creer_equipement": ["ajoute un équipement", "ajouter un équipement", "créer un équipement", "nouvel équipement"],
    "mon_nom": ["comment je m'appelle", "qui suis-je", "mon nom"],
    "identite": ["qui es-tu", "qui es tu", "tu es qui", "qui t'a créé", "qui t'a cree", "que peux-tu faire", "es-tu un robot"],
    "salutation": ["bonjour", "salut", "bonsoir", "hello", "coucou", "ca va", "ça va"],
}

TIME_PATTERN = re.compile(r"(\d{1,2})\s*h\s*(\d{2})?")


def _detecter_intention(message_lower: str) -> str:
    for intention, mots_cles in INTENTIONS.items():
        if any(mot in message_lower for mot in mots_cles):
            return intention
    return "autre"


def _extraire_heures(message: str) -> tuple[str | None, str | None]:
    correspondances = TIME_PATTERN.findall(message)
    heures = [f"{h.zfill(2)}:{(m or '00').zfill(2)}" for h, m in correspondances]
    heure_debut = heures[0] if len(heures) >= 1 else None
    heure_fin = heures[1] if len(heures) >= 2 else None
    return heure_debut, heure_fin

def extraire_heures_seules(message: str) -> tuple[str | None, str | None]:
    """Exposée pour être réutilisée hors du pipeline d'extraction complet — notamment quand l'utilisateur ne répond qu'avec un horaire, sans reformuler toute sa demande."""
    return _extraire_heures(message)

def _extraire_periode(message_lower: str) -> str:
    if "semaine" in message_lower:
        return "semaine"
    return "jour"

def _extraire_date(message: str) -> str | None:
    resultats = search_dates(
        message,
        languages=["fr"],
        settings={"PREFER_DATES_FROM": "future"},
    )
    if not resultats:
        return None
    _, date_trouvee = resultats[0]
    return date_trouvee.strftime("%Y-%m-%d")


async def extraire_intention_par_regles(message: str) -> dict:
    """
    Extraction déterministe par règles — sert de filet de sécurité si
    l'appel au modèle Hugging Face échoue (réseau, quota, timeout).
    """
    message_lower = message.lower()
    intention = _detecter_intention(message_lower)
    heure_debut, heure_fin = _extraire_heures(message)
    date = _extraire_date(message)
    periode = _extraire_periode(message_lower)

    return {
    "intention": intention,
    "equipement": message if intention == "reserver" else None,
    "date": date,
    "heure_debut": heure_debut,
    "heure_fin": heure_fin,
    "periode": periode,
}