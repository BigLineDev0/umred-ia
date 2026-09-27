import asyncio
from datetime import date

from app.assistant.contexte import Contexte, lire_choix
from app.assistant.formatage import formater_date, formater_heure
from app.assistant.reservation import reserver_equipement_choisi
from app.core.constantes import STATUTS_EQUIPEMENT_NON_RESERVABLES
from app.core.session_store import Etape, definir_etape
from app.schemas.chat import ChatOption, ChatResponse
from app.services.disponibilite import calculer_creneaux_libres, jours_de_la_periode
from app.services.django_client import get_creneaux_occupes, get_equipements
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
        return await _disponibilites_equipement(ctx, equipement)
    # Aucun équipement précis (« quels équipements sont disponibles ? ») :
    # on liste ceux qui ont au moins un créneau libre à la date demandée.
    return await _lister_equipements_disponibles(ctx, equipements, ext.date or date.today().isoformat())


async def _disponibilites_equipement(ctx: Contexte, equipement: dict) -> ChatResponse:
    ext = ctx.extraction
    if equipement.get("statut") in STATUTS_EQUIPEMENT_NON_RESERVABLES:
        return ChatResponse(reponse=f"{equipement['nom']} n'est actuellement pas réservable (panne, maintenance ou hors service).")

    # Une date précise l'emporte ; sinon aujourd'hui, ou les 7 prochains
    # jours si l'utilisateur a parlé de « semaine ».
    jours = [ext.date] if ext.date else jours_de_la_periode(ext.periode)
    occupes = await get_creneaux_occupes(ctx.token, equipement["id"], jours[0], jours[-1])
    libres = calculer_creneaux_libres(occupes, jours)
    lignes = [f"- {formater_date(j)} : {_formater_creneaux(c)}" for j, c in libres.items()]

    options = None
    if len(jours) == 1 and libres[jours[0]]:
        # Raccourci : réserver directement depuis la réponse.
        definir_etape(ctx.session, Etape.SELECTION_DISPONIBILITE, date=jours[0], equipements={equipement["id"]: equipement})
        options = [ChatOption(label=f"Réserver {equipement['nom']}", value=f"dispo_{equipement['id']}")]

    return ChatResponse(reponse=f"Disponibilités de {equipement['nom']} ({equipement['laboratoire_nom']}) :\n" + "\n".join(lignes),
                        options=options)


async def _lister_equipements_disponibles(ctx: Contexte, equipements: list[dict], date_iso: str) -> ChatResponse:
    if date_iso < date.today().isoformat():
        return ChatResponse(reponse=f"Le {formater_date(date_iso)} est déjà passé. Pour quelle date souhaitez-vous voir les disponibilités ?")

    # Un équipement en panne, en maintenance ou hors service n'est jamais
    # proposé, quel que soit son planning.
    reservables = [e for e in equipements if e.get("statut") not in STATUTS_EQUIPEMENT_NON_RESERVABLES]
    limite = asyncio.Semaphore(APPELS_DJANGO_SIMULTANES)

    async def creneaux_libres(e: dict) -> list[tuple[str, str]]:
        async with limite:
            occupes = await get_creneaux_occupes(ctx.token, e["id"], date_iso, date_iso)
        return calculer_creneaux_libres(occupes, [date_iso])[date_iso]

    tous_les_creneaux = await asyncio.gather(*(creneaux_libres(e) for e in reservables))
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
    return ChatResponse(reponse="\n".join(lignes), options=options)


async def traiter_selection_disponibilite(ctx: Contexte) -> ChatResponse | None:
    etape = ctx.contexte_etape
    identifiant = lire_choix(ctx.message_normalise, "dispo")
    equipement = etape["equipements"].get(identifiant) if identifiant is not None else None
    if equipement is None:
        return None
    return await reserver_equipement_choisi(ctx, equipement, etape["date"])
