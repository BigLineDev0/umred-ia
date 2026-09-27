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
"""
import re
from collections.abc import Awaitable, Callable

from app.assistant import annulation, consultation, disponibilites, reservation
from app.assistant.contexte import Contexte
from app.core.security import Utilisateur
from app.core.session_store import Etape, terminer_etape
from app.nlu.extractor import extraire
from app.nlu.intentions import Intention
from app.schemas.chat import ChatResponse

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
    Intention.CREER_EQUIPEMENT: consultation.expliquer_creation_equipement,
    Intention.MON_NOM: consultation.donner_nom,
    Intention.IDENTITE: consultation.presenter_assistant,
    Intention.SALUTATION: consultation.saluer,
    Intention.AUTRE: consultation.repondre_autre,
}

# Valeur d'un bouton cliquable (« equip_3 », « alt_0 »...).
_VALEUR_BOUTON = re.compile(r"^(equip|dispo|alt|equiv|annul)_\d+$")


async def traiter_message(user: Utilisateur, session: dict, message: str) -> ChatResponse:
    ctx = Contexte.creer(user, session, message)
    etape = session["etape"]

    # --- 1. Réponse à une question posée au tour précédent ---
    if etape in GESTIONNAIRES_ETAPE:
        reponse = await GESTIONNAIRES_ETAPE[etape](ctx)
        if reponse is not None:
            return reponse
        terminer_etape(session)
        etape = None

    # Clic sur un bouton d'une ancienne réponse (étape terminée ou session
    # expirée) : inutile d'envoyer « equip_3 » au modèle de langage.
    if _VALEUR_BOUTON.match(ctx.message_normalise):
        return ChatResponse(reponse="Ce choix n'est plus valable (la demande a expiré ou a déjà été traitée). "
                                    "Pouvez-vous reformuler votre demande ?")

    # --- 2. Nouvelle demande (ou complément d'une réservation incomplète) ---
    ctx.extraction = await extraire(message, reservation_en_cours=etape == Etape.COLLECTE_RESERVATION)

    if etape == Etape.COLLECTE_RESERVATION:
        reponse = await reservation.poursuivre_collecte(ctx)
        if reponse is not None:
            return reponse
        terminer_etape(session)

    gestionnaire = GESTIONNAIRES_INTENTION.get(ctx.extraction.intention, consultation.repondre_autre)
    return await gestionnaire(ctx)
