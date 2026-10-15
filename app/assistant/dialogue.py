"""
Chef d'orchestre du dialogue. Pour chaque message :

1. Une étape est en cours (on attend un choix, un oui/non...) ?
   -> son gestionnaire tente d'interpréter le message comme la réponse
      attendue. S'il n'y parvient pas (il renvoie None), c'est que
      l'utilisateur est passé à autre chose : on clôt l'étape et on
      traite le message comme une nouvelle demande.
2. Sinon, extraction (intention + informations) puis routage vers le
   traitement de l'intention.

Les deux tables de routage ci-dessous remplacent une longue cascade de
`if` : ajouter une intention = écrire une fonction + une ligne ici.

C'est ici que se fait le « tool calling » : le modèle de langage ne choisit
qu'une intention dans une liste fermée (sortie JSON validée), et c'est ce
routeur déterministe qui décide quels outils Django appeler. Les outils
sont les fonctions de app/services/django_client.py (une fonction = un
endpoint, appelé avec le JWT de l'utilisateur) :

    intention                   outils Django appelés
    reserver                    get_equipements, get_creneaux_occupes, verifier_reservation, creer_reservation
    consulter_disponibilite     get_equipements, get_creneaux_occupes, verifier_reservation
    consulter_mes_reservations  get_reservations (filtres de période appliqués par Django)
    annuler                     get_reservations, annuler_reservation (après confirmation)
    motif_refus                 get_reservations(statut=REFUSEE)
    statut_demande              get_reservations(statut=EN_ATTENTE), get_equipements
    bilan                       get_reservations(période, archives comprises) ; chiffres calculés ici
    conseil_equipement          get_equipements (fiches : nom, catégorie, description)
    aide                        get_horaires, get_equipements (mode d'emploi) ou réponse fixe
    maintenance                 get_equipements, get_maintenances
    statistiques                get_reservations, get_equipements, get_maintenances
    naviguer / creer_equipement aucun : catalogue de pages app/core/navigation.py
"""
import re
from collections.abc import Awaitable, Callable
from datetime import date, timedelta

from app.assistant import aide, annulation, consultation, disponibilites, navigation, reservation, suivi
from app.assistant.contexte import Contexte, est_non, est_oui, est_relance
from app.assistant.formatage import formater_date
from app.core import metriques
from app.core.security import Utilisateur
from app.core.session_store import Etape, terminer_etape
from app.nlu.domaine import peut_concerner_la_plateforme
from app.nlu.extractor import extraire
from app.nlu.intentions import Intention
from app.nlu.regles import extraire_par_regles
from app.schemas.chat import ChatResponse
from app.services.django_client import get_equipements
from app.services.matching import equipements_mentionnes

Gestionnaire = Callable[[Contexte], Awaitable[ChatResponse | None]]

GESTIONNAIRES_ETAPE: dict[Etape, Gestionnaire] = {
    Etape.SELECTION_EQUIPEMENT: reservation.traiter_selection_equipement,
    Etape.CONFIRMATION_RESERVATION: reservation.traiter_confirmation,
    Etape.SELECTION_ALTERNATIVE: reservation.traiter_selection_alternative,
    Etape.SELECTION_DISPONIBILITE: disponibilites.traiter_selection_disponibilite,
    Etape.SELECTION_ANNULATION: annulation.traiter_selection,
    Etape.CONFIRMATION_ANNULATION: annulation.traiter_confirmation,
}

GESTIONNAIRES_INTENTION: dict[Intention, Gestionnaire] = {
    Intention.RESERVER: reservation.demarrer_reservation,
    Intention.ANNULER: annulation.gerer_annulation,
    Intention.CONSULTER_MES_RESERVATIONS: consultation.lister_mes_reservations,
    Intention.CONSULTER_DISPONIBILITE: disponibilites.consulter_disponibilite,
    Intention.MAINTENANCE: consultation.prochaine_maintenance,
    Intention.STATISTIQUES: consultation.statistiques,
    Intention.CREER_EQUIPEMENT: navigation.creer_equipement,
    Intention.NAVIGUER: navigation.naviguer,
    Intention.MOTIF_REFUS: consultation.expliquer_refus,
    Intention.STATUT_DEMANDE: suivi.statut_demandes,
    Intention.BILAN: suivi.bilan,
    Intention.CONSEIL_EQUIPEMENT: aide.conseiller_equipement,
    Intention.AIDE: aide.repondre_aide,
    Intention.REMERCIEMENT: consultation.remercier,
    Intention.MON_NOM: consultation.donner_nom,
    Intention.IDENTITE: consultation.presenter_assistant,
    Intention.SALUTATION: consultation.saluer,
    Intention.AUTRE: consultation.repondre_autre,
}

# Valeur d'un bouton cliquable (« equip_3 », « alt_0 »...).
_VALEUR_BOUTON = re.compile(r"^(equip|dispo|alt|equiv|partiel|annul|creneau)_\d+$")


# Intentions qu'une relance (« et jeudi ? ») peut reprendre : celles dont la
# réponse dépend d'une date. Une relance ne CONFIRME jamais rien : une
# réservation reprise repasse par la vérification Django et la confirmation.
INTENTIONS_REPRISES = {Intention.CONSULTER_DISPONIBILITE, Intention.RESERVER, Intention.CONSULTER_MES_RESERVATIONS,
                       Intention.MAINTENANCE}
# Intentions qui ne changent pas le sujet de la conversation.
INTENTIONS_NEUTRES = {Intention.AUTRE, Intention.SALUTATION, Intention.REMERCIEMENT, Intention.IDENTITE, Intention.MON_NOM}
_DECALAGES_RELATIFS = {"surlendemain": 2, "lendemain": 1, "veille": -1}


async def traiter_message(user: Utilisateur, session: dict, message: str) -> ChatResponse:
    ctx = Contexte.creer(user, session, message)
    etape = session["etape"]

    # --- 1. Réponse à une question posée au tour précédent ---
    if etape in GESTIONNAIRES_ETAPE:
        reponse = await GESTIONNAIRES_ETAPE[etape](ctx)
        if reponse is not None:
            metriques.noter(chemin="etape", intention=str(etape))
            return reponse
        terminer_etape(session)
        etape = None

    # Choix d'un créneau proposé pendant la collecte (« le premier », clic).
    if etape == Etape.COLLECTE_RESERVATION:
        reponse = await reservation.traiter_choix_creneau(ctx)
        if reponse is not None:
            metriques.noter(chemin="etape", intention=str(etape))
            return reponse

    # Clic sur un bouton d'une ancienne réponse (étape terminée ou session
    # expirée) : inutile d'envoyer « equip_3 » au modèle de langage.
    if _VALEUR_BOUTON.match(ctx.message_normalise):
        return ChatResponse(reponse="Ce choix n'est plus valable (la demande a expiré ou a déjà été traitée). "
                                    "Pouvez-vous reformuler votre demande ?")
    # « Oui » sans rien à confirmer (double clic, confirmation déjà traitée,
    # session expirée) : surtout pas d'action, et pas d'appel au modèle.
    if etape is None and (est_oui(ctx.message_normalise) or est_non(ctx.message_normalise)):
        return ChatResponse(reponse="Il n'y a aucune action en attente de confirmation. Que souhaitez-vous faire ?",
                            options=consultation.SUGGESTIONS_AIDE)
    # « Oui » à « Souhaitez-vous une autre date ? » : il manque la date elle-même.
    if etape == Etape.COLLECTE_RESERVATION and est_oui(ctx.message_normalise):
        return ChatResponse(type="clarification", intention="reserver",
                            reponse="Indiquez-moi la date ou l'horaire souhaité, par exemple « jeudi de 10h à 12h ».")

    # --- 2. Nouvelle demande (ou complément d'une réservation incomplète) ---
    memoire = session["memoire"]
    ctx.relance = bool(memoire.get("intention")) and est_relance(ctx.message_normalise)
    ctx.extraction = await extraire(
        message,
        reservation_en_cours=etape == Etape.COLLECTE_RESERVATION,
        reprise_possible=ctx.relance and memoire.get("intention") in INTENTIONS_REPRISES,
        pertinent=lambda: _peut_concerner_la_plateforme(ctx),
    )
    _resoudre_le_contexte(ctx)
    metriques.noter(intention=str(ctx.extraction.intention))

    if etape == Etape.COLLECTE_RESERVATION:
        reponse = await reservation.poursuivre_collecte(ctx)
        if reponse is not None:
            return _signaler_ambiguite(ctx, reponse)
        terminer_etape(session)

    gestionnaire = GESTIONNAIRES_INTENTION.get(ctx.extraction.intention, consultation.repondre_autre)
    reponse = await gestionnaire(ctx)
    if ctx.extraction.intention not in INTENTIONS_NEUTRES:
        memoire["intention"] = ctx.extraction.intention
    return _signaler_ambiguite(ctx, reponse)


def _resoudre_le_contexte(ctx: Contexte) -> None:
    """
    Complète l'extraction avec la conversation en cours, uniquement pour une
    relance : « et jeudi après-midi ? » reprend l'intention précédente,
    « le lendemain » se calcule à partir de la date dont on parlait.
    """
    ext, memoire = ctx.extraction, ctx.session["memoire"]
    if not ctx.relance:
        return
    decalage = next((d for mot, d in _DECALAGES_RELATIFS.items() if mot in ctx.message_normalise), None)
    if decalage is not None and not ext.date and memoire.get("date"):
        ext.date = (date.fromisoformat(memoire["date"]) + timedelta(days=decalage)).isoformat()
    a_une_date = ext.date or ext.moment or ext.heure_debut or ext.periode != "jour"
    if ext.intention == Intention.AUTRE and a_une_date and memoire.get("intention") in INTENTIONS_REPRISES:
        ext.intention = memoire["intention"]


async def _peut_concerner_la_plateforme(ctx: Contexte) -> bool:
    """
    Le modèle n'est consulté que si le message a un lien possible avec la
    plateforme : vocabulaire du laboratoire ou de la plateforme, message à
    la première personne, date ou horaire, ou nom d'un équipement réel.
    Sinon (« Quelle est la capitale du Japon ? ») il ne pourrait que
    répondre « autre », après ~8 s de calcul.
    """
    if peut_concerner_la_plateforme(ctx.message) or extraire_par_regles(ctx.message).a_des_informations_de_reservation:
        return True
    return bool(equipements_mentionnes(ctx.message, await get_equipements(ctx.token)))


def _signaler_ambiguite(ctx: Contexte, reponse: ChatResponse) -> ChatResponse:
    """« Vendredi prochain » dit un jeudi : on dit quelle date a été retenue, et l'autre lecture possible."""
    ext = ctx.extraction
    if ext and ext.date and ext.date_alternative and reponse.type not in ("error", "denied"):
        reponse.reponse += (f"\n\nJ'ai compris {formater_date(ext.date)}. Si vous pensiez au "
                            f"{formater_date(ext.date_alternative)}, précisez-le.")
    return reponse
