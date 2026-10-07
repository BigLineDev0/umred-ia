"""Réponses « en lecture seule » : aucune n'ouvre d'étape de dialogue."""
from collections import Counter

from rapidfuzz import fuzz

from app.assistant.contexte import Contexte
from app.assistant.formatage import formater_date, formater_heure, formater_plage, resume_reservation
from app.assistant.navigation import action_page, actions_pages
from app.core import temps
from app.core.constantes import (
    LABELS_ROLE, LABELS_STATUT_RESERVATION, ROLES_VUE_GLOBALE, STATUT_MAINTENANCE_PLANIFIEE,
    STATUTS_EQUIPEMENT_NON_RESERVABLES, Role,
)
from app.core.security import Utilisateur
from app.nlu.conversation import MESSAGE_AIDE, repondre_conversationnel
from app.nlu.domaine import concerne_le_labo
from app.nlu.texte import contient_un, normaliser_texte
from app.schemas.chat import ChatAction, ChatResponse
from app.services.disponibilite import bornes_semaine
from app.services.django_client import get_equipements, get_maintenances, get_reservations
from app.services.matching import resoudre_equipement
from app.services.reservations import apercu_du_jour, reservations_actives_a_venir

NOMBRE_MAX_RESERVATIONS_AFFICHEES = 5
MOTS_PORTEE_GLOBALE = ("tous les utilisateurs", "au total", "toute la plateforme", "de la plateforme", "global")


# --- Identité et politesse ---

async def saluer(ctx: Contexte) -> ChatResponse:
    prenom = f" {ctx.user.prenom}" if ctx.user.prenom else ""
    return ChatResponse(reponse=f"Bonjour{prenom} 👋 Comment puis-je vous aider aujourd'hui ?")


async def donner_nom(ctx: Contexte) -> ChatResponse:
    if not ctx.user.prenom:
        return ChatResponse(reponse="Je ne parviens pas à retrouver votre identité pour le moment.")
    role = LABELS_ROLE.get(ctx.user.role, ctx.user.role or "utilisateur")
    return ChatResponse(reponse=f"Vous êtes {ctx.user.prenom} {ctx.user.nom or ''}".rstrip() + f", connecté en tant que {role}.")


async def presenter_assistant(ctx: Contexte) -> ChatResponse:
    return ChatResponse(reponse=(
        "Je suis l'assistant virtuel de UMRED. Je peux vous aider à réserver des équipements, "
        "consulter vos réservations et les disponibilités, suivre les maintenances et vous orienter vers "
        "la bonne page de la plateforme selon votre rôle. "
        "J'ai été développé par Aliou Diallo dans le cadre de son projet de certification à Simplon Sénégal."
    ))


async def repondre_autre(ctx: Contexte) -> ChatResponse:
    # Le message parle du labo sans être une commande reconnue : on montre
    # l'aide plutôt que de laisser le modèle improviser une procédure.
    if concerne_le_labo(ctx.message):
        return ChatResponse(reponse=f"Je ne suis pas certain de bien comprendre votre demande. {MESSAGE_AIDE}",
                            actions=actions_pages(ctx.user.role, "reservation_formulaire", "mes_reservations"))
    return ChatResponse(reponse=await repondre_conversationnel(ctx.message, ctx.session["historique"]))


# --- Réservations ---

def donnees_reservation(r: dict) -> dict:
    """Champs d'une réservation Django utiles à l'affichage en carte (rien d'autre n'est exposé)."""
    return {"id": r["id"], "date": r["date"], "heure_debut": r["heure_debut"][:5], "heure_fin": r["heure_fin"][:5],
            "statut": r["statut"], "statut_libelle": LABELS_STATUT_RESERVATION.get(r["statut"], r["statut"]),
            "laboratoire": r.get("laboratoire_nom"), "equipements": r.get("equipements_noms") or []}


async def lister_mes_reservations(ctx: Contexte) -> ChatResponse:
    ext = ctx.extraction
    # Période demandée (« cette semaine », « demain ») : le filtre est
    # appliqué par Django ; sinon, les prochaines réservations actives.
    if ext and (ext.date or ext.periode in ("semaine", "semaine_prochaine")):
        debut, fin = (ext.date, ext.date) if ext.date else bornes_semaine(ext.periode)
        reservations = sorted(await get_reservations(ctx.token, date_debut=debut, date_fin=fin),
                              key=lambda r: (r["date"], r["heure_debut"]))
        periode = formater_date(debut) if debut == fin else ("la semaine prochaine" if ext.periode == "semaine_prochaine" else "cette semaine")
        introduction, aucune = f"Voici vos réservations pour {periode} :", f"Vous n'avez aucune réservation pour {periode}."
    else:
        reservations = reservations_actives_a_venir(await get_reservations(ctx.token, a_venir=True))
        introduction, aucune = "Voici vos prochaines réservations :", "Vous n'avez aucune réservation à venir."

    actions = actions_pages(ctx.user.role, "mes_reservations")
    if not reservations:
        return ChatResponse(type="reservations", reponse=aucune, data=[],
                            actions=actions_pages(ctx.user.role, "reservation_formulaire", "mes_reservations"))
    affichees = reservations[:NOMBRE_MAX_RESERVATIONS_AFFICHEES]
    lignes = [introduction] + [f"- {resume_reservation(r)}" for r in affichees]
    if len(reservations) > len(affichees):
        lignes.append(f"… et {len(reservations) - len(affichees)} autre(s), visibles dans « Mes réservations ».")
    return ChatResponse(type="reservations", reponse="\n".join(lignes), actions=actions,
                        data=[donnees_reservation(r) for r in affichees])


# --- Explication d'un refus ---

async def expliquer_refus(ctx: Contexte) -> ChatResponse:
    """
    « Pourquoi ma réservation a été refusée ? » : la réponse est le motif
    enregistré dans Django par la personne (ou la règle) qui a statué,
    jamais une explication imaginée.
    """
    ext = ctx.extraction
    refusees = await get_reservations(ctx.token, statut="REFUSEE")
    if ext and ext.date:
        refusees = [r for r in refusees if r["date"] == ext.date]
    if ext and ext.equipement:
        cible = normaliser_texte(ext.equipement)
        refusees = [r for r in refusees if any(fuzz.partial_ratio(cible, normaliser_texte(n)) >= 85
                                                for n in r.get("equipements_noms") or [])] or refusees
    actions = actions_pages(ctx.user.role, "mes_reservations")
    if not refusees:
        return ChatResponse(type="reservations", data=[], actions=actions,
                            reponse="Je ne trouve aucune réservation refusée vous concernant"
                                    + (f" pour {formater_date(ext.date)}." if ext and ext.date else "."))

    # La plus récemment traitée en premier.
    refusee = max(refusees, key=lambda r: (r.get("date_validation") or "", r["date"]))
    quoi = ", ".join(refusee.get("equipements_noms") or []) or refusee.get("laboratoire_nom", "")
    quand = f"{formater_date(refusee['date'])} {formater_plage(refusee['heure_debut'], refusee['heure_fin'])}"
    if refusee.get("motif_refus"):
        par = f" par {refusee['validateur_nom']}" if refusee.get("validateur_nom") else ""
        explication = f"Votre réservation {quoi} du {quand} a été refusée{par} pour le motif suivant : « {refusee['motif_refus'].strip().rstrip('.')} »"
    else:
        explication = (f"Votre réservation {quoi} du {quand} a été refusée, mais aucun motif n'a été renseigné. "
                       "Vous pouvez contacter le responsable du laboratoire")
    if len(refusees) > 1:
        explication += f". Vous avez {len(refusees)} réservations refusées au total"
    return ChatResponse(type="reservations", reponse=explication + ".", actions=actions,
                        data=[{**donnees_reservation(refusee), "motif_refus": refusee.get("motif_refus") or None}])


# --- Maintenance ---

async def prochaine_maintenance(ctx: Contexte) -> ChatResponse:
    equipements = await get_equipements(ctx.token)
    equipement = resoudre_equipement(ctx.extraction.equipement or ctx.message, equipements)
    if not equipement:
        return ChatResponse(type="clarification",
                            reponse="Sur quel équipement souhaitez-vous connaître la prochaine maintenance ? "
                                    "Par exemple : « prochaine maintenance du microscope ».",
                            actions=actions_pages(ctx.user.role, "maintenances"))

    maintenances = await get_maintenances(ctx.token, equipement_id=equipement["id"], statut=STATUT_MAINTENANCE_PLANIFIEE)
    aujourd_hui = temps.aujourd_hui().isoformat()
    a_venir = sorted((m for m in maintenances if m["date_planifiee"][:10] >= aujourd_hui), key=lambda m: m["date_planifiee"])

    prefixe = ""
    if equipement.get("statut") in STATUTS_EQUIPEMENT_NON_RESERVABLES:
        prefixe = f"{equipement['nom']} est actuellement indisponible (panne, maintenance ou hors service). "
    # Lien vers la fiche de l'équipement (identifiant entier issu de Django).
    actions = [ChatAction(label=f"Voir {equipement['nom']}", route=f"/equipements/{int(equipement['id'])}")]
    if (page := action_page("maintenances", ctx.user.role)):
        actions.append(page)
    donnees = {"equipement": equipement["nom"], "statut_equipement": equipement.get("statut"),
               "prochaine_maintenance": a_venir[0]["date_planifiee"] if a_venir else None}
    if not a_venir:
        return ChatResponse(type="maintenance", actions=actions, data=donnees,
                            reponse=f"{prefixe}Aucune maintenance n'est actuellement planifiée pour {equipement['nom']}.")
    return ChatResponse(type="maintenance", actions=actions, data=donnees,
                        reponse=f"{prefixe}La prochaine maintenance de {equipement['nom']} est planifiée "
                                f"{formater_date(a_venir[0]['date_planifiee'])}.")


# --- Statistiques ---

async def statistiques(ctx: Contexte) -> ChatResponse:
    message = ctx.message_normalise

    if "maintenance" in message:
        maintenances = await get_maintenances(ctx.token)
        # Django ne renvoie la liste complète qu'aux administrateurs : pour
        # les autres rôles, le chiffre porte sur ce qui leur est visible.
        portee = "au total" if ctx.user.role == Role.ADMIN else "vous concernant ou en attente de prise en charge"
        return ChatResponse(type="statistics", reponse=f"{len(maintenances)} intervention(s) de maintenance {portee}.",
                            data={"maintenances": len(maintenances)}, actions=actions_pages(ctx.user.role, "maintenances"))

    if "equipement" in message:
        equipements = await get_equipements(ctx.token)
        reservables = sum(1 for e in equipements if e.get("statut") not in STATUTS_EQUIPEMENT_NON_RESERVABLES)
        return ChatResponse(type="statistics", reponse=f"{len(equipements)} équipement(s) référencé(s) sur la plateforme, "
                                                      f"dont {reservables} actuellement réservable(s).",
                            data={"equipements": len(equipements), "reservables": reservables},
                            actions=actions_pages(ctx.user.role, "equipements"))

    demande_globale = contient_un(message, MOTS_PORTEE_GLOBALE)
    # Django n'ouvre la liste complète (?all=true) qu'aux administrateurs :
    # la demander pour un autre rôle provoquerait un refus 403.
    globale_autorisee = demande_globale and ctx.user.role in ROLES_VUE_GLOBALE
    reservations = await get_reservations(ctx.token, tous=globale_autorisee)
    mois_courant = temps.aujourd_hui().strftime("%Y-%m")
    du_mois = [r for r in reservations if r["date"].startswith(mois_courant)]
    par_statut = Counter(r["statut"] for r in du_mois)

    portee = "sur la plateforme" if globale_autorisee else "pour vous"
    reponse = f"{len(du_mois)} réservation(s) ce mois-ci {portee}"
    if du_mois:
        reponse += f" (dont {par_statut.get('VALIDEE', 0)} validée(s) et {par_statut.get('EN_ATTENTE', 0)} en attente)"
    reponse += "."
    if demande_globale and not globale_autorisee:
        reponse += " Les statistiques globales sont réservées aux administrateurs."
    return ChatResponse(type="statistics", reponse=reponse,
                        data={"reservations_du_mois": len(du_mois), "validees": par_statut.get("VALIDEE", 0),
                              "en_attente": par_statut.get("EN_ATTENTE", 0), "portee": "plateforme" if globale_autorisee else "personnelle"},
                        actions=actions_pages(ctx.user.role, "rapports", "pilotage"))


# --- Message d'accueil ---

async def message_accueil(user: Utilisateur) -> ChatResponse:
    bonjour = f"Bonjour {user.prenom} 👋" if user.prenom else "Bonjour 👋"
    apercu = apercu_du_jour(await get_reservations(user.token, a_venir=True))
    total, prochaine = apercu["total_du_jour"], apercu["prochaine"]

    if total == 0:
        return ChatResponse(reponse=f"{bonjour} Vous n'avez aucune réservation prévue aujourd'hui. Comment puis-je vous aider ?")
    texte = f"{bonjour} Vous avez {total} réservation(s) aujourd'hui"
    if prochaine:
        quoi = (prochaine.get("equipements_noms") or ["salle"])[0]
        texte += f", la prochaine à {formater_heure(prochaine['heure_debut'])} ({quoi}, {prochaine['laboratoire_nom']})"
    return ChatResponse(reponse=f"{texte}. Comment puis-je vous aider ?")
