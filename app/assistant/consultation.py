"""Réponses « en lecture seule » : aucune n'ouvre d'étape de dialogue."""
from collections import Counter
from datetime import date

from app.assistant.contexte import Contexte
from app.assistant.formatage import formater_date, formater_heure, resume_reservation
from app.core.constantes import (
    LABELS_ROLE, ROLES_GESTION_EQUIPEMENTS, ROLES_SUPERVISEURS, STATUT_MAINTENANCE_PLANIFIEE,
    STATUTS_EQUIPEMENT_NON_RESERVABLES, Role,
)
from app.core.security import Utilisateur
from app.nlu.conversation import MESSAGE_AIDE, repondre_conversationnel
from app.nlu.domaine import concerne_le_labo
from app.nlu.texte import contient_un
from app.schemas.chat import ChatResponse
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
        "consulter vos réservations et les disponibilités, et suivre les maintenances. "
        "J'ai été développé par Aliou Diallo dans le cadre de son projet de certification à Simplon Sénégal."
    ))


async def repondre_autre(ctx: Contexte) -> ChatResponse:
    # Le message parle du labo sans être une commande reconnue : on montre
    # l'aide plutôt que de laisser le modèle improviser une procédure.
    if concerne_le_labo(ctx.message):
        return ChatResponse(reponse=f"Je ne suis pas certain de bien comprendre votre demande. {MESSAGE_AIDE}")
    return ChatResponse(reponse=await repondre_conversationnel(ctx.message, ctx.session["historique"]))


async def expliquer_creation_equipement(ctx: Contexte) -> ChatResponse:
    if ctx.user.role not in ROLES_GESTION_EQUIPEMENTS:
        return ChatResponse(reponse="Seuls les administrateurs et les techniciens peuvent ajouter un équipement.")
    return ChatResponse(reponse="Rendez-vous sur la page « Équipements », puis cliquez sur « Nouvel équipement ».")


# --- Réservations ---

async def lister_mes_reservations(ctx: Contexte) -> ChatResponse:
    a_venir = reservations_actives_a_venir(await get_reservations(ctx.token, a_venir=True))
    if not a_venir:
        return ChatResponse(reponse="Vous n'avez aucune réservation à venir.")
    affichees = a_venir[:NOMBRE_MAX_RESERVATIONS_AFFICHEES]
    lignes = ["Voici vos prochaines réservations :"] + [f"- {resume_reservation(r)}" for r in affichees]
    if len(a_venir) > len(affichees):
        lignes.append(f"… et {len(a_venir) - len(affichees)} autre(s), visibles dans « Mes réservations ».")
    return ChatResponse(reponse="\n".join(lignes))


# --- Maintenance ---

async def prochaine_maintenance(ctx: Contexte) -> ChatResponse:
    equipements = await get_equipements(ctx.token)
    equipement = resoudre_equipement(ctx.extraction.equipement or ctx.message, equipements)
    if not equipement:
        return ChatResponse(reponse="Sur quel équipement souhaitez-vous connaître la prochaine maintenance ? "
                                    "Par exemple : « prochaine maintenance du microscope ».")

    maintenances = await get_maintenances(ctx.token, equipement_id=equipement["id"], statut=STATUT_MAINTENANCE_PLANIFIEE)
    aujourd_hui = date.today().isoformat()
    a_venir = sorted((m for m in maintenances if m["date_planifiee"][:10] >= aujourd_hui), key=lambda m: m["date_planifiee"])

    prefixe = ""
    if equipement.get("statut") in STATUTS_EQUIPEMENT_NON_RESERVABLES:
        prefixe = f"{equipement['nom']} est actuellement indisponible (panne, maintenance ou hors service). "
    if not a_venir:
        return ChatResponse(reponse=f"{prefixe}Aucune maintenance n'est actuellement planifiée pour {equipement['nom']}.")
    return ChatResponse(reponse=f"{prefixe}La prochaine maintenance de {equipement['nom']} est planifiée "
                                f"{formater_date(a_venir[0]['date_planifiee'])}.")


# --- Statistiques ---

async def statistiques(ctx: Contexte) -> ChatResponse:
    message = ctx.message_normalise

    if "maintenance" in message:
        maintenances = await get_maintenances(ctx.token)
        # Django ne renvoie la liste complète qu'aux administrateurs : pour
        # les autres rôles, le chiffre porte sur ce qui leur est visible.
        portee = "au total" if ctx.user.role == Role.ADMIN else "vous concernant ou en attente de prise en charge"
        return ChatResponse(reponse=f"{len(maintenances)} intervention(s) de maintenance {portee}.")

    if "equipement" in message:
        equipements = await get_equipements(ctx.token)
        reservables = sum(1 for e in equipements if e.get("statut") not in STATUTS_EQUIPEMENT_NON_RESERVABLES)
        return ChatResponse(reponse=f"{len(equipements)} équipement(s) référencé(s) sur la plateforme, "
                                    f"dont {reservables} actuellement réservable(s).")

    demande_globale = contient_un(message, MOTS_PORTEE_GLOBALE)
    globale_autorisee = demande_globale and ctx.user.role in ROLES_SUPERVISEURS
    reservations = await get_reservations(ctx.token, tous=globale_autorisee)
    mois_courant = date.today().strftime("%Y-%m")
    du_mois = [r for r in reservations if r["date"].startswith(mois_courant)]
    par_statut = Counter(r["statut"] for r in du_mois)

    portee = "sur la plateforme" if globale_autorisee else "pour vous"
    reponse = f"{len(du_mois)} réservation(s) ce mois-ci {portee}"
    if du_mois:
        reponse += f" (dont {par_statut.get('VALIDEE', 0)} validée(s) et {par_statut.get('EN_ATTENTE', 0)} en attente)"
    reponse += "."
    if demande_globale and not globale_autorisee:
        reponse += " Les statistiques globales sont réservées aux responsables de la plateforme."
    return ChatResponse(reponse=reponse)


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
