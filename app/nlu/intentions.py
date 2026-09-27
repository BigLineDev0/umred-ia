"""
Catalogue unique des intentions comprises par l'assistant. Il est partagé
par le prompt du modèle, l'extraction par règles et le routage : ajouter
une intention se fait ici, à un seul endroit.
"""
from enum import StrEnum

from app.nlu.texte import contient_un


class Intention(StrEnum):
    RESERVER = "reserver"
    ANNULER = "annuler"
    CONSULTER_MES_RESERVATIONS = "consulter_mes_reservations"
    CONSULTER_DISPONIBILITE = "consulter_disponibilite"
    MAINTENANCE = "maintenance"
    STATISTIQUES = "statistiques"
    CREER_EQUIPEMENT = "creer_equipement"
    MON_NOM = "mon_nom"
    IDENTITE = "identite"
    SALUTATION = "salutation"
    AUTRE = "autre"


# Mots-clés SANS accents (le message est normalisé avant comparaison).
# L'ORDRE COMPTE : la première intention qui correspond gagne. On place
# donc les formulations les plus spécifiques en premier — sinon « mes
# réservations » serait capté par « reserv » (réserver) et « annule ma
# réservation » aussi.
MOTS_CLES_INTENTIONS: list[tuple[Intention, tuple[str, ...]]] = [
    # Une question sur le FONCTIONNEMENT (« comment faire une réservation ? »)
    # n'est pas une demande d'action : elle ne doit pas lancer une réservation.
    (Intention.AUTRE, ("comment faire", "comment reserver", "comment annuler", "comment fonctionne",
                       "comment ca marche", "comment on ", "c'est quoi", "qu'est-ce que", "qu'est ce que")),
    (Intention.ANNULER, ("annule", "annuler", "annulation", "supprime ma reservation", "supprimer ma reservation")),
    (Intention.CONSULTER_MES_RESERVATIONS, ("mes reservations", "mes demandes", "prochaines reservations", "mon planning",
                                            "mes creneaux", "ma prochaine reservation", "ma derniere reservation")),
    (Intention.CREER_EQUIPEMENT, ("ajoute un equipement", "ajouter un equipement", "creer un equipement", "nouvel equipement")),
    (Intention.STATISTIQUES, ("combien", "statistique", "nombre de")),
    # « Réserve-moi un créneau libre » est une réservation : le verbe
    # d'action passe avant les mots de consultation.
    (Intention.RESERVER, ("reserv", "je veux prendre", "bloquer le", "bloque le")),
    (Intention.CONSULTER_DISPONIBILITE, ("dispo", "libre", "creneaux")),
    (Intention.MAINTENANCE, ("maintenance", "entretien", "revision")),
    (Intention.MON_NOM, ("comment je m'appelle", "qui suis-je", "qui suis je", "mon nom")),
    (Intention.IDENTITE, ("qui es-tu", "qui es tu", "tu es qui", "qui t'a cree", "que peux-tu faire",
                          "que sais-tu faire", "es-tu un robot", "tu sers a quoi")),
    (Intention.SALUTATION, ("bonjour", "salut", "bonsoir", "hello", "coucou", "ca va")),
]

# Le petit modèle confond régulièrement ces intentions (« combien de
# réservations ai-je ? » -> consulter_mes_reservations). Ces mots-clés
# sont non ambigus : on les laisse primer sur le modèle.
MOTS_STATISTIQUES = ("combien", "statistique", "nombre de")
MOTS_DISPONIBILITE = ("dispo", "libre")  # « dispo » couvre disponible / disponibilité
# Intentions d'action qu'aucune heuristique ne doit écraser.
INTENTIONS_ACTION = {Intention.RESERVER, Intention.ANNULER, Intention.CREER_EQUIPEMENT}


def intention_par_mots_cles(message_normalise: str) -> Intention:
    for intention, mots_cles in MOTS_CLES_INTENTIONS:
        if contient_un(message_normalise, mots_cles):
            return intention
    return Intention.AUTRE


def valider_intention(valeur: object) -> Intention:
    """Toute valeur hors catalogue (hallucination du modèle) devient AUTRE."""
    try:
        return Intention(str(valeur).strip().lower())
    except ValueError:
        return Intention.AUTRE


def affiner_intention(intention_modele: Intention, message_normalise: str) -> Intention:
    """
    Combine le modèle et les règles :
    - le modèle n'a rien reconnu -> on tente les mots-clés (filet de sécurité) ;
    - une action explicite (réserver, annuler...) est conservée telle quelle ;
    - sinon, quelques mots-clés sans ambiguïté corrigent les confusions connues.
    """
    if intention_modele == Intention.AUTRE:
        return intention_par_mots_cles(message_normalise)
    if intention_modele in INTENTIONS_ACTION:
        return intention_modele
    if contient_un(message_normalise, MOTS_STATISTIQUES):
        return Intention.STATISTIQUES
    if contient_un(message_normalise, MOTS_DISPONIBILITE):
        return Intention.CONSULTER_DISPONIBILITE
    return intention_modele
