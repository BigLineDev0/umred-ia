"""
Annulation d'une réservation : identification -> (choix) -> confirmation -> appel Django.
Seules les réservations actives et à venir de l'utilisateur sont
proposées ; Django revérifie de toute façon que la réservation lui
appartient avant d'annuler.
"""
import re

from rapidfuzz import fuzz

from app.assistant.contexte import (
    Contexte, commence_par_oui_ou_non, est_non, est_oui, lire_choix, lire_numero, options_confirmation,
)
from app.assistant.formatage import (
    formater_creneau, formater_date, formater_date_titre, formater_heure, formater_plage,
)
from app.core.session_store import Etape, definir_etape, terminer_etape
from app.nlu.texte import contient_un, normaliser_texte
from app.schemas.chat import ChatOption, ChatResponse, DetailsConfirmation
from app.services.django_client import annuler_reservation, get_reservations
from app.services.reservations import reservations_actives_a_venir

NOMBRE_MAX_CHOIX = 5
MOTS_PROCHAINE = ("derniere", "prochaine", "plus proche", "plus recente")
MOTS_TOUTES = ("toutes", "tous mes", "tout annuler", "l'ensemble")
# « les réservations de Moussa » : un prénom (majuscule) après « réservation(s) de ».
_RESERVATION_D_AUTRUI = re.compile(r"r[ée]servations?\s+(?:de|d['’])\s*([A-ZÀ-Ý][\w-]+)")
MESSAGE_AUTRUI = ("Je ne peux agir que sur vos propres réservations, une à la fois et après votre confirmation. "
                  "Pour la réservation d'une autre personne, adressez-vous à un technicien ou à un administrateur.")


def _vise_autrui(ctx: Contexte, reservations: list[dict]) -> bool:
    """« Annule les réservations de Moussa » : le nom cité n'est ni un équipement ni un laboratoire de l'utilisateur."""
    correspondance = _RESERVATION_D_AUTRUI.search(ctx.message)
    if not correspondance:
        return False
    nom = normaliser_texte(correspondance.group(1))
    connus = {normaliser_texte(mot) for r in reservations
              for texte in [*(r.get("equipements_noms") or []), r.get("laboratoire_nom") or ""] for mot in texte.split()}
    return nom not in connus and nom not in {normaliser_texte(m) for m in ("demain", "lundi", "mardi", "mercredi", "jeudi",
                                                                            "vendredi", "samedi", "dimanche")}


def _filtrer_par_equipement(reservations: list[dict], texte_equipement: str) -> list[dict]:
    cible = normaliser_texte(texte_equipement)
    return [
        r for r in reservations
        if any(fuzz.partial_ratio(cible, normaliser_texte(nom)) >= 85 for nom in r.get("equipements_noms") or [])
    ]


async def gerer_annulation(ctx: Contexte) -> ChatResponse:
    ext = ctx.extraction
    actives = reservations_actives_a_venir(await get_reservations(ctx.token, a_venir=True))
    if _vise_autrui(ctx, actives):
        # Django refuserait de toute façon (seules ses propres réservations
        # sont visibles) ; on le dit clairement au lieu de proposer les siennes.
        return ChatResponse(type="denied", reponse=MESSAGE_AUTRUI)
    if not actives:
        return ChatResponse(reponse="Vous n'avez aucune réservation à venir à annuler.")
    if contient_un(ctx.message_normalise, MOTS_TOUTES):
        # Pas d'annulation groupée : chaque annulation est confirmée une à une.
        return _proposer_choix(ctx, actives, "Je ne peux annuler qu'une réservation à la fois, après votre confirmation. "
                                             "Laquelle souhaitez-vous annuler ?")

    candidats = actives
    if ext.date:
        candidats = [r for r in candidats if r["date"] == ext.date]
        if ext.heure_debut:
            candidats = [r for r in candidats if r["heure_debut"][:5] == ext.heure_debut]
    elif contient_un(ctx.message_normalise, MOTS_PROCHAINE):
        # « Ma dernière réservation » au sens courant : celle qui arrive
        # bientôt (la plus proche dans le temps), pas la dernière créée.
        candidats = actives[:1]
    if ext.equipement:
        # Filtre facultatif : ignoré s'il élimine tout (nom mal reconnu).
        candidats = _filtrer_par_equipement(candidats, ext.equipement) or candidats

    if not candidats:
        quand = formater_date(ext.date) + (f" à {formater_heure(ext.heure_debut)}" if ext.heure_debut else "")
        return _proposer_choix(ctx, actives, f"Je ne trouve aucune réservation active {quand}. Voici vos prochaines réservations :")
    if len(candidats) == 1:
        return _demander_confirmation(ctx, candidats[0])
    return _proposer_choix(ctx, candidats, "Quelle réservation souhaitez-vous annuler ?")


def _proposer_choix(ctx: Contexte, reservations: list[dict], introduction: str) -> ChatResponse:
    proposees = reservations[:NOMBRE_MAX_CHOIX]
    definir_etape(ctx.session, Etape.SELECTION_ANNULATION, reservations=proposees)
    # Chaque réservation est un bouton (date et horaire en titre,
    # équipements en sous-titre) : le texte se limite à la question.
    options = [
        ChatOption(label=f"{formater_date_titre(r['date'])} · {formater_creneau(r['heure_debut'], r['heure_fin'])}",
                   value=f"annul_{r['id']}",
                   description=", ".join(r.get("equipements_noms") or []) or r.get("laboratoire_nom"))
        for r in proposees
    ]
    return ChatResponse(reponse=introduction, options=options)


async def traiter_selection(ctx: Contexte) -> ChatResponse | None:
    proposees = ctx.contexte_etape["reservations"]
    identifiant = lire_choix(ctx.message_normalise, "annul")
    if identifiant is not None:
        choisie = next((r for r in proposees if r["id"] == identifiant), None)
    else:
        index = lire_numero(ctx.message_normalise, len(proposees))
        choisie = proposees[index] if index is not None else None
    return _demander_confirmation(ctx, choisie) if choisie else None


def _demander_confirmation(ctx: Contexte, reservation: dict) -> ChatResponse:
    definir_etape(ctx.session, Etape.CONFIRMATION_ANNULATION, reservation=reservation)
    return ChatResponse(
        reponse="Confirmez-vous l'annulation de cette réservation ?",
        necessite_confirmation=True,
        options=options_confirmation(),
        details_confirmation=DetailsConfirmation(
            laboratoire=reservation.get("laboratoire_nom"),
            equipement=", ".join(reservation.get("equipements_noms") or []) or None,
            date=formater_date(reservation["date"]),
            heure_debut=reservation["heure_debut"][:5],
            heure_fin=reservation["heure_fin"][:5],
        ),
    )


async def traiter_confirmation(ctx: Contexte) -> ChatResponse | None:
    reservation = ctx.contexte_etape["reservation"]
    if est_non(ctx.message_normalise):
        terminer_etape(ctx.session)
        return ChatResponse(reponse="D'accord, je conserve votre réservation.")
    if not est_oui(ctx.message_normalise):
        if commence_par_oui_ou_non(ctx.message_normalise):
            # « Oui mais... » : on ne détruit rien sur une réponse ambiguë.
            return ChatResponse(reponse="Je n'ai rien annulé. Répondez « oui » pour confirmer l'annulation de cette "
                                        "réservation, ou « non » pour la conserver.",
                                necessite_confirmation=True, options=options_confirmation())
        return None

    terminer_etape(ctx.session)
    resultat = await annuler_reservation(ctx.token, reservation["id"])
    if not resultat.ok:
        return ChatResponse(reponse=f"Je n'ai pas pu annuler la réservation : {resultat.detail}")
    return ChatResponse(reponse=f"Votre réservation du {formater_date(reservation['date'])} "
                                f"{formater_plage(reservation['heure_debut'], reservation['heure_fin'])} a bien été annulée.")
