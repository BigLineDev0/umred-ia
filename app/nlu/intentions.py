"""
Catalogue unique des intentions comprises par l'assistant. Il est partagé
par le prompt du modèle, l'extraction par règles et le routage : ajouter
une intention se fait ici, à un seul endroit.
"""
import re
from enum import StrEnum

from app.core.navigation import page_demandee
from app.nlu.texte import contient_un


class Intention(StrEnum):
    RESERVER = "reserver"
    ANNULER = "annuler"
    CONSULTER_MES_RESERVATIONS = "consulter_mes_reservations"
    CONSULTER_DISPONIBILITE = "consulter_disponibilite"
    MAINTENANCE = "maintenance"
    STATISTIQUES = "statistiques"
    CREER_EQUIPEMENT = "creer_equipement"
    NAVIGUER = "naviguer"
    MOTIF_REFUS = "motif_refus"
    STATUT_DEMANDE = "statut_demande"
    BILAN = "bilan"
    CONSEIL_EQUIPEMENT = "conseil_equipement"
    REMERCIEMENT = "remerciement"
    AIDE = "aide"
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
    # « Pourquoi ma demande est encore en attente ? » : l'état d'une demande
    # en cours, à ne pas confondre avec un refus (cherché juste après).
    (Intention.STATUT_DEMANDE, ("en attente", "ou en est", "statut de ma", "etat de ma", "pas encore valid",
                                "toujours pas valid", "pas encore trait", "toujours pas trait", "quand sera valid",
                                "quand ma demande", "pas encore accept", "toujours pas accept", "acceptee",
                                "ete validee", "est validee", "elle validee", "est confirmee", "elle confirmee",
                                "est-elle confirmee", "bien confirmee")),
    # « Pourquoi ma réservation a été refusée ? » : une explication, et
    # surtout pas une annulation (« annul » est cherché plus bas).
    (Intention.MOTIF_REFUS, ("refus", "rejet", "pourquoi ma reservation", "pourquoi ma demande", "pourquoi mes reservations")),
    # Une question sur le FONCTIONNEMENT (« comment faire une réservation ? »)
    # n'est pas une demande d'action : elle ne doit pas lancer une réservation.
    # Réponse déterministe (app/assistant/aide.py), sans modèle de langage.
    (Intention.AIDE, ("comment faire", "comment reserver", "comment annuler", "comment fonctionne",
                      "comment ca marche", "comment on ", "comment utiliser", "comment signaler", "signaler une panne",
                      "qui valide", "qui doit valider", "qui va valider", "horaires", "heures d'ouverture",
                      "heure d'ouverture", "a quelle heure ouvre", "a quelle heure ferme", "mode d'emploi",
                      "consignes de securite")),
    (Intention.ANNULER, ("annule", "annuler", "annulation", "supprime ma reservation", "supprimer ma reservation",
                         "supprime la reservation", "plus besoin de ma", "plus besoin de la reservation",
                         "decommand", "desist")),
    # « Fais-moi le bilan des réservations de la semaine » : un résumé
    # chiffré, et non une réservation (« reserv » est cherché plus bas).
    (Intention.BILAN, ("bilan", "recap", "resume de mes", "resume des", "resume de la semaine", "faire le point",
                       "fais le point", "fais-moi le point", "synthese de mes", "resume-moi", "resume moi",
                       "mon activite", "combien d'heures", "nombre d'heures", "heures d'utilisation")),
    (Intention.CONSULTER_MES_RESERVATIONS, ("mes reservations", "mes demandes", "prochaines reservations", "mon planning",
                                            "mes creneaux", "ma prochaine reservation", "ma derniere reservation",
                                            "de prevu", "mon programme", "mon agenda")),
    (Intention.CREER_EQUIPEMENT, ("ajoute un equipement", "ajouter un equipement", "creer un equipement", "nouvel equipement")),
    (Intention.STATISTIQUES, ("combien", "statistique", "nombre de")),
    # Signalement d'une panne : APRÈS les statistiques (« combien
    # d'équipements sont en panne ? » est une question chiffrée).
    (Intention.AIDE, ("casse", "ne marche plus", "marche pas", "ne fonctionne plus", "fonctionne pas", "defectueu",
                      "en panne", "est hs", "abime")),
    # « Quel équipement dois-je utiliser pour... ? » : un conseil, avant le
    # verbe « utiliser » qui signale une réservation (ignoré si la question
    # porte sur une disponibilité, voir intention_par_mots_cles).
    (Intention.CONSEIL_EQUIPEMENT, ("quel equipement", "quel appareil", "quelle machine", "quel instrument",
                                    "dois-je utiliser", "dois je utiliser", "faut-il utiliser", "faut il utiliser",
                                    "lequel utiliser", "me conseill", "conseille-moi", "recommand", "adapte pour",
                                    "adapte a ", "pour faire une analyse", "pour realiser une", "pour analyser")),
    # « Réserve-moi un créneau libre » est une réservation : le verbe
    # d'action passe avant les mots de consultation.
    (Intention.RESERVER, ("reserv", "je veux prendre", "bloquer le", "bloque le", "veux utiliser", "voudrais utiliser",
                          "aimerais utiliser", "souhaite utiliser", "souhaiterais utiliser", "besoin du ",
                          "besoin de la ", "besoin de l'", "me garder", "garde-moi", "mets-moi", "mets moi",
                          "inscris-moi", "inscris moi")),
    # « occupé » mais pas « m'occuper » (le message est complété d'une espace finale).
    (Intention.CONSULTER_DISPONIBILITE, ("dispo", "libre", "creneaux", "occupe ", "occupee", "occupes ", "est pris",
                                         "deja pris", "de la place", "etre possible", "est possible",
                                         "peux prendre", "puis-je prendre")),
    (Intention.MAINTENANCE, ("maintenance", "entretien", "revision", "revise", "entretenu")),
    (Intention.MON_NOM, ("comment je m'appelle", "qui suis-je", "qui suis je", "mon nom")),
    (Intention.IDENTITE, ("qui es-tu", "qui es tu", "tu es qui", "t'es qui", "qui t'a cree", "que peux-tu faire",
                          "tu fais quoi", "que fais-tu", "tu peux faire quoi", "que pouvez-vous faire", "a quoi tu sers",
                          "que sais-tu faire", "es-tu un robot", "tu sers a quoi")),
    # « ça va » n'est une salutation que seul (voir SALUTATIONS_COURTES) :
    # « la centrifugeuse, ça va être possible ? » est une vraie question.
    (Intention.SALUTATION, ("bonjour", "salut", "bonsoir", "hello", "coucou")),
    # Politesse de fin d'échange : réponse immédiate, sans modèle de langage.
    (Intention.REMERCIEMENT, ("merci", "au revoir", "a bientot", "bonne journee", "bonne soiree", "parfait",
                              "super", "genial", "top")),
]

# Le petit modèle confond régulièrement ces intentions (« combien de
# réservations ai-je ? » -> consulter_mes_reservations). Ces mots-clés
# sont non ambigus : on les laisse primer sur le modèle.
MOTS_STATISTIQUES = ("combien", "statistique", "nombre de")
MOTS_DISPONIBILITE = ("dispo", "libre")  # « dispo » couvre disponible / disponibilité
# Intentions d'action qu'aucune heuristique ne doit écraser.
INTENTIONS_ACTION = {Intention.RESERVER, Intention.ANNULER, Intention.CREER_EQUIPEMENT}
# Intentions que les mots-clés reconnaissent sans ambiguïté : elles priment
# sur une intention plus générale proposée par le modèle.
INTENTIONS_PRIORITAIRES = {Intention.STATUT_DEMANDE, Intention.BILAN}


# « C'est quoi un thermocycleur ? » est une question générale, mais
# « c'est quoi mes réservations ? » est bien une consultation.
QUESTIONS_DEFINITION = ("c'est quoi", "qu'est-ce que", "qu'est ce que")
POSSESSIFS = ("mes ", "ma ", "mon ", "j'ai ")
SALUTATIONS_COURTES = ("ca va", "comment vas-tu", "comment allez-vous")
# Abréviations courantes ramenées au mot complet avant la recherche.
ABREVIATIONS = {"resa": "reservation", "resas": "reservations"}


def intention_par_mots_cles(message_normalise: str) -> Intention:
    for abreviation, complet in ABREVIATIONS.items():
        message_normalise = re.sub(rf"\b{abreviation}\b", complet, message_normalise)
    message_normalise = f"{message_normalise} "
    # Une page de l'application explicitement demandée (« je veux gérer
    # les laboratoires », « ajouter un équipement ») : voir app/core/navigation.py.
    if page_demandee(message_normalise):
        return Intention.NAVIGUER
    if contient_un(message_normalise, QUESTIONS_DEFINITION) and not contient_un(message_normalise, POSSESSIFS):
        return Intention.AUTRE
    question_de_disponibilite = contient_un(message_normalise, MOTS_DISPONIBILITE)
    if len(message_normalise.split()) <= 4 and contient_un(message_normalise, SALUTATIONS_COURTES):
        return Intention.SALUTATION
    # « Le spectrophotomètre est-il réservable lundi ? » : une disponibilité,
    # alors que « reserv » désigne sinon une réservation.
    if "reservable" in message_normalise:
        return Intention.CONSULTER_DISPONIBILITE
    for intention, mots_cles in MOTS_CLES_INTENTIONS:
        if intention == Intention.CONSEIL_EQUIPEMENT and question_de_disponibilite:
            # « Quel appareil est libre demain ? » : une disponibilité.
            continue
        if contient_un(message_normalise, mots_cles):
            return intention
    return Intention.AUTRE


def valider_intention(valeur: object) -> Intention:
    """Toute valeur hors catalogue (hallucination du modèle) devient AUTRE."""
    try:
        return Intention(str(valeur).strip().lower())
    except ValueError:
        return Intention.AUTRE


# Indices lexicaux minimaux d'une intention proposée par le modèle. Mesuré
# sur Qwen2.5-1.5B : « Merci beaucoup ! » était classé motif_refus, ce qui
# affichait une réservation refusée sans rapport. Une intention sans aucun
# de ses indices est écartée (traitée comme « autre »).
INDICES_INTENTION: dict[Intention, tuple[str, ...]] = {
    Intention.MOTIF_REFUS: ("refus", "rejet", "pourquoi", "motif", "raison"),
    Intention.ANNULER: ("annul", "supprim", "retir", "desist", "plus besoin", "decommand", "libere"),
    Intention.STATUT_DEMANDE: ("attente", "statut", "etat", "ou en est", "valid", "traite", "accept"),
    Intention.BILAN: ("bilan", "recap", "resume", "point", "synthese"),
    Intention.STATISTIQUES: ("combien", "statistique", "nombre", "total", "taux"),
    Intention.MON_NOM: ("nom", "appelle", "qui suis"),
}


def plausible(intention: Intention, message_normalise: str) -> bool:
    indices = INDICES_INTENTION.get(intention)
    return indices is None or contient_un(message_normalise, indices)


def affiner_intention(intention_modele: Intention, message_normalise: str) -> Intention:
    """
    Combine le modèle et les règles :
    - le modèle n'a rien reconnu -> on tente les mots-clés (filet de sécurité) ;
    - une action explicite (réserver, annuler...) est conservée telle quelle ;
    - sinon, quelques mots-clés sans ambiguïté corrigent les confusions connues.
    """
    if not plausible(intention_modele, message_normalise):
        intention_modele = Intention.AUTRE
    if intention_modele == Intention.AUTRE:
        return intention_par_mots_cles(message_normalise)
    if intention_modele in INTENTIONS_ACTION:
        return intention_modele
    par_mots_cles = intention_par_mots_cles(message_normalise)
    if par_mots_cles in INTENTIONS_PRIORITAIRES:
        return par_mots_cles
    if contient_un(message_normalise, MOTS_STATISTIQUES):
        return Intention.STATISTIQUES
    if contient_un(message_normalise, MOTS_DISPONIBILITE):
        return Intention.CONSULTER_DISPONIBILITE
    return intention_modele
