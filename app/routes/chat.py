from fastapi import APIRouter, Depends
from datetime import date

from app.schemas.chat import ChatRequest, ChatResponse
from app.core.security import get_bearer_token, get_current_user
from app.core.session_store import get_session, update_session
from app.nlu.extractor import extraire_intention
from app.services.django_client import (
    get_equipements, creer_reservation, get_mes_reservations,
    annuler_reservation, get_maintenances, get_creneaux_occupes,
    get_reservations_stats, get_toutes_maintenances
)
from app.services.matching import resoudre_equipement
from app.services.disponibilite import calculer_creneaux_libres, jours_de_la_periode

from app.nlu.conversation import repondre_conversationnel
from app.nlu.normalisation import normaliser_date, nettoyer_extraction
from app.nlu.domaine import concerne_le_labo

LABELS_ROLE = {"ADMIN": "administrateur", "TECHNICIEN": "technicien", "CHERCHEUR": "enseignant-chercheur", "ETUDIANT": "étudiant"}
ROLES_SUPERVISEURS = ["ADMIN", "TECHNICIEN", "CHERCHEUR"]

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)


async def chat(payload: ChatRequest, token: str = Depends(get_bearer_token), user=Depends(get_current_user)):
    session = get_session(payload.session_id)
    message_brut = payload.message.strip()
    message_lower = message_brut.lower()

    if session.get("attente_confirmation"):
        action = session.get("action_en_attente")

        if message_lower in ["oui", "yes", "confirme", "confirmer"]:
            update_session(payload.session_id, attente_confirmation=False)
            if action == "creer_reservation":
                resultat = await creer_reservation(token, session["reservation_prete"])
                if "erreur" in resultat:
                    return ChatResponse(reponse=f"Je n'ai pas pu créer la réservation : {resultat['erreur']}")
                return ChatResponse(reponse="C'est confirmé, votre réservation a bien été enregistrée.")
            if action == "annuler_reservation":
                resultat = await annuler_reservation(token, session["reservation_id_a_annuler"])
                if "erreur" in resultat:
                    return ChatResponse(reponse="Je n'ai pas pu annuler cette réservation.")
                return ChatResponse(reponse="Votre réservation a bien été annulée.")

        elif message_lower in ["non", "no", "annule", "annuler"]:
            update_session(payload.session_id, attente_confirmation=False)
            return ChatResponse(reponse="D'accord, j'annule cette demande.")

        else:
            # Le message ne répond ni "oui" ni "non" à la confirmation en
            # attente : on l'abandonne plutôt que de l'imposer comme un
            # refus implicite, et on traite ce message comme une VRAIE
            # nouvelle demande — pas de continuation, pas de return ici.
            update_session(payload.session_id, attente_confirmation=False)

    extraction = await extraire_intention(message_brut)
    extraction = nettoyer_extraction(extraction)
    extraction["date"] = normaliser_date(extraction.get("date"))
    intention = extraction.get("intention")

    # Verrou déterministe pour les statistiques : peu importe ce que
    # l'extraction du LLM a renvoyé, "combien"/"statistique" porte
    # toujours sur un comptage — jamais laissé retomber sur le menu.
    if intention not in ["reserver", "annuler"] and any(m in message_lower for m in ["combien", "statistique", "nombre de"]):
        intention = "statistiques"

    if intention == "salutation":
        prenom = user.get("prenom")
        return ChatResponse(reponse=f"Bonjour {prenom} 👋 Comment puis-je vous aider aujourd'hui ?" if prenom else "Bonjour 👋 Comment puis-je vous aider ?")

    if intention == "mon_nom":
        if user.get("prenom"):
            role = LABELS_ROLE.get(user["role"], user["role"])
            return ChatResponse(reponse=f"Vous êtes {user['prenom']} {user['nom']}, connecté en tant que {role}.")
        return ChatResponse(reponse="Je ne parviens pas à retrouver votre identité pour le moment.")

    if intention == "identite":
        return ChatResponse(reponse=(
            "Je suis l'assistant virtuel de UMRED Labo 🤖 — je peux vous aider à réserver des équipements, "
            "consulter vos réservations, suivre les maintenances et répondre à vos questions sur la plateforme. "
            "J'ai été développé par Aliou Diallo dans le cadre de son projet de certification à Simplon Sénégal."
        ))

    # --- Réservation (inchangé) ---
    if intention == "reserver":
        return await _gerer_reservation(payload, token, extraction)

    # --- Consultation des réservations ---
    if intention == "consulter_mes_reservations":
        reservations = await get_mes_reservations(token)
        a_venir = [r for r in reservations if r["date"] >= date.today().isoformat()][:5]
        if not a_venir:
            return ChatResponse(reponse="Vous n'avez aucune réservation à venir.")
        lignes = [f"- {r['date']} de {r['heure_debut'][:5]} à {r['heure_fin'][:5]} ({r['laboratoire_nom']}, statut : {r['statut']})" for r in a_venir]
        return ChatResponse(reponse="Voici vos prochaines réservations :\n" + "\n".join(lignes))

    # --- Annulation ---
    if intention == "annuler":
        return await _gerer_annulation(payload, token, extraction)

    # --- Maintenance ---
    if intention == "maintenance":
        equipements = await get_equipements(token)
        equipement = resoudre_equipement(payload.message, equipements)
        if not equipement:
            return ChatResponse(reponse="Sur quel équipement souhaitez-vous connaître la prochaine maintenance ?")
        maintenances = await get_maintenances(token, equipement["id"])
        if not maintenances:
            return ChatResponse(reponse=f"Aucune maintenance n'est actuellement planifiée pour {equipement['nom']}.")
        prochaine = sorted(maintenances, key=lambda m: m["date_planifiee"])[0]
        return ChatResponse(reponse=f"La prochaine maintenance de {equipement['nom']} est planifiée le {prochaine['date_planifiee'][:10]}.")

    # --- Statistiques ---
    if intention == "statistiques":
        # retire cette ligne : message_lower = payload.message.lower()
        demande_globale = user["role"] in ROLES_SUPERVISEURS and any(
            m in message_lower for m in ["tous les utilisateurs", "au total", "toute la plateforme", "de la plateforme"]
        )

        if "maintenance" in message_lower:
            maintenances = await get_toutes_maintenances(token)
            return ChatResponse(reponse=f"{len(maintenances)} intervention(s) de maintenance enregistrée(s) au total.")

        if "équipement" in message_lower or "equipement" in message_lower:
            equipements = await get_equipements(token)
            return ChatResponse(reponse=f"{len(equipements)} équipement(s) référencé(s) sur la plateforme.")

        reservations = await get_reservations_stats(token, tous=demande_globale)
        mois_courant = date.today().strftime("%Y-%m")
        count = len([r for r in reservations if r["date"].startswith(mois_courant)])
        portee = "sur la plateforme" if demande_globale else "personnellement"
        return ChatResponse(reponse=f"{count} réservation(s) effectuée(s) {portee} ce mois-ci.")

    # --- Disponibilité (jour ou semaine) ---
    if intention == "consulter_disponibilite":
        equipements = await get_equipements(token)
        equipement = resoudre_equipement(payload.message, equipements)
        if not equipement:
            return ChatResponse(reponse="Pour quel équipement souhaitez-vous connaître les disponibilités ?")

        jours = jours_de_la_periode(extraction.get("periode", "jour"))
        occupes = await get_creneaux_occupes(token, equipement["id"], jours[0], jours[-1])
        libres = calculer_creneaux_libres(occupes, jours)
        lignes = [f"- {j} : {', '.join(h) if h else 'aucun créneau libre'}" for j, h in libres.items()]
        return ChatResponse(reponse=f"Disponibilités de {equipement['nom']} :\n" + "\n".join(lignes))

    # --- Création d'équipement : réservée aux rôles habilités, guidée vers le formulaire ---
    if intention == "creer_equipement":
        if user["role"] not in ["ADMIN", "TECHNICIEN"]:
            return ChatResponse(reponse="Seuls les administrateurs et techniciens peuvent ajouter un équipement.")
        return ChatResponse(reponse="Pour ajouter un équipement, rendez-vous sur la page Équipements puis cliquez sur « Nouvel équipement » — le formulaire vous guidera pour renseigner le numéro de série et le laboratoire.")

    if concerne_le_labo(payload.message):
        # Le message parle bien du laboratoire, mais aucune intention précise
        # n'a été reconnue — on ne laisse JAMAIS le LLM répondre librement
        # sur ce sujet (il n'a accès à aucune donnée réelle et pourrait en
        # inventer). On propose un menu clair à la place.
        return ChatResponse(reponse=(
            "Je ne suis pas certain de bien comprendre votre demande. Voici ce que je peux faire :\n"
            "- Réserver un équipement\n"
            "- Consulter vos réservations\n"
            "- Annuler une réservation\n"
            "- Vous informer sur une maintenance ou une disponibilité"
        ))
        
    return ChatResponse(reponse=await repondre_conversationnel(payload.message))  
   

async def _gerer_reservation(payload, token, extraction) -> ChatResponse:
    slots_manquants = [c for c in ["equipement", "date", "heure_debut", "heure_fin"] if not extraction.get(c)]
    if slots_manquants:
        return ChatResponse(reponse=f"Il me manque : {', '.join(slots_manquants)}.", intention="reserver")

    equipements = await get_equipements(token)
    equipement = resoudre_equipement(extraction["equipement"], equipements)
    if not equipement:
        return ChatResponse(reponse=f"Je ne trouve pas d'équipement correspondant à « {extraction['equipement']} ».")

    reservation_prete = {
        "laboratoire": equipement["laboratoire"],
        "equipements": [equipement["id"]],
        "date": extraction["date"],
        "heure_debut": extraction["heure_debut"],
        "heure_fin": extraction["heure_fin"],
        "motif": "Réservation via assistant",
    }
    update_session(payload.session_id, attente_confirmation=True, action_en_attente="creer_reservation", reservation_prete=reservation_prete)
    return ChatResponse(
        reponse=f"{equipement['nom']} disponible : confirmez-vous la réservation du {extraction['date']} de {extraction['heure_debut']} à {extraction['heure_fin']} ?",
        necessite_confirmation=True,
    )


async def _gerer_annulation(payload, token, extraction) -> ChatResponse:
    if not extraction.get("date"):
        return ChatResponse(reponse="Quelle réservation souhaitez-vous annuler (date et heure) ?")

    reservations = await get_mes_reservations(token)
    candidats = [r for r in reservations if r["date"] == extraction["date"] and r["statut"] in ["EN_ATTENTE", "VALIDEE"]]
    if extraction.get("heure_debut"):
        candidats = [r for r in candidats if r["heure_debut"][:5] == extraction["heure_debut"]]

    if not candidats:
        return ChatResponse(reponse="Je ne trouve aucune réservation correspondante.")
    if len(candidats) > 1:
        return ChatResponse(reponse="Plusieurs réservations correspondent, pouvez-vous préciser l'heure exacte ?")

    reservation = candidats[0]
    update_session(payload.session_id, attente_confirmation=True, action_en_attente="annuler_reservation", reservation_id_a_annuler=reservation["id"])
    return ChatResponse(
        reponse=f"Confirmez-vous l'annulation de la réservation du {reservation['date']} à {reservation['heure_debut'][:5]} ({reservation['laboratoire_nom']}) ?",
        necessite_confirmation=True,
    )
  
  
