"""
Extraction DÉTERMINISTE par règles (expressions régulières + dateparser).

Elle joue deux rôles :
1. filet de sécurité complet si le modèle de langage est indisponible ;
2. source de vérité pour les dates et heures, même quand le modèle répond :
   une regex ne peut pas « inventer » une heure absente du message, alors
   qu'un petit modèle génératif le fait parfois (hallucination).
"""
import re

from app.core import temps
from app.nlu.extraction import Extraction
from app.nlu.fautes import corriger_dates
from app.nlu.intentions import _VERBE_RESERVER, Intention, intention_par_mots_cles
from app.nlu.normalisation import ajouter_minutes, date_alternative, normaliser_date
from app.nlu.texte import normaliser_texte

_NOMBRES_EN_LETTRES = {"une": 1, "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6}

# « pendant 2h », « pendant deux heures », « pendant 1h30 »
_DUREE = re.compile(r"pendant\s+(\d{1,2}|une|un|deux|trois|quatre|cinq|six)\s*(?:h|heures?)\s*(\d{2})?")
# « pour deux heures », « durant 2 heures » : le mot « heure(s) » est exigé
# et le nombre borné, sinon « pour 14h » (une heure de début) serait lu
# comme une durée de 14 heures.
_DUREE_POUR = re.compile(r"(?:pour|durant)\s+(une|un|deux|trois|quatre|cinq|six|[1-8])\s*heures?\b")
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


_APRES_MIDI = re.compile(r"apr[eè]s[- ]?midi")
# « ce matin », « cet après-midi », « ce soir » : le moment est AUJOURD'HUI.
_MOMENT_DU_JOUR = re.compile(r"\b(?:ce matin|cet? apres[- ]?midi|cet aprem|ce soir)\b")


def _heure(heures: str, minutes: str | None) -> str:
    return f"{int(heures):02d}:{minutes or '00'}"


def _extraire_duree_minutes(texte: str) -> int | None:
    correspondance = _DUREE.search(texte)
    if not correspondance:
        pour = _DUREE_POUR.search(texte)
        return (_NOMBRES_EN_LETTRES.get(pour.group(1)) or int(pour.group(1))) * 60 if pour else None
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
    # « après-midi » est un moment de la journée, pas l'heure « midi » : sans
    # ce retrait, « vendredi après-midi » devenait « vendredi à partir de 12h ».
    texte = _APRES_MIDI.sub(" aprem ", message.lower()).replace("midi", "12h")
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


def _expression_date(message: str) -> str | None:
    correspondance = _DATE.search(message.lower())
    return re.sub(r"^le\s+", "", correspondance.group(1)) if correspondance else None


def extraire_date(message: str) -> str | None:
    expression = _expression_date(message)
    if expression:
        return normaliser_date(expression)
    if _MOMENT_DU_JOUR.search(normaliser_texte(message)):
        return temps.aujourd_hui().isoformat()
    return None


def extraire_periode(message_normalise: str) -> str:
    if "debut de semaine" in message_normalise or "debut de la semaine" in message_normalise:
        return "debut_semaine"
    if "semaine prochaine" in message_normalise:
        return "semaine_prochaine"
    if "semaine derniere" in message_normalise or "semaine passee" in message_normalise:
        return "semaine_derniere"
    return "semaine" if "semaine" in message_normalise else "jour"


# « toute la journée » doit passer avant « matin » (« de la matinée à... »).
_MOMENTS = [
    ("journee", ("toute la journee", "journee entiere", "la journee complete", "toute la jour")),
    ("apres_midi", ("apres-midi", "apres midi", "aprem", "aprm")),
    ("matin", ("matin",)),
    ("soir", ("soir",)),
]


def extraire_moment(message_normalise: str) -> str | None:
    for moment, expressions in _MOMENTS:
        if any(e in message_normalise for e in expressions):
            return moment
    return None


# Nom de l'équipement cité après un verbe de réservation : « réserve le
# XYZ-999 demain » -> « XYZ-999 ». Sert à répondre « aucun équipement ne
# correspond à XYZ-999 » au lieu de redemander quel équipement.
_OBJET_RESERVATION = re.compile(
    r"\b(?:reserve[rz]?|reservons|bloquer?|prendre|utiliser)(?:[- ]moi)?\s+"
    r"(?:(?:le|la|les|un|une|du|des|mon|ma)\s+|l')?"
    r"(?P<objet>.+?)"
    r"(?=\s+(?:demain|aujourd|apres|ce |cet |cette |lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|le \d|le premier"
    r"|de \d|des \d|a \d|entre|pour|matin|soir|toute|en debut|la semaine|jusqu|pendant|vers|svp|s'il)|\s*[?.!,;]|$)"
)
# Le verbe est directement suivi d'une date ou d'un horaire (« réserver demain ») :
# aucun équipement n'est cité.
_DEBUT_NON_EQUIPEMENT = re.compile(
    r"^(?:demain|aujourd|apres|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|ce |cet |cette |pour|matin|soir"
    r"|toute|de \d|a \d|\d|entre|le \d)"
)
# Formulations qui ne désignent aucun équipement précis.
_OBJETS_GENERIQUES = ("quelque chose", "equipement", "appareil", "machine", "creneau", "reservation", "salle")


def extraire_objet_reservation(message_normalise: str) -> str | None:
    correspondance = _OBJET_RESERVATION.search(message_normalise)
    if not correspondance:
        return None
    objet = correspondance.group("objet").strip(" -'")
    if not objet or _DEBUT_NON_EQUIPEMENT.match(objet) or len(objet) > 60 or objet in _OBJETS_GENERIQUES or objet.startswith(("quelque", "un ", "une ")):
        return None
    return objet


def _graphie_originale(fragment: str | None, message: str, message_normalise: str) -> str | None:
    """« xyz-999 » -> « XYZ-999 » tel que tapé (la normalisation conserve les positions)."""
    if not fragment or len(message) != len(message_normalise):
        return fragment
    debut = message_normalise.find(fragment)
    return message[debut:debut + len(fragment)] if debut >= 0 else fragment


def extraire_par_regles(message: str) -> Extraction:
    message = corriger_dates(message.strip())
    message_normalise = normaliser_texte(message)
    heure_debut, heure_fin = extraire_heures(message)
    expression = _expression_date(message)
    date = extraire_date(message)
    intention = intention_par_mots_cles(message_normalise)
    if intention == Intention.AUTRE and "reservation" in message_normalise and (date or heure_debut):
        # « Réservation du microscope demain à 10h » : le nom seul, mais
        # avec un créneau, vaut une demande de réservation.
        intention = Intention.RESERVER
    elif intention == Intention.AIDE and _VERBE_RESERVER.search(message_normalise) and (date or heure_debut):
        # « Je veux réserver le spectrophotomètre demain de 9h à 11h. Quelles
        # sont les étapes ? » : la demande concrète passe avant l'explication.
        intention = Intention.RESERVER
    return Extraction(
        intention=intention,
        # Seul le nom cité après « réserver » est isolé ; sinon la recherche
        # floue se fera sur le message complet.
        equipement=_graphie_originale(extraire_objet_reservation(message_normalise), message, message_normalise),
        date=date,
        heure_debut=heure_debut,
        heure_fin=heure_fin,
        periode=extraire_periode(message_normalise),
        moment=extraire_moment(message_normalise),
        date_alternative=date_alternative(expression) if expression else None,
        date_invalide=expression if expression and not date else None,
        duree_minutes=_extraire_duree_minutes(message.lower()),
    )
