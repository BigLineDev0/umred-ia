"""
Extraction DÉTERMINISTE par règles (expressions régulières + dateparser).

Elle joue deux rôles :
1. filet de sécurité complet si le modèle de langage est indisponible ;
2. source de vérité pour les dates et heures, même quand le modèle répond :
   une regex ne peut pas « inventer » une heure absente du message, alors
   qu'un petit modèle génératif le fait parfois (hallucination).
"""
import re

from app.nlu.extraction import Extraction
from app.nlu.intentions import intention_par_mots_cles
from app.nlu.normalisation import ajouter_minutes, normaliser_date
from app.nlu.texte import normaliser_texte

_NOMBRES_EN_LETTRES = {"une": 1, "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6}

# « pendant 2h », « pendant deux heures », « pendant 1h30 »
_DUREE = re.compile(r"pendant\s+(\d{1,2}|une|un|deux|trois|quatre|cinq|six)\s*(?:h|heures?)\s*(\d{2})?")
# « de 10h à 12h », « de 10 à 12h », « entre 9h30 et 11h », « 10h-12h ».
# Sans « de/entre », la 1re heure doit porter un « h » : sinon « le 12 à
# 14h » (une date puis une heure) serait lu comme une plage 12h-14h.
_PLAGE = re.compile(
    r"(?:(?:de|entre)\s+([01]?\d|2[0-3])\s*(?:h\s*([0-5]\d)?)?|(?<![\d:/])([01]?\d|2[0-3])\s*h\s*([0-5]\d)?)"
    r"\s*(?:à|a|et|-)\s*([01]?\d|2[0-3])\s*h\s*([0-5]\d)?"
)
# « jusqu'à 12h », « fin à 17h30 »
_FIN = re.compile(r"(?:jusqu['’]?\s*[àa]|fin\s+[àa]|termine\s+[àa])\s*([01]?\d|2[0-3])\s*h\s*([0-5]\d)?")
# « 14h », « 14h30 », « 14 h 30 », « 14:30 »
_HEURE = re.compile(r"(?<![\d:/])([01]?\d|2[0-3])\s*(?:h\s*([0-5]\d)?|:([0-5]\d))(?![\d/])")
# Expressions de date reconnues. On extrait un MORCEAU précis du message
# avant de le confier à dateparser : lui donner la phrase entière produit
# des faux positifs (il voit des dates dans des mots anodins).
_DATE = re.compile(
    r"\b(aujourd'hui|apr[eè]s[- ]demain|demain"
    r"|(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)(?:\s+prochain)?"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[/.]\d{1,2}(?:[/.]\d{2,4})?"
    r"|(?:le\s+)?\d{1,2}(?:er)?\s+(?:janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)(?:\s+\d{4})?"
    r"|le\s+\d{1,2}(?:er)?(?!\s*(?:h|:|\d|/|\.)))"
)


def _heure(heures: str, minutes: str | None) -> str:
    return f"{int(heures):02d}:{minutes or '00'}"


def _extraire_duree_minutes(texte: str) -> int | None:
    correspondance = _DUREE.search(texte)
    if not correspondance:
        return None
    quantite, minutes = correspondance.groups()
    heures = _NOMBRES_EN_LETTRES.get(quantite) or int(quantite)
    return heures * 60 + int(minutes or 0)


def extraire_heures(message: str) -> tuple[str | None, str | None]:
    """
    Renvoie (heure_debut, heure_fin). Ordre de priorité :
    1. une plage explicite (« de 10h à 12h ») ;
    2. sinon les deux premières heures trouvées ;
    3. une durée (« pendant 2h ») complète l'heure de fin si elle manque.
    La durée est retirée du texte AVANT la recherche des heures, sinon
    « pendant 2h » serait lu comme « 02:00 ».
    """
    texte = message.lower().replace("midi", "12h")
    duree = _extraire_duree_minutes(texte)
    texte = _DUREE.sub(" ", texte)

    plage = _PLAGE.search(texte)
    if plage:
        h1a, m1a, h1b, m1b, h2, m2 = plage.groups()
        return _heure(h1a or h1b, m1a or m1b), _heure(h2, m2)

    # « jusqu'à 12h » ne donne qu'une heure de FIN.
    fin_explicite = _FIN.search(texte)
    if fin_explicite:
        texte = _FIN.sub(" ", texte)

    heures = [_heure(h, m1 or m2) for h, m1, m2 in _HEURE.findall(texte)]
    debut = heures[0] if heures else None
    fin = heures[1] if len(heures) > 1 else None
    if fin_explicite:
        fin = _heure(*fin_explicite.groups())
    if debut and not fin and duree:
        fin = ajouter_minutes(debut, duree)
    return debut, fin


def extraire_date(message: str) -> str | None:
    correspondance = _DATE.search(message.lower())
    if not correspondance:
        return None
    expression = re.sub(r"^le\s+", "", correspondance.group(1))
    return normaliser_date(expression)


def extraire_periode(message_normalise: str) -> str:
    return "semaine" if "semaine" in message_normalise else "jour"


def extraire_par_regles(message: str) -> Extraction:
    message_normalise = normaliser_texte(message)
    heure_debut, heure_fin = extraire_heures(message)
    return Extraction(
        intention=intention_par_mots_cles(message_normalise),
        equipement=None,  # la recherche floue se fera sur le message complet
        date=extraire_date(message),
        heure_debut=heure_debut,
        heure_fin=heure_fin,
        periode=extraire_periode(message_normalise),
    )
