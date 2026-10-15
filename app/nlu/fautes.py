"""
Correction des fautes de frappe sur les mots de DATE (« demin », « vendredie »,
« aujourdhui »), avant l'extraction par règles : sans elle, « reserv la
centrifugeuse demin à 10h » perdait sa date et l'assistant redemandait
une information pourtant donnée.

Volontairement restreint aux mots de date : corriger n'importe quel mot
transformerait des noms propres (« Martin » -> « matin ») ou des mots
courants (« vendre » -> « vendredi »). Les noms d'équipements ont leur
propre recherche approximative (app/services/matching.py).
"""
import re

from rapidfuzz import fuzz

# Fautes fréquentes qu'aucune distance d'édition ne rattrape proprement
# (mots trop courts, apostrophe oubliée...).
CORRECTIONS = {
    "auj": "aujourd'hui", "ajd": "aujourd'hui", "aujourdhui": "aujourd'hui", "aujourdui": "aujourd'hui",
    "aujourd hui": "aujourd'hui", "dem1": "demain", "2main": "demain",
    "apres demin": "après-demain", "apresdemain": "après-demain",
}

# Cibles de la correction approximative (sans accents, en minuscules).
MOTS_DATE = ("demain", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
SEUIL = 85
# Mots français réels proches d'un jour ou de « demain » : jamais corrigés.
EXCEPTIONS = {"vendre", "vendue", "vendus", "demande", "demandes", "domaine", "mardis", "lundis", "jeudis",
              "mercredis", "vendredis", "samedis", "dimanches", "demains", "medi", "mandat"}

_MOT = re.compile(r"[a-zA-Zàâäéèêëîïôöùûüç']+")


def _corriger_mot(mot: str) -> str:
    minuscule = mot.lower()
    if minuscule in CORRECTIONS:
        return CORRECTIONS[minuscule]
    if len(minuscule) < 5 or minuscule in MOTS_DATE or minuscule in EXCEPTIONS or "'" in minuscule:
        return mot
    # Même première lettre : on corrige une faute de frappe, pas un autre mot.
    candidats = [(fuzz.ratio(minuscule, cible), cible) for cible in MOTS_DATE if cible[0] == minuscule[0]]
    score, cible = max(candidats, default=(0, None))
    return cible if cible and score >= SEUIL and abs(len(cible) - len(minuscule)) <= 2 else mot


def corriger_dates(message: str) -> str:
    """« reserv la centrifugeuse demin à 10h » -> « reserv la centrifugeuse demain à 10h »."""
    for faute, correction in CORRECTIONS.items():
        if " " in faute:
            message = re.sub(rf"\b{faute}\b", correction, message, flags=re.IGNORECASE)
    return _MOT.sub(lambda m: _corriger_mot(m.group(0)), message)
