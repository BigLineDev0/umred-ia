"""
Parcours de réservation, étape par étape :

    demande -> COLLECTE (infos manquantes ? créneaux proposés) -> SELECTION_EQUIPEMENT (ambiguïté ?)
            -> vérification Django (/reservations/verifier/) ─> CONFIRMATION ─> envoi à Django -> succès
                         └> conflit ─> SELECTION_ALTERNATIVE ──> CONFIRMATION

Le « brouillon » est la demande en cours de construction : il survit
d'un message à l'autre, ce qui permet à l'utilisateur de compléter sa
demande (« Réserve le microscope » puis « demain » puis « le matin »).

L'assistant ne décide jamais qu'un créneau est libre : il propose des
créneaux à partir du planning renvoyé par Django, puis c'est Django qui
vérifie la demande (conflits, règles de l'établissement, statut) avant
la confirmation, et la revérifie à la création.
"""
import asyncio
import re
from datetime import datetime
from typing import Any

from app.assistant.contexte import Contexte, est_non, est_oui, lire_choix, lire_numero, options_confirmation
from app.assistant.formatage import (
    formater_creneau, formater_date, formater_date_titre, formater_heure, formater_liste, formater_plage,
)
from app.assistant.navigation import actions_pages
from app.core import temps
from app.core.constantes import STATUTS_EQUIPEMENT_NON_RESERVABLES
from app.core.session_store import Etape, definir_etape, memoriser, terminer_etape
from app.nlu.intentions import Intention
from app.schemas.chat import ChatOption, ChatResponse, DetailsConfirmation
from app.services.disponibilite import (
    LIBELLES_MOMENTS, calculer_creneaux_libres, decouper_en_creneaux, enveloppe, est_disponible,
    fenetre_moment, jours_de_la_periode, restreindre,
)
from app.services.django_client import (
    creer_reservation, get_alerte_usure, get_creneaux_occupes, get_equipements, get_horaires, get_reservations,
    verifier_reservation,
)
from app.services.matching import equipements_mentionnes

MOTIF_PAR_DEFAUT = "Réservation via l'assistant SenLab"
# Pendant la collecte, un message reconnu comme l'une de ces intentions
# complète la demande en cours ; toute autre intention l'abandonne.
INTENTIONS_COMPATIBLES_COLLECTE = {Intention.RESERVER, Intention.AUTRE, Intention.CONSULTER_DISPONIBILITE}
DUREE_CRENEAU_PROPOSE = 120  # minutes

# « Le même équipement que la dernière fois », « comme d'habitude ».
_MEME_QUE_LA_DERNIERE_FOIS = re.compile(r"meme (?:equipement|appareil|machine|chose)|(?:que|comme) la derniere fois|comme d'habitude|habituel")
# « Réserve-le », « je veux le réserver » : l'équipement dont on vient de parler.
_PRONOM_EQUIPEMENT = re.compile(r"\breserve[rz]?-(?:le|la|les)\b|\b(?:le|la|les|l') ?reserver\b|\bcelui-ci\b|\bcelle-ci\b|\bcet equipement\b")


# ---------------------------------------------------------------------------
# 1. Démarrage et collecte des informations
# ---------------------------------------------------------------------------

def _brouillon_vide() -> dict[str, Any]:
    return {"equipement": None, "equipement_choisi": None, "date": None, "heure_debut": None, "heure_fin": None,
            "moment": None, "periode": None}


def _completer_brouillon(brouillon: dict[str, Any], ctx: Contexte) -> dict[str, Any]:
    """Les nouvelles informations remplacent les anciennes, les absentes ne les effacent pas."""
    ext = ctx.extraction
    nouveau = {**_brouillon_vide(), **brouillon}
    if ext.equipement:
        # L'utilisateur désigne un autre équipement : on oublie le précédent choix.
        nouveau.update(equipement=ext.equipement, equipement_choisi=None)
    for champ in ("date", "heure_debut", "heure_fin", "moment"):
        if getattr(ext, champ):
            nouveau[champ] = getattr(ext, champ)
    if ext.date:
        nouveau["periode"] = None
    elif ext.periode == "debut_semaine":
        nouveau["periode"] = ext.periode
    # On attendait l'heure de fin et l'utilisateur répond par une seule
    # heure (« 12h ») postérieure au début : c'est la réponse à la question.
    if (brouillon.get("heure_debut") and not brouillon.get("heure_fin") and ext.heure_debut
            and not ext.heure_fin and ext.heure_debut > brouillon["heure_debut"]):
        nouveau.update(heure_debut=brouillon["heure_debut"], heure_fin=ext.heure_debut)
    return nouveau


async def _equipement_de_la_derniere_fois(ctx: Contexte) -> dict | None:
    """
    Équipement de la réservation la plus récente de l'utilisateur, lu dans
    Django (jamais deviné) : en priorité une réservation passée, sinon la
    plus proche à venir.
    """
    reservations = [r for r in await get_reservations(ctx.token)
                    if r.get("equipements") and r.get("statut") in ("VALIDEE", "TERMINEE", "EN_ATTENTE")]
    if not reservations:
        return None
    aujourd_hui = temps.aujourd_hui().isoformat()
    passees = sorted((r for r in reservations if r["date"] <= aujourd_hui), key=lambda r: (r["date"], r["heure_debut"]))
    derniere = passees[-1] if passees else min(reservations, key=lambda r: (r["date"], r["heure_debut"]))
    equipements = {e["id"]: e for e in await get_equipements(ctx.token)}
    return equipements.get(derniere["equipements"][0])


async def demarrer_reservation(ctx: Contexte) -> ChatResponse:
    brouillon = _completer_brouillon(_brouillon_vide(), ctx)
    memoire = ctx.session.get("memoire", {})

    if _MEME_QUE_LA_DERNIERE_FOIS.search(ctx.message_normalise):
        equipement = await _equipement_de_la_derniere_fois(ctx)
        if equipement is None:
            return ChatResponse(type="clarification", intention=Intention.RESERVER,
                                reponse="Je ne retrouve aucune réservation précédente avec un équipement. "
                                        "Quel équipement souhaitez-vous réserver ?",
                                actions=actions_pages(ctx.user.role, "equipements"))
        brouillon.update(equipement=equipement["nom"], equipement_choisi=equipement)
    elif _PRONOM_EQUIPEMENT.search(ctx.message_normalise) and memoire.get("equipement"):
        # « Réserve-le » juste après « le PCR est-il libre demain ? ».
        brouillon.update(equipement=memoire["equipement"]["nom"], equipement_choisi=memoire["equipement"])
        if not brouillon["date"] and memoire.get("date"):
            brouillon["date"] = memoire["date"]
    return await avancer_reservation(ctx, brouillon)


async def poursuivre_collecte(ctx: Contexte) -> ChatResponse | None:
    """
    L'utilisateur répond à une question du type « pour quelle date ? ».
    Renvoie None si le message n'a rien à voir avec la demande en cours :
    le dialogue l'abandonne alors et traite le message normalement.
    """
    brouillon = {**_brouillon_vide(), **ctx.contexte_etape["brouillon"]}
    if est_non(ctx.message_normalise):
        terminer_etape(ctx.session)
        return ChatResponse(reponse="D'accord, j'abandonne cette demande de réservation.")
    if ctx.extraction.intention not in INTENTIONS_COMPATIBLES_COLLECTE:
        return None

    nouveau = _completer_brouillon(brouillon, ctx)
    if nouveau == brouillon:
        # Rien de neuf dans l'extraction : le message est peut-être juste
        # un nom d'équipement (« le microscope ») que seule la recherche
        # floue sur la liste des équipements peut reconnaître.
        if brouillon["equipement"] or not equipements_mentionnes(ctx.message, await get_equipements(ctx.token)):
            return None
        nouveau["equipement"] = ctx.message
    return await avancer_reservation(ctx, nouveau)


async def traiter_choix_creneau(ctx: Contexte) -> ChatResponse | None:
    """
    Choix d'un des créneaux proposés pendant la collecte : clic sur le
    bouton (« creneau_0 ») ou réponse « le premier », « 2 »... Renvoie None
    si le message n'est pas un tel choix (il sera lu comme un complément).
    """
    creneaux = ctx.contexte_etape.get("creneaux") or []
    if not creneaux:
        return None
    index = lire_choix(ctx.message_normalise, "creneau")
    if index is None:
        index = lire_numero(ctx.message_normalise, len(creneaux))
    if index is None or index >= len(creneaux):
        return None
    debut, fin = creneaux[index]
    brouillon = {**_brouillon_vide(), **ctx.contexte_etape["brouillon"], "heure_debut": debut, "heure_fin": fin}
    return await avancer_reservation(ctx, brouillon)


def _verifier_coherence(brouillon: dict[str, Any], maintenant: datetime) -> list[str]:
    """Retire les valeurs incohérentes du brouillon et explique pourquoi."""
    remarques = []
    aujourd_hui = maintenant.date().isoformat()
    if brouillon["date"] and brouillon["date"] < aujourd_hui:
        remarques.append(f"La date du {formater_date(brouillon['date'])} est déjà passée.")
        brouillon["date"] = None
    if brouillon["heure_debut"] and brouillon["heure_fin"] and brouillon["heure_debut"] >= brouillon["heure_fin"]:
        remarques.append("L'heure de fin doit être postérieure à l'heure de début.")
        brouillon["heure_fin"] = None
    if brouillon["date"] == aujourd_hui and brouillon["heure_debut"] and brouillon["heure_debut"] < maintenant.strftime("%H:%M"):
        remarques.append("Cet horaire est déjà passé aujourd'hui.")
        brouillon["heure_debut"] = brouillon["heure_fin"] = None
    return remarques


async def horaires_etablissement(ctx: Contexte) -> dict:
    """
    {"ouverture", "fermeture", "horaires_jour"} : l'enveloppe globale (pour
    restreindre un moment de la journée) et la carte par jour de la semaine
    (jour fermé / plage propre à chaque jour), transmise à
    calculer_creneaux_libres. Vide si les horaires sont indisponibles.
    """
    horaires = await get_horaires(ctx.token)
    if not horaires:
        return {}
    ouverture, fermeture, par_jour = horaires
    return {"ouverture": ouverture, "fermeture": fermeture, "horaires_jour": par_jour}


async def avancer_reservation(ctx: Contexte, brouillon: dict[str, Any]) -> ChatResponse:
    """
    Fait progresser la demande aussi loin que possible : vérifie la
    cohérence, identifie l'équipement, demande ce qui manque (une question
    à la fois), puis passe à la sélection (si ambiguïté) ou à la confirmation.
    """
    remarques = _verifier_coherence(brouillon, temps.maintenant())

    # --- Identification de l'équipement ---
    par_famille: dict[str, list[dict]] = {}
    if brouillon["equipement_choisi"]:
        par_famille = {"choix": [brouillon["equipement_choisi"]]}
    else:
        equipements = await get_equipements(ctx.token)
        texte = brouillon["equipement"] or ctx.message
        par_famille = equipements_mentionnes(texte, equipements)
        if not par_famille and brouillon["equipement"] and texte != ctx.message:
            # Le nom isolé par les règles peut être trop court : on retente sur le message entier.
            par_famille = equipements_mentionnes(ctx.message, equipements)
            texte = ctx.message if par_famille else texte
        if par_famille:
            brouillon["equipement"] = texte
        elif brouillon["equipement"]:
            remarques.append(f"Je n'ai trouvé aucun équipement correspondant à « {brouillon['equipement']} ».")
            brouillon["equipement"] = None

    # Un seul équipement réservable identifié : on peut lui proposer des créneaux.
    unique = None
    if len(par_famille) == 1:
        candidats = [e for e in next(iter(par_famille.values())) if e.get("statut") not in STATUTS_EQUIPEMENT_NON_RESERVABLES]
        unique = candidats[0] if len(candidats) == 1 else None

    # « Toute la journée » : de l'ouverture à la fermeture de l'établissement.
    if brouillon["moment"] == "journee" and brouillon["date"] and not brouillon["heure_debut"]:
        horaires = await horaires_etablissement(ctx)
        brouillon["heure_debut"], brouillon["heure_fin"] = fenetre_moment("journee", **enveloppe(horaires))

    # --- Informations manquantes : une question à la fois ---
    if not par_famille:
        return _demander(ctx, brouillon, remarques, "Quel équipement souhaitez-vous réserver ? "
                                                   "Par exemple : « le microscope demain de 10h à 12h ».",
                         actions=actions_pages(ctx.user.role, "equipements"))
    nom = unique["nom"] if unique else formater_liste([c[0]["nom"] if len(c) == 1 else f for f, c in par_famille.items()])
    if not brouillon["date"]:
        if brouillon["periode"] == "debut_semaine":
            jours = formater_liste([formater_date(j) for j in jours_de_la_periode("debut_semaine")], "ou")
            question = f"Quel jour souhaitez-vous réserver {nom} : {jours} ?"
        else:
            question = f"Pour quelle date souhaitez-vous réserver {nom} ? Vous pouvez aussi préciser l'horaire, par exemple « demain de 10h à 12h »."
        return _demander(ctx, brouillon, remarques, question)
    if brouillon["heure_debut"] and not brouillon["heure_fin"]:
        return _demander(ctx, brouillon, remarques, f"Jusqu'à quelle heure souhaitez-vous réserver {nom} à partir de "
                                                   f"{formater_heure(brouillon['heure_debut'])} ? Il me manque l'heure de fin.")
    if not brouillon["heure_debut"]:
        return await _proposer_creneaux(ctx, brouillon, remarques, nom, unique)

    # --- Équipements indisponibles (panne, maintenance, hors service) ---
    resolus: list[dict] = []
    ambigues: list[list] = []
    for fam, candidats in par_famille.items():
        reservables = [e for e in candidats if e.get("statut") not in STATUTS_EQUIPEMENT_NON_RESERVABLES]
        if not reservables:
            terminer_etape(ctx.session)
            noms = formater_liste([e["nom"] for e in candidats])
            return ChatResponse(reponse=f"{noms} n'est actuellement pas réservable (panne, maintenance ou hors service). "
                                        "Demandez-moi les équipements disponibles pour trouver une alternative.")
        if len(reservables) == 1:
            resolus.append(reservables[0])
        else:
            ambigues.append([fam, reservables])

    creneau = {c: brouillon[c] for c in ("date", "heure_debut", "heure_fin")}
    if ambigues:
        return await _demander_selection_equipement(ctx, creneau, resolus, ambigues)
    return await proposer_confirmation(ctx, creneau, resolus)


def _demander(ctx: Contexte, brouillon: dict, remarques: list[str], question: str, **extra: Any) -> ChatResponse:
    definir_etape(ctx.session, Etape.COLLECTE_RESERVATION, brouillon=brouillon)
    return ChatResponse(type="clarification", reponse=" ".join([*remarques, question]), intention=Intention.RESERVER, **extra)


async def _proposer_creneaux(ctx: Contexte, brouillon: dict, remarques: list[str], nom: str, equipement: dict | None) -> ChatResponse:
    """
    Date connue, horaire inconnu : on propose des créneaux libres calculés
    à partir des réservations renvoyées par Django (« le matin » limite la
    recherche). Le créneau choisi sera de toute façon revérifié par Django.
    """
    exemple = "l'heure de début et l'heure de fin, par exemple « de 10h à 12h »."
    if equipement is None:
        # Plusieurs équipements possibles : impossible de proposer un planning commun.
        return _demander(ctx, brouillon, remarques, f"À quelle heure souhaitez-vous réserver {nom} "
                                                   f"{formater_date(brouillon['date'])} ? Indiquez {exemple}")

    date_iso = brouillon["date"]
    horaires = await horaires_etablissement(ctx)
    occupes = await get_creneaux_occupes(ctx.token, equipement["id"], date_iso, date_iso)
    libres = calculer_creneaux_libres(occupes, [date_iso], temps.maintenant(), **horaires)[date_iso]
    libres = restreindre(libres, fenetre_moment(brouillon["moment"], **enveloppe(horaires)))
    creneaux = decouper_en_creneaux(libres, DUREE_CRENEAU_PROPOSE)
    quand = f"{formater_date(date_iso)}" + (f" {LIBELLES_MOMENTS[brouillon['moment']]}" if brouillon["moment"] else "")

    definir_etape(ctx.session, Etape.COLLECTE_RESERVATION, brouillon=brouillon, creneaux=creneaux)
    if not creneaux:
        return ChatResponse(type="clarification", intention=Intention.RESERVER,
                            reponse=" ".join([*remarques, f"{nom} n'a plus de créneau libre {quand}. "
                                              "Souhaitez-vous une autre date ou un autre moment de la journée ?"]),
                            data={"equipement": nom, "date": date_iso, "creneaux": []})

    # Les créneaux sont dans les boutons : la phrase ne les énumère pas.
    question = (f"Choisissez un créneau pour {nom} {quand}, ou indiquez l'heure de début et de fin "
                f"(ex. « de 10h à 12h »).")
    return ChatResponse(
        type="clarification", intention=Intention.RESERVER,
        reponse=" ".join([*remarques, question]),
        options=[ChatOption(label=formater_creneau(d, f), value=f"creneau_{i}") for i, (d, f) in enumerate(creneaux)],
        data={"equipement": nom, "date": date_iso, "creneaux": [{"debut": d, "fin": f} for d, f in creneaux]},
    )


# ---------------------------------------------------------------------------
# 2. Plusieurs équipements correspondent : l'utilisateur choisit
# ---------------------------------------------------------------------------

async def _demander_selection_equipement(ctx: Contexte, creneau: dict, resolus: list[dict], ambigues: list[list]) -> ChatResponse:
    famille, candidats = ambigues[0]
    # Les vérifications de disponibilité sont lancées en parallèle
    # (asyncio.gather) plutôt qu'une par une.
    libres = await asyncio.gather(*(
        est_disponible(ctx.token, c["id"], creneau["date"], creneau["heure_debut"], creneau["heure_fin"]) for c in candidats
    ))
    # Le laboratoire et la disponibilité vont en sous-titre des boutons :
    # le texte reste une seule question, sans répéter la liste.
    options = [
        ChatOption(label=c["nom"], value=f"equip_{c['id']}",
                   description=f"{c['laboratoire_nom']} · {'disponible' if libre else 'déjà réservé sur ce créneau'}")
        for c, libre in zip(candidats, libres)
    ]

    definir_etape(ctx.session, Etape.SELECTION_EQUIPEMENT, creneau=creneau, resolus=resolus, ambigues=ambigues)
    return ChatResponse(
        reponse=f"Plusieurs équipements correspondent à « {famille} ». Lequel souhaitez-vous réserver ?",
        options=options,
    )


async def traiter_selection_equipement(ctx: Contexte) -> ChatResponse | None:
    etape = ctx.contexte_etape
    candidats = etape["ambigues"][0][1]
    choisi = None
    identifiant = lire_choix(ctx.message_normalise, "equip")
    if identifiant is not None:
        # On n'accepte QUE les identifiants proposés : impossible d'injecter
        # un équipement arbitraire en forgeant la valeur du bouton.
        choisi = next((c for c in candidats if c["id"] == identifiant), None)
    else:
        index = lire_numero(ctx.message_normalise, len(candidats))
        choisi = candidats[index] if index is not None else None
    if choisi is None:
        return None

    resolus = [*etape["resolus"], choisi]
    restantes = etape["ambigues"][1:]
    if restantes:  # « le microscope et la balance » : une question par famille ambiguë
        return await _demander_selection_equipement(ctx, etape["creneau"], resolus, restantes)
    return await proposer_confirmation(ctx, etape["creneau"], resolus)


# ---------------------------------------------------------------------------
# 3. Confirmation puis envoi à Django
# ---------------------------------------------------------------------------

async def proposer_confirmation(ctx: Contexte, creneau: dict, equipements: list[dict]) -> ChatResponse:
    # Règle Django : tous les équipements d'une réservation appartiennent
    # au même laboratoire. On le vérifie ici pour donner un message clair.
    laboratoires = {e["laboratoire"] for e in equipements}
    if len(laboratoires) > 1:
        terminer_etape(ctx.session)
        detail = formater_liste([f"{e['nom']} ({e['laboratoire_nom']})" for e in equipements])
        return ChatResponse(reponse=f"Ces équipements sont dans des laboratoires différents : {detail}. "
                                    "Merci de faire une réservation par laboratoire.")

    reservation = {
        "laboratoire": equipements[0]["laboratoire"],
        "equipements": [e["id"] for e in equipements],
        **creneau,
        "motif": MOTIF_PAR_DEFAUT,
    }
    # Vérification par Django AVANT de demander la confirmation : un conflit
    # ou une règle non respectée est annoncé tout de suite, avec les
    # alternatives calculées par Django, au lieu d'après le « oui ».
    verification = await verifier_reservation(ctx.token, reservation)
    if verification.ok and verification.data.get("disponible") is False:
        return proposer_alternatives(ctx, reservation, equipements, verification.data)
    if verification.status_code == 400:
        terminer_etape(ctx.session)
        return ChatResponse(type="error", reponse=f"Cette demande ne peut pas être acceptée : {verification.detail}")
    # Autre réponse (ancienne version de l'API sans /verifier/...) : on
    # continue, la création revérifiera tout de toute façon.
    information = []
    if verification.ok and verification.data.get("statut_prevu") == "EN_ATTENTE":
        raison = (verification.data.get("raison_statut") or "").strip()
        information.append(f"ℹ️ Demande soumise à validation{' : ' + raison[:1].lower() + raison[1:] if raison else '.'}")

    definir_etape(ctx.session, Etape.CONFIRMATION_RESERVATION, reservation=reservation, equipements=equipements)
    memoriser(ctx.session, equipement=equipements[0] if len(equipements) == 1 else None, date=creneau["date"])

    # Croisement avec l'algorithme d'alerte d'usure du tableau de bord
    # technicien : l'utilisateur est prévenu AVANT de confirmer, et non
    # après coup sur un tableau de bord qu'il ne consulte jamais.
    alertes = await asyncio.gather(*(get_alerte_usure(ctx.token, e["id"]) for e in equipements))
    lignes = [f"{'🔴' if a['niveau'] == 'critique' else '🟠'} {a['message']}" for a in alertes if a]

    return ChatResponse(
        type="confirmation",
        reponse="\n\n".join([*lignes, *information, "Confirmez-vous cette réservation ?"]),
        necessite_confirmation=True,
        options=options_confirmation(),
        details_confirmation=DetailsConfirmation(
            laboratoire=equipements[0].get("laboratoire_nom"),
            equipement=", ".join(e["nom"] for e in equipements),
            date=formater_date(creneau["date"]),
            heure_debut=creneau["heure_debut"][:5],
            heure_fin=creneau["heure_fin"][:5],
        ),
    )


async def traiter_confirmation(ctx: Contexte) -> ChatResponse | None:
    if est_oui(ctx.message_normalise):
        return await _finaliser_reservation(ctx)
    if est_non(ctx.message_normalise):
        terminer_etape(ctx.session)
        return ChatResponse(reponse="D'accord, je n'enregistre pas cette réservation.")
    return None


async def _finaliser_reservation(ctx: Contexte) -> ChatResponse:
    etape = ctx.contexte_etape
    reservation, equipements = etape["reservation"], etape["equipements"]
    resultat = await creer_reservation(ctx.token, reservation)

    if resultat.status_code == 409:
        return proposer_alternatives(ctx, reservation, equipements, resultat.data)

    terminer_etape(ctx.session)
    if not resultat.ok:
        return ChatResponse(type="error", reponse=f"Je n'ai pas pu créer la réservation : {resultat.detail}")

    quand = f"{formater_date(reservation['date'])} {formater_plage(reservation['heure_debut'], reservation['heure_fin'])}"
    # Données renvoyées par Django après création : c'est la preuve que
    # l'action a réellement eu lieu (identifiant et statut réels).
    reponse = ChatResponse(
        type="reservation",
        reponse=f"C'est confirmé ! Votre réservation pour {quand} est validée.",
        data={"id": resultat.data.get("id"), "statut": resultat.data.get("statut"), "date": reservation["date"],
              "heure_debut": reservation["heure_debut"], "heure_fin": reservation["heure_fin"],
              "equipements": [e["nom"] for e in equipements]},
        actions=actions_pages(ctx.user.role, "mes_reservations"),
    )
    # Le statut initial est décidé par Django (règles de gestion) : un
    # étudiant ou un équipement sensible passe par une validation humaine.
    if resultat.data.get("statut") == "EN_ATTENTE":
        reponse.reponse = (f"Votre demande pour {quand} est enregistrée. "
                           "Elle est en attente de validation par un responsable : vous serez notifié de sa décision.")
    return reponse


# ---------------------------------------------------------------------------
# 4. Conflit de planning : alternatives proposées par Django
# ---------------------------------------------------------------------------

def _phrase_conflit(reservation: dict, conflits: list[dict]) -> str:
    """« Thermocycleur PCR est déjà réservé demain (vendredi 9 octobre), de 9h à 11h. »"""
    jour = formater_date(reservation["date"])
    if len(conflits) == 1:
        c = conflits[0]
        return f"{c['equipement']} est déjà réservé {jour}, {formater_plage(c['heure_debut'], c['heure_fin'])}."
    occupes = formater_liste(sorted({c["equipement"] for c in conflits}))
    plage = formater_plage(reservation["heure_debut"], reservation["heure_fin"])
    return f"Ce créneau est déjà pris {jour}, {plage}" + (f" : {occupes} sont occupés." if conflits else ".")


def proposer_alternatives(
    ctx: Contexte, reservation: dict, equipements: list[dict], conflit: dict, introduction: str | None = None,
) -> ChatResponse:
    alternatives = conflit.get("alternatives") or {}
    # Créneaux libres pour TOUS les équipements demandés, classés par
    # proximité avec l'heure voulue ; chacun porte un message explicatif
    # (« disponible à partir de 11h00 »).
    memes = alternatives.get("creneaux", [])
    # Chaque équivalent indique l'équipement qu'il remplace ('remplace').
    equivalents = alternatives.get("equipements_equivalents", [])

    phrase = introduction or _phrase_conflit(reservation, conflit.get("conflits", []))

    # Les alternatives sont les boutons eux-mêmes (créneau en titre, jour
    # ou équipement remplacé en sous-titre) : le texte ne les répète pas.
    options = [
        ChatOption(label=formater_creneau(alt["heure_debut"], alt["heure_fin"]), value=f"alt_{i}",
                   description=formater_date_titre(alt["date"]))
        for i, alt in enumerate(memes)
    ] + [
        ChatOption(label=equiv["nom"], value=f"equiv_{equiv['id']}",
                   description=(f"À la place de {equiv['remplace_nom']}, même créneau"
                                if equiv.get("remplace_nom") else "Même créneau"))
        for equiv in equivalents
    ]

    if not options:
        terminer_etape(ctx.session)
        return ChatResponse(type="availability", data={"disponible": False, "alternatives": []},
                            reponse=f"{phrase} Aucune alternative n'est disponible dans les prochains jours : "
                                    "contactez un technicien.")

    definir_etape(ctx.session, Etape.SELECTION_ALTERNATIVE, reservation=reservation, equipements=equipements,
                  alternatives=memes, equivalents=equivalents)
    # Les alternatives sont celles calculées par Django : l'assistant se
    # contente de les présenter.
    return ChatResponse(type="availability", reponse=f"{phrase} Voici d'autres possibilités :", options=options,
                        data={"disponible": False, "alternatives": memes})


async def traiter_selection_alternative(ctx: Contexte) -> ChatResponse | None:
    etape = ctx.contexte_etape
    reservation, equipements = etape["reservation"], etape["equipements"]
    creneau = {c: reservation[c] for c in ("date", "heure_debut", "heure_fin")}

    index = lire_choix(ctx.message_normalise, "alt")
    if index is not None and index < len(etape["alternatives"]):
        alt = etape["alternatives"][index]
        creneau = {"date": alt["date"], "heure_debut": alt["heure_debut"], "heure_fin": alt["heure_fin"]}
        return await proposer_confirmation(ctx, creneau, equipements)

    identifiant = lire_choix(ctx.message_normalise, "equiv")
    equiv = next((e for e in etape["equivalents"] if e["id"] == identifiant), None) if identifiant is not None else None
    if equiv:
        # Django garantit que l'équivalent est dans le même laboratoire. Il
        # ne remplace que l'équipement en conflit ; le reste de la sélection
        # est conservé.
        remplacant = {**equiv, "laboratoire": reservation["laboratoire"], "laboratoire_nom": equipements[0].get("laboratoire_nom")}
        remplace = equiv.get("remplace", equipements[0]["id"])
        nouveaux = [remplacant if e["id"] == remplace else e for e in equipements]
        return await proposer_confirmation(ctx, creneau, nouveaux)
    return None


# ---------------------------------------------------------------------------
# Utilitaire partagé avec le parcours « disponibilités »
# ---------------------------------------------------------------------------

async def reserver_equipement_choisi(
    ctx: Contexte, equipement: dict, date_iso: str, heure_debut: str | None = None, heure_fin: str | None = None,
    moment: str | None = None,
) -> ChatResponse:
    """Démarre une réservation pour un équipement déjà identifié (clic dans la liste des disponibilités)."""
    brouillon = {**_brouillon_vide(), "equipement": equipement["nom"], "equipement_choisi": equipement, "date": date_iso,
                 "heure_debut": heure_debut, "heure_fin": heure_fin, "moment": moment}
    return await avancer_reservation(ctx, brouillon)
