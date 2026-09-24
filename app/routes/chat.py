from fastapi import APIRouter, Depends
from datetime import date as date_cls

from app.schemas.chat import ChatRequest, ChatResponse, ChatOption
from app.core.security import get_bearer_token, get_current_user
from app.core.session_store import get_session, update_session
from app.nlu.extractor import extraire_intention
from app.nlu.normalisation import normaliser_date, nettoyer_extraction
from app.nlu.domaine import concerne_le_labo
from app.nlu.conversation import repondre_conversationnel
from app.services.django_client import (
    get_alerte_usure, get_equipements, creer_reservation, get_mes_reservations, annuler_reservation,
    get_maintenances, get_creneaux_occupes, get_reservations_stats, get_toutes_maintenances,
    est_disponible,
)
from app.services.matching import resoudre_equipement, equipements_mentionnes
from app.services.disponibilite import calculer_creneaux_libres, jours_de_la_periode
from app.nlu.fallback import extraire_heures_seules
from app.services.django_client import get_apercu_accueil
from app.schemas.chat import DetailsConfirmation

LABELS_ROLE = {"ADMIN": "administrateur", "TECHNICIEN": "technicien", "CHERCHEUR": "enseignant-chercheur", "ETUDIANT": "étudiant"}
ROLES_SUPERVISEURS = ["ADMIN", "TECHNICIEN", "CHERCHEUR"]

def options_confirmation() -> list[ChatOption]:
    return [
        ChatOption(label="Oui, Confirmer", value="oui"),
        ChatOption(label="Non, Annuler", value="non"),
    ]

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, token: str = Depends(get_bearer_token), user=Depends(get_current_user)):
    session = get_session(payload.session_id)
    message_brut = payload.message.strip()
    message_lower = message_brut.lower()

    # --- 1. Sélection d'un équipement ambigu, en attente ---
    if session.get("attente_selection_equipement") and message_lower.startswith("equip_"):
        return await _traiter_selection_equipement(payload, token, session, message_lower)

    if session.get("attente_selection_disponibilite") and message_lower.startswith("dispo_"):
        return await _traiter_selection_disponibilite(payload, token, session, message_lower)

    if session.get("attente_heures_reservation"):
        heure_debut, heure_fin = extraire_heures_seules(message_brut)
        if not heure_debut or not heure_fin:
            return ChatResponse(reponse="Je n'ai pas compris l'horaire. Merci de préciser par exemple « de 10h à 12h ».")

        equipement = session["equipement_preselectionne"]
        date_cible = session["date_preselectionnee"]
        update_session(payload.session_id, attente_heures_reservation=False, slots_reservation={
            "date": date_cible, "heure_debut": heure_debut, "heure_fin": heure_fin,
            "equipements_resolus": [equipement["id"]], "laboratoire": equipement["laboratoire"],
        })
        
        return await _proposer_confirmation(payload.session_id, token, [equipement])
    
    # --- 2. Choix d'une alternative après conflit ---
    if session.get("attente_selection_alternative") and (message_lower.startswith("alt_") or message_lower.startswith("equiv_")):
        return await _traiter_selection_alternative(payload, session, message_lower)

    # --- 3. Confirmation finale en attente (oui/non) ---
    if session.get("attente_confirmation"):
        if message_lower in ["oui", "yes", "confirme", "confirmer"]:
            update_session(payload.session_id, attente_confirmation=False)
            return await _finaliser_reservation(payload, token, session)
        elif message_lower in ["non", "no", "annule", "annuler"]:
            update_session(payload.session_id, attente_confirmation=False)
            return ChatResponse(reponse="D'accord, j'annule cette demande.")
        else:
            update_session(payload.session_id, attente_confirmation=False)
            # on ne retourne rien ici : le message est traité comme une nouvelle demande, ci-dessous

    # --- Extraction habituelle ---
    extraction = await extraire_intention(message_brut)
    extraction = nettoyer_extraction(extraction)
    extraction["date"] = normaliser_date(extraction.get("date"))
    intention = extraction.get("intention")

    if intention not in ["reserver", "annuler"] and any(m in message_lower for m in ["combien", "statistique", "nombre de"]):
        intention = "statistiques"
        
    if intention not in ["reserver", "annuler"] and any(m in message_lower for m in ["disponible", "disponibilité", "libre", "quels équipements sont disponibles"]):
        intention = "consulter_disponibilite"

    if intention == "salutation":
        prenom = user.get("prenom")
        return ChatResponse(reponse=f"Bonjour {prenom} 👋 Comment puis-je vous aider aujourd'hui ?" if prenom else "Bonjour 👋")

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

    if intention == "reserver":
        return await _gerer_reservation(payload, token, extraction)

    if intention == "consulter_mes_reservations":
        reservations = await get_mes_reservations(token)
        a_venir = [r for r in reservations if r["date"] >= date_cls.today().isoformat()][:5]
        if not a_venir:
            return ChatResponse(reponse="Vous n'avez aucune réservation à venir.")
        lignes = [f"- {r['date']} de {r['heure_debut'][:5]} à {r['heure_fin'][:5]} ({r['laboratoire_nom']}, statut : {r['statut']})" for r in a_venir]
        return ChatResponse(reponse="Voici vos prochaines réservations :\n" + "\n".join(lignes))

    if intention == "annuler":
        return await _gerer_annulation(payload, token, extraction)

    if intention == "maintenance":
        equipements = await get_equipements(token)
        equipement = resoudre_equipement(message_brut, equipements)
        if not equipement:
            return ChatResponse(reponse="Sur quel équipement souhaitez-vous connaître la prochaine maintenance ?")
        maintenances = await get_maintenances(token, equipement["id"])
        if not maintenances:
            return ChatResponse(reponse=f"Aucune maintenance n'est actuellement planifiée pour {equipement['nom']}.")
        prochaine = sorted(maintenances, key=lambda m: m["date_planifiee"])[0]
        return ChatResponse(reponse=f"La prochaine maintenance de {equipement['nom']} est planifiée le {prochaine['date_planifiee'][:10]}.")

    if intention == "statistiques":
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
        mois_courant = date_cls.today().strftime("%Y-%m")
        count = len([r for r in reservations if r["date"].startswith(mois_courant)])
        portee = "sur la plateforme" if demande_globale else "personnellement"
        return ChatResponse(reponse=f"{count} réservation(s) effectuée(s) {portee} ce mois-ci.")

    if intention == "consulter_disponibilite":
        equipements = await get_equipements(token)
        equipement = resoudre_equipement(message_brut, equipements)

        if equipement:
            # Un équipement précis a été reconnu — comportement inchangé :
            # on affiche ses disponibilités jour par jour (ou sur la semaine).
            jours = jours_de_la_periode(extraction.get("periode", "jour"))
            occupes = await get_creneaux_occupes(token, equipement["id"], jours[0], jours[-1])
            libres = calculer_creneaux_libres(occupes, jours)
            lignes = [f"- {j} : {', '.join(h) if h else 'aucun créneau libre'}" for j, h in libres.items()]
            return ChatResponse(reponse=f"Disponibilités de {equipement['nom']} :\n" + "\n".join(lignes))

        # Aucun équipement précis nommé dans le message ("quels équipements
        # sont disponibles ?") — on liste tous les équipements ayant au
        # moins un créneau libre à la date demandée (aujourd'hui par défaut).
        date_cible = extraction.get("date") or date_cls.today().isoformat()
        return await _lister_disponibilites(token, payload.session_id, date_cible)

    if intention == "creer_equipement":
        if user["role"] not in ["ADMIN", "TECHNICIEN"]:
            return ChatResponse(reponse="Seuls les administrateurs et techniciens peuvent ajouter un équipement.")
        return ChatResponse(reponse="Rendez-vous sur la page Équipements puis « Nouvel équipement ».")

    if concerne_le_labo(message_brut):
        return ChatResponse(reponse=(
            "Je ne suis pas certain de bien comprendre votre demande. Voici ce que je peux faire :\n"
            "- Réserver un équipement\n- Consulter vos réservations\n- Annuler une réservation\n"
            "- Vous informer sur une maintenance ou une disponibilité"
        ))

    return ChatResponse(reponse=await repondre_conversationnel(message_brut))


# ---------------------------------------------------------------------
# RÉSERVATION — avec désambiguïsation multi-équipements et gestion des
# conflits de planning (alternatives + priorité).
# ---------------------------------------------------------------------

async def _gerer_reservation(payload, token, extraction) -> ChatResponse:
    slots_manquants = [c for c in ["date", "heure_debut", "heure_fin"] if not extraction.get(c)]
    if slots_manquants:
        return ChatResponse(reponse=f"Il me manque : {', '.join(slots_manquants)}.", intention="reserver")

    if not extraction.get("equipement"):
        return ChatResponse(reponse="Quel équipement souhaitez-vous réserver ?", intention="reserver")

    equipements = await get_equipements(token)
    par_famille = equipements_mentionnes(extraction["equipement"], equipements)

    if not par_famille:
        return ChatResponse(reponse=f"Je ne trouve aucun équipement correspondant à « {extraction['equipement']} ».")

    # Les familles à un seul membre sont résolues tout de suite ; celles à
    # plusieurs membres restent en attente de choix explicite.
    resolus: list[dict] = []
    ambigues: list[tuple[str, list[dict]]] = []
    for famille, candidats in par_famille.items():
        if len(candidats) == 1:
            resolus.append(candidats[0])
        else:
            ambigues.append((famille, candidats))

    session_id = payload.session_id
    update_session(session_id, slots_reservation={
        "date": extraction["date"], "heure_debut": extraction["heure_debut"], "heure_fin": extraction["heure_fin"],
        "equipements_resolus": [e["id"] for e in resolus],
        "laboratoire": resolus[0]["laboratoire"] if resolus else None,
    })

    if ambigues:
        return await _demander_selection_equipement(session_id, token, ambigues[0], extraction)

    return await _proposer_confirmation(session_id, token, resolus)


async def _demander_selection_equipement(session_id, token, ambigu, extraction) -> ChatResponse:
    famille, candidats = ambigu
    date, heure_debut, heure_fin = extraction["date"], extraction["heure_debut"], extraction["heure_fin"]

    options = []
    lignes = []
    for i, c in enumerate(candidats):
        libre = await est_disponible(token, c["id"], date, heure_debut, heure_fin)
        statut = "Disponible" if libre else "Déjà réservé à cette heure"
        lignes.append(f"{i + 1}. {c['nom']} — {c['laboratoire_nom']} — {statut}")
        options.append(ChatOption(label=f"{c['nom']} ({c['laboratoire_nom']})", value=f"equip_{i}"))

    update_session(session_id, attente_selection_equipement=True, candidats_ambigus=candidats)

    return ChatResponse(
        reponse=f"Plusieurs équipements correspondent à « {famille} » :\n" + "\n".join(lignes) + "\n\nLequel souhaitez-vous réserver ?",
        options=options,
    )


async def _traiter_selection_equipement(payload, token, session, message_lower) -> ChatResponse:
    try:
        index = int(message_lower.replace("equip_", ""))
        choisi = session["candidats_ambigus"][index]
    except (ValueError, IndexError, KeyError):
        return ChatResponse(reponse="Choix non reconnu, merci de cliquer sur une des options proposées.")

    slots = session["slots_reservation"]
    slots["equipements_resolus"].append(choisi["id"])
    if not slots.get("laboratoire"):
        slots["laboratoire"] = choisi["laboratoire"]
    update_session(payload.session_id, slots_reservation=slots, attente_selection_equipement=False, candidats_ambigus=None)

    equipements = [{"id": eid, "nom": choisi["nom"]} for eid in slots["equipements_resolus"]]
    return await _proposer_confirmation(payload.session_id, token, equipements, slots=slots)


async def _proposer_confirmation(session_id, token, equipements: list[dict], slots: dict | None = None) -> ChatResponse:
    slots = slots or get_session(session_id)["slots_reservation"]
    equipements_ids = slots["equipements_resolus"] if "equipements_resolus" in slots else [e["id"] for e in equipements]
    noms = ", ".join(e["nom"] for e in equipements) if equipements and "nom" in equipements[0] else "les équipements sélectionnés"

    reservation_prete = {
        "laboratoire": slots["laboratoire"],
        "equipements": equipements_ids,
        "date": slots["date"], "heure_debut": slots["heure_debut"], "heure_fin": slots["heure_fin"],
        "motif": "Réservation via assistant",
    }
    update_session(session_id, reservation_prete=reservation_prete, attente_confirmation=True, action_en_attente="creer_reservation")

    # Croisement avec l'algorithme d'alerte d'usure déjà construit pour le
    # dashboard technicien — l'assistant prévient AVANT la confirmation,
    # pas seulement après coup sur un tableau de bord que le chercheur
    # ne consulte jamais.
    alertes_texte = []
    for eid in equipements_ids:
        alerte = await get_alerte_usure(token, eid)
        if alerte:
            icone = "🔴" if alerte['niveau'] == 'critique' else "🟠"
            alertes_texte.append(f"{icone} {alerte['message']}")

    prefixe = "\n\n".join(alertes_texte) + "\n\n" if alertes_texte else ""


    laboratoire_nom = equipements[0].get('laboratoire_nom', '') if equipements else ''
    details = DetailsConfirmation(
        laboratoire=laboratoire_nom, equipement=noms,
        date=slots['date'], heure_debut=slots['heure_debut'][:5], heure_fin=slots['heure_fin'][:5],
    )

    return ChatResponse(
        reponse=f"{prefixe}Confirmez-vous cette réservation ?",
        necessite_confirmation=True, options=options_confirmation(), details_confirmation=details,
    )

    return ChatResponse(
        reponse=f"{prefixe}{noms} — confirmez-vous la réservation du {slots['date']} de {slots['heure_debut']} à {slots['heure_fin']} ?",
        necessite_confirmation=True,
        options=options_confirmation(),
    )


async def _finaliser_reservation(payload, token, session) -> ChatResponse:
    resultat = await creer_reservation(token, session["reservation_prete"])
    statut = resultat.pop('_status_code', 200)

    if statut == 409:
        return await _proposer_alternatives(payload.session_id, resultat)

    if statut >= 400:
        message = resultat.get('detail', "Une erreur est survenue lors de la création de la réservation.")
        return ChatResponse(reponse=f"Je n'ai pas pu créer la réservation : {message}")

    return ChatResponse(reponse="C'est confirmé, votre réservation a bien été enregistrée.")


async def _proposer_alternatives(session_id, resultat_conflit: dict) -> ChatResponse:
    alternatives = resultat_conflit.get("alternatives", {})
    memes = alternatives.get("memes_equipements", [])
    equivalents = alternatives.get("equipements_equivalents", [])

    options = []
    lignes = ["Ce créneau est déjà pris sur l'équipement demandé."]

    if resultat_conflit.get("priorite_superieure"):
        lignes.append("Votre demande est prioritaire, les techniciens ont été alertés du conflit.")

    if memes:
        lignes.append("\nAutres créneaux disponibles :")
        for i, alt in enumerate(memes):
            lignes.append(f"- {alt['date']} de {alt['heure_debut']} à {alt['heure_fin']}")
            options.append(ChatOption(label=f"{alt['date']} · {alt['heure_debut']}-{alt['heure_fin']}", value=f"alt_{i}"))

    if equivalents:
        lignes.append("\nÉquipements équivalents libres à la même heure :")
        for equiv in equivalents:
            lignes.append(f"- {equiv['nom']}")
            options.append(ChatOption(label=equiv['nom'], value=f"equiv_{equiv['id']}"))

    update_session(session_id, attente_selection_alternative=True, alternatives_proposees=memes, equivalents_proposes=equivalents)

    if not options:
        lignes.append("\nAucune alternative disponible dans les prochains jours — contactez un technicien.")

    return ChatResponse(reponse="\n".join(lignes), options=options or None)


async def _traiter_selection_alternative(payload, session, message_lower) -> ChatResponse:
    slots = session["reservation_prete"]

    if message_lower.startswith("alt_"):
        index = int(message_lower.replace("alt_", ""))
        alt = session["alternatives_proposees"][index]
        slots["date"], slots["heure_debut"], slots["heure_fin"] = alt["date"], alt["heure_debut"] + ":00", alt["heure_fin"] + ":00"
    else:
        equip_id = int(message_lower.replace("equiv_", ""))
        slots["equipements"] = [equip_id]

    update_session(payload.session_id, reservation_prete=slots, attente_selection_alternative=False, attente_confirmation=True, action_en_attente="creer_reservation")

    return ChatResponse(
        reponse=f"Nouvelle proposition : le {slots['date']} de {slots['heure_debut'][:5]} à {slots['heure_fin'][:5]}. Confirmez-vous ?",
        necessite_confirmation=True,
        options=options_confirmation(),
    )


async def _gerer_annulation(payload, token, extraction) -> ChatResponse:
    reservations = await get_mes_reservations(token)
    actives = [r for r in reservations if r["statut"] in ["EN_ATTENTE", "VALIDEE"]]

    message_lower = payload.message.lower()
    if not extraction.get("date") and any(m in message_lower for m in ["dernière", "derniere", "la plus récente"]):
        if not actives:
            return ChatResponse(reponse="Vous n'avez aucune réservation active à annuler.")
        # La plus proche dans le temps, pas la plus ancienne créée — c'est
        # ce qu'un utilisateur veut dire par "ma dernière réservation" au
        # sens usuel : celle qui arrive bientôt, pas celle du passé.
        reservation = sorted(actives, key=lambda r: (r["date"], r["heure_debut"]))[0]
        return await _demander_confirmation_annulation(payload.session_id, reservation)

    if not extraction.get("date"):
        return ChatResponse(reponse="Quelle réservation souhaitez-vous annuler (date et heure) ?")

    candidats = [r for r in actives if r["date"] == extraction["date"]]
    if extraction.get("heure_debut"):
        candidats = [r for r in candidats if r["heure_debut"][:5] == extraction["heure_debut"]]

    if not candidats:
        return ChatResponse(reponse="Je ne trouve aucune réservation correspondante.")
    if len(candidats) > 1:
        return ChatResponse(reponse="Plusieurs réservations correspondent, pouvez-vous préciser l'heure exacte ?")

    return await _demander_confirmation_annulation(payload.session_id, candidats[0])


async def _demander_confirmation_annulation(session_id, reservation) -> ChatResponse:
    update_session(session_id, attente_confirmation=True, action_en_attente="annuler_reservation", reservation_id_a_annuler=reservation["id"])
    return ChatResponse(
        reponse=f"Confirmez-vous l'annulation de la réservation du {reservation['date']} à {reservation['heure_debut'][:5]} ({reservation['laboratoire_nom']}) ?",
        necessite_confirmation=True,
        options=options_confirmation(),
    )
  
  
async def _lister_disponibilites(token, session_id, date_cible: str) -> ChatResponse:
    equipements = await get_equipements(token)
    disponibles = []

    for e in equipements:
        # Un équipement en panne ou hors service n'est jamais montré,
        # peu importe son planning de réservation.
        if e['statut'] in ['EN_PANNE', 'HORS_SERVICE']:
            continue
        occupes = await get_creneaux_occupes(token, e['id'], date_cible, date_cible)
        libres = calculer_creneaux_libres(occupes, [date_cible]).get(date_cible, [])
        if libres:
            disponibles.append({'equipement': e, 'creneaux': libres})

    if not disponibles:
        return ChatResponse(reponse=f"Aucun équipement n'a de créneau libre le {date_cible}.")

    lignes = [f"Voici les équipements disponibles le {date_cible} :"]
    options = []
    for d in disponibles[:8]:  # limite raisonnable pour ne pas noyer le chat
        e = d['equipement']
        lignes.append(f"- {e['nom']} ({e['laboratoire_nom']}) : {', '.join(d['creneaux'])}")
        options.append(ChatOption(label=f"{e['nom']} — {e['laboratoire_nom']}", value=f"dispo_{e['id']}_{date_cible}"))

    update_session(session_id, attente_selection_disponibilite=True)
    return ChatResponse(reponse="\n".join(lignes), options=options)


async def _traiter_selection_disponibilite(payload, token, session, message_lower) -> ChatResponse:
    try:
        _, equip_id_str, date_cible = message_lower.split("_", 2)
        equip_id = int(equip_id_str)
    except ValueError:
        return ChatResponse(reponse="Choix non reconnu, merci de cliquer sur une option proposée.")

    equipements = await get_equipements(token)
    equipement = next((e for e in equipements if e["id"] == equip_id), None)
    if not equipement:
        return ChatResponse(reponse="Cet équipement n'est plus disponible.")

    update_session(payload.session_id,
        attente_selection_disponibilite=False, attente_heures_reservation=True,
        equipement_preselectionne=equipement, date_preselectionnee=date_cible,
    )
    return ChatResponse(reponse=f"À quelle heure souhaitez-vous réserver {equipement['nom']} le {date_cible} ? (ex. de 10h à 12h)")


@router.get("/chat/accueil", response_model=ChatResponse)
async def accueil(token: str = Depends(get_bearer_token), user=Depends(get_current_user)):
    prenom = user.get("prenom") or ""
    apercu = await get_apercu_accueil(token)

    if apercu["total_du_jour"] == 0:
        message = f"Bonjour {prenom} 👋 Vous n'avez aucune réservation prévue aujourd'hui. Comment puis-je vous aider ?"
    elif apercu["prochaine"]:
        p = apercu["prochaine"]
        message = (
            f"Bonjour {prenom} 👋 Vous avez {apercu['total_du_jour']} réservation(s) aujourd'hui, "
            f"dont une à {p['heure_debut'][:5]} ({p['equipements_noms'][0] if p.get('equipements_noms') else 'salle'}, {p['laboratoire_nom']})."
        )
    else:
        message = f"Bonjour {prenom} 👋 Vous avez {apercu['total_du_jour']} réservation(s) aujourd'hui."

    return ChatResponse(reponse=message)