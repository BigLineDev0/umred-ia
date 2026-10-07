import re
from datetime import date, datetime, timedelta

import dateparser

from app.core import temps
from app.nlu.texte import normaliser_texte

# Les petits modèles écrivent parfois la CHAÎNE "null" au lieu du null JSON.
# Sans ce nettoyage, "null" serait considéré comme une valeur présente.
VALEURS_NULLES = {"null", "none", "n/a", "aucun", "aucune", "inconnu", ""}

PARAMETRES_DATEPARSER = {
    # « lundi » = le prochain lundi, pas celui de la semaine passée.
    "PREFER_DATES_FROM": "future",
    # Au Sénégal comme en France, 05/10 = 5 octobre (et non 10 mai).
    "DATE_ORDER": "DMY",
}

_HEURE = re.compile(r"^\s*([01]?\d|2[0-3])\s*(?:h|:)\s*([0-5]\d)?(?::[0-5]\d)?\s*$", re.IGNORECASE)


def valeur_ou_none(valeur: object) -> str | None:
    if not isinstance(valeur, str) or valeur.strip().lower() in VALEURS_NULLES:
        return None
    return valeur.strip()


JOURS_SEMAINE = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_DECALAGES = {"aujourd'hui": 0, "demain": 1, "apres-demain": 2, "apres demain": 2}
_JOUR_DU_MOIS = re.compile(r"^(?:le\s+)?(\d{1,2})(?:er)?$")


def _date_relative(expression: str, aujourd_hui: date) -> date | None:
    """
    Résolution maison des expressions les plus courantes. dateparser gère
    mal « lundi prochain » ou « le 12 » (il peut renvoyer une date passée) ;
    ici le comportement est simple, prévisible et testé :
    - un jour de la semaine désigne sa PROCHAINE occurrence (jamais aujourd'hui) ;
    - « le 12 » désigne le 12 du mois courant, ou du mois suivant s'il est passé.
    """
    texte = normaliser_texte(expression).removesuffix(" prochain")
    if texte in _DECALAGES:
        return aujourd_hui + timedelta(days=_DECALAGES[texte])
    if texte in JOURS_SEMAINE:
        ecart = (JOURS_SEMAINE.index(texte) - aujourd_hui.weekday()) % 7 or 7
        return aujourd_hui + timedelta(days=ecart)
    jour = _JOUR_DU_MOIS.match(texte)
    if jour:
        numero = int(jour.group(1))
        annee, mois = aujourd_hui.year, aujourd_hui.month
        for _ in range(3):  # le mois suivant peut ne pas avoir de 31
            try:
                candidat = date(annee, mois, numero)
                if candidat >= aujourd_hui:
                    return candidat
            except ValueError:
                pass
            annee, mois = (annee + 1, 1) if mois == 12 else (annee, mois + 1)
    return None


def normaliser_date(date_brute: str | None, aujourd_hui: date | None = None) -> str | None:
    """
    Convertit une expression de date (« demain », « vendredi prochain »,
    « 28/09 », « 5 octobre », « 2026-09-28 ») en date ISO YYYY-MM-DD, ou None.
    Ordre : ISO strict, puis expressions relatives maison, puis dateparser
    pour tout le reste (dates avec nom de mois, formats numériques).
    """
    date_brute = valeur_ou_none(date_brute)
    if not date_brute:
        return None
    aujourd_hui = aujourd_hui or temps.aujourd_hui()
    try:
        return date.fromisoformat(date_brute).isoformat()
    except ValueError:
        pass

    relative = _date_relative(date_brute, aujourd_hui)
    if relative:
        return relative.isoformat()

    resultat = dateparser.parse(
        date_brute, languages=["fr"],
        settings={**PARAMETRES_DATEPARSER, "RELATIVE_BASE": datetime.combine(aujourd_hui, datetime.min.time())},
    )
    return resultat.date().isoformat() if resultat else None


def normaliser_heure(heure_brute: str | None) -> str | None:
    """« 9h », « 09:00 », « 14h30 », « 14:30:00 » -> « 09:00 », « 14:30 »."""
    heure_brute = valeur_ou_none(heure_brute)
    if not heure_brute:
        return None
    correspondance = _HEURE.match(heure_brute)
    if not correspondance:
        return None
    heures, minutes = correspondance.groups()
    return f"{int(heures):02d}:{minutes or '00'}"


def ajouter_minutes(heure: str, minutes: int) -> str | None:
    """Heure de fin à partir d'une durée ; None si l'on dépasse minuit."""
    debut = datetime.strptime(heure, "%H:%M")
    fin = debut + timedelta(minutes=minutes)
    return fin.strftime("%H:%M") if fin.date() == debut.date() else None
