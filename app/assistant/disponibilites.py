"""
Questions de disponibilité. Deux cas :
- un créneau PRÉCIS (« le PCR est-il libre demain de 9h à 11h ? ») : la
  réponse est celle de Django (/reservations/verifier/), avec ses
  alternatives en cas de conflit ;
- une période (« demain matin », « cette semaine ») : on affiche les
  plages libres calculées à partir des réservations renvoyées par Django.
"""
import asyncio

from app.assistant.contexte import Contexte, lire_choix
from app.assistant.formatage import formater_date, formater_heure, formater_plage
from app.assistant.reservation import (
    MOTIF_PAR_DEFAUT, horaires_etablissement, proposer_alternatives, reserver_equipement_choisi,
)
from app.core import temps
from app.core.constantes import STATUTS_EQUIPEMENT_NON_RESERVABLES
from app.core.session_store import Etape, definir_etape, memoriser
from app.schemas.chat import ChatOption, ChatResponse
from app.services.disponibilite import (
    LIBELLES_MOMENTS, calculer_creneaux_libres, enveloppe, fenetre_moment, jours_de_la_periode, restreindre,
)
from app.services.django_client import get_creneaux_occupes, get_equipements, verifier_reservation
from app.services.matching import resoudre_equipement

NOMBRE_MAX_EQUIPEMENTS_AFFICHES = 8  # au-delà, la réponse devient illisible dans une bulle de chat
APPELS_DJANGO_SIMULTANES = 5          # parallélisme borné pour ne pas saturer Django


def _formater_creneaux(creneaux: list[tuple[str, str]]) -> str:
    return ", ".join(f"{formater_heure(d)}–{formater_heure(f)}" for d, f in creneaux) or "aucun créneau libre"


async def consulter_disponibilite(ctx: Contexte) -> ChatResponse:
    ext = ctx.extraction
    equipements = await get_equipements(ctx.token)
    equipement = resoudre_equipement(ext.equipement or ctx.message, equipements)
    if equipement:
        memoriser(ctx.session, equipement=equipement, date=ext.date)
        if ext.date and ext.heure_debut and ext.heure_fin:
            return await _verifier_creneau_precis(ctx, equipement)
        return await _disponibilites_equipement(ctx, equipement)
    # Aucun équipement précis (« quels équipements sont disponibles ? ») :
    # on liste ceux qui ont au moins un créneau libre à la date demandée.
    return await _lister_equipements_disponibles(ctx, equipements, ext.date or temps.aujourd_hui().isoformat())


async def _verifier_creneau_precis(ctx: Contexte, equipement: dict) -> ChatResponse:
    ext = ctx.extraction
    if equipement.get("statut") in STATUTS_EQUIPEMENT_NON_RESERVABLES:
        return ChatResponse(type="availability", reponse=f"{equipement['nom']} n'est actuellement pas réservable "
                                                         "(panne, maintenance ou hors service).",
                            data={"equipement": equipement["nom"], "disponible": False})
    demande = {"laboratoire": equipement["laboratoire"], "equipements": [equipement["id"]], "date": ext.date,
               "heure_debut": ext.heure_debut, "heure_fin": ext.heure_fin, "motif": MOTIF_PAR_DEFAUT}
    verification = await verifier_reservation(ctx.token, demande)
    quand = f"{formater_date(ext.date)} {formater_plage(ext.heure_debut, ext.heure_fin)}"

    if verification.status_code == 400:
        return ChatResponse(type="availability", reponse=f"Ce créneau n'est pas réservable : {verification.detail}",
                            data={"equipement": equipement["nom"], "disponible": False})
    if not verification.ok:
        # Vérification exacte impossible : on retombe sur les plages libres.
        return await _disponibilites_equipement(ctx, equipement)
    if verification.data.get("disponible") is False:
        return proposer_alternatives(ctx, demande, [equipement], verification.data,
                                     introduction=f"Non, {equipement['nom']} est déjà réservé {quand}.")

    definir_etape(ctx.session, Etape.SELECTION_DISPONIBILITE, date=ext.date, equipements={equipement["id"]: equipement},
                  heure_debut=ext.heure_debut, heure_fin=ext.heure_fin)
    return ChatResponse(
        type="availability",
        reponse=f"Oui, {equipement['nom']} ({equipement['laboratoire_nom']}) est disponible {quand}.",
        options=[ChatOption(label="Réserver ce créneau", value=f"dispo_{equipement['id']}")],
        data={"equipement": equipement["nom"], "disponible": True, "date": ext.date,
              "creneaux": [{"date": ext.date, "debut": ext.heure_debut, "fin": ext.heure_fin}]},
    )


async def _disponibilites_equipement(ctx: Contexte, equipement: dict) -> ChatResponse:
    ext = ctx.extraction
    if equipement.get("statut") in STATUTS_EQUIPEMENT_NON_RESERVABLES:
        return ChatResponse(reponse=f"{equipement['nom']} n'est actuellement pas réservable (panne, maintenance ou hors service).")

    # Une date précise l'emporte ; sinon aujourd'hui, ou les 7 prochains
    # jours si l'utilisateur a parlé de « semaine ».
    jours = [ext.date] if ext.date else jours_de_la_periode(ext.periode)
    occupes = await get_creneaux_occupes(ctx.token, equipement["id"], jours[0], jours[-1])
    horaires = await horaires_etablissement(ctx)
    libres = calculer_creneaux_libres(occupes, jours, **horaires)
    if ext.moment:
        # « Demain matin » : seules les plages du matin nous intéressent.
        fenetre = fenetre_moment(ext.moment, **enveloppe(horaires))
        libres = {j: restreindre(c, fenetre) for j, c in libres.items()}
    lignes = [f"- {formater_date(j)} : {_formater_creneaux(c)}" for j, c in libres.items()]
    donnees = {"equipement": equipement["nom"], "disponible": any(libres.values()),
               "creneaux": [{"date": j, "debut": d, "fin": f} for j, c in libres.items() for d, f in c]}

    options = None
    if len(jours) == 1 and libres[jours[0]]:
        # Raccourci : réserver directement depuis la réponse.
        definir_etape(ctx.session, Etape.SELECTION_DISPONIBILITE, date=jours[0], equipements={equipement["id"]: equipement},
                      moment=ext.moment)
        options = [ChatOption(label=f"Réserver {equipement['nom']}", value=f"dispo_{equipement['id']}")]

    moment = f" {LIBELLES_MOMENTS[ext.moment]}" if ext.moment else ""
    if len(jours) == 1:
        introduction = (f"Oui, {equipement['nom']} ({equipement['laboratoire_nom']}) est libre {formater_date(jours[0])}{moment} :"
                        if libres[jours[0]] else
                        f"Non, {equipement['nom']} ({equipement['laboratoire_nom']}) n'a aucun créneau libre {formater_date(jours[0])}{moment}.")
        lignes = [f"- {_formater_creneaux(libres[jours[0]])}"] if libres[jours[0]] else []
    else:
        introduction = f"Disponibilités de {equipement['nom']} ({equipement['laboratoire_nom']}){moment} :"
    return ChatResponse(type="availability", reponse="\n".join([introduction, *lignes]), options=options, data=donnees)


async def _lister_equipements_disponibles(ctx: Contexte, equipements: list[dict], date_iso: str) -> ChatResponse:
    if date_iso < temps.aujourd_hui().isoformat():
        return ChatResponse(reponse=f"Le {formater_date(date_iso)} est déjà passé. Pour quelle date souhaitez-vous voir les disponibilités ?")

    # Un équipement en panne, en maintenance ou hors service n'est jamais
    # proposé, quel que soit son planning.
    reservables = [e for e in equipements if e.get("statut") not in STATUTS_EQUIPEMENT_NON_RESERVABLES]
    limite = asyncio.Semaphore(APPELS_DJANGO_SIMULTANES)
    horaires = await horaires_etablissement(ctx)

    async def creneaux_libres(e: dict) -> list[tuple[str, str]]:
        async with limite:
            occupes = await get_creneaux_occupes(ctx.token, e["id"], date_iso, date_iso)
        return calculer_creneaux_libres(occupes, [date_iso], **horaires)[date_iso]

    tous_les_creneaux = await asyncio.gather(*(creneaux_libres(e) for e in reservables))
    if ctx.extraction.moment:
        fenetre = fenetre_moment(ctx.extraction.moment, **enveloppe(horaires))
        tous_les_creneaux = [restreindre(c, fenetre) for c in tous_les_creneaux]
    disponibles = [(e, c) for e, c in zip(reservables, tous_les_creneaux) if c]
    if not disponibles:
        return ChatResponse(reponse=f"Aucun équipement n'a de créneau libre {formater_date(date_iso)}.")

    affiches = disponibles[:NOMBRE_MAX_EQUIPEMENTS_AFFICHES]
    lignes = [f"Équipements disponibles {formater_date(date_iso)} :"]
    lignes += [f"- {e['nom']} ({e['laboratoire_nom']}) : {_formater_creneaux(c)}" for e, c in affiches]
    if len(disponibles) > len(affiches):
        lignes.append(f"… et {len(disponibles) - len(affiches)} autre(s). Précisez un équipement pour affiner.")
    lignes.append("\nCliquez sur un équipement pour le réserver.")

    # On mémorise la date et la liste proposée côté serveur : le bouton ne
    # transporte que l'id, qui est vérifié à la réception (pas de date ni
    # d'équipement arbitraire injectable par le client).
    definir_etape(ctx.session, Etape.SELECTION_DISPONIBILITE, date=date_iso, equipements={e["id"]: e for e, _ in affiches})
    options = [ChatOption(label=f"{e['nom']} — {e['laboratoire_nom']}", value=f"dispo_{e['id']}") for e, _ in affiches]
    donnees = [{"equipement": e["nom"], "laboratoire": e["laboratoire_nom"], "date": date_iso,
                "creneaux": [{"debut": d, "fin": f} for d, f in c]} for e, c in affiches]
    return ChatResponse(type="availability", reponse="\n".join(lignes), options=options, data=donnees)


async def traiter_selection_disponibilite(ctx: Contexte) -> ChatResponse | None:
    etape = ctx.contexte_etape
    identifiant = lire_choix(ctx.message_normalise, "dispo")
    equipement = etape["equipements"].get(identifiant) if identifiant is not None else None
    if equipement is None:
        return None
    return await reserver_equipement_choisi(ctx, equipement, etape["date"], etape.get("heure_debut"), etape.get("heure_fin"),
                                            etape.get("moment"))
