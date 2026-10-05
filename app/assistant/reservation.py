"""
Parcours de réservation, étape par étape :

    demande -> COLLECTE (infos manquantes ?) -> SELECTION_EQUIPEMENT (ambiguïté ?)
            -> CONFIRMATION ─> envoi à Django -> succès
                                                 └> conflit 409 ─> SELECTION_ALTERNATIVE ──> CONFIRMATION

Le « brouillon » est la demande en cours de construction : il survit
d'un message à l'autre, ce qui permet à l'utilisateur de compléter sa
demande (« Réserve le microscope » puis « demain de 10h à 12h »).
"""
import asyncio
from datetime import datetime
from typing import Any

from app.assistant.contexte import Contexte, est_non, est_oui, lire_choix, lire_numero, options_confirmation
from app.assistant.formatage import formater_date, formater_liste, formater_plage
from app.core.constantes import STATUTS_EQUIPEMENT_NON_RESERVABLES
from app.core.session_store import Etape, definir_etape, terminer_etape
from app.nlu.intentions import Intention
from app.schemas.chat import ChatOption, ChatResponse, DetailsConfirmation
from app.services.disponibilite import est_disponible
from app.services.django_client import creer_reservation, get_alerte_usure, get_equipements
from app.services.matching import equipements_mentionnes

MOTIF_PAR_DEFAUT = "Réservation via l'assistant UMRED"
LIBELLES_CHAMPS = {"equipement": "l'équipement", "date": "la date", "heure_debut": "l'heure de début", "heure_fin": "l'heure de fin"}
# Pendant la collecte, un message reconnu comme l'une de ces intentions
# complète la demande en cours ; toute autre intention l'abandonne.
INTENTIONS_COMPATIBLES_COLLECTE = {Intention.RESERVER, Intention.AUTRE, Intention.CONSULTER_DISPONIBILITE}


# ---------------------------------------------------------------------------
# 1. Démarrage et collecte des informations
# ---------------------------------------------------------------------------

def _brouillon_vide() -> dict[str, Any]:
    return {"equipement": None, "equipement_choisi": None, "date": None, "heure_debut": None, "heure_fin": None}


def _completer_brouillon(brouillon: dict[str, Any], ctx: Contexte) -> dict[str, Any]:
    """Les nouvelles informations remplacent les anciennes, les absentes ne les effacent pas."""
    ext = ctx.extraction
    nouveau = dict(brouillon)
    if ext.equipement:
        # L'utilisateur désigne un autre équipement : on oublie le précédent choix.
        nouveau.update(equipement=ext.equipement, equipement_choisi=None)
    for champ in ("date", "heure_debut", "heure_fin"):
        if getattr(ext, champ):
            nouveau[champ] = getattr(ext, champ)
    # On attendait l'heure de fin et l'utilisateur répond par une seule
    # heure (« 12h ») postérieure au début : c'est la réponse à la question.
    if (brouillon["heure_debut"] and not brouillon["heure_fin"] and ext.heure_debut
            and not ext.heure_fin and ext.heure_debut > brouillon["heure_debut"]):
        nouveau.update(heure_debut=brouillon["heure_debut"], heure_fin=ext.heure_debut)
    return nouveau


async def demarrer_reservation(ctx: Contexte) -> ChatResponse:
    return await avancer_reservation(ctx, _completer_brouillon(_brouillon_vide(), ctx))


async def poursuivre_collecte(ctx: Contexte) -> ChatResponse | None:
    """
    L'utilisateur répond à une question du type « il me manque l'heure de fin ».
    Renvoie None si le message n'a rien à voir avec la demande en cours :
    le dialogue l'abandonne alors et traite le message normalement.
    """
    brouillon = ctx.contexte_etape["brouillon"]
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


async def avancer_reservation(ctx: Contexte, brouillon: dict[str, Any]) -> ChatResponse:
    """
    Fait progresser la demande aussi loin que possible : vérifie la
    cohérence, identifie l'équipement, demande ce qui manque, puis passe
    à la sélection (si ambiguïté) ou directement à la confirmation.
    """
    remarques = _verifier_coherence(brouillon, datetime.now())

    # --- Identification de l'équipement ---
    par_famille: dict[str, list[dict]] = {}
    if brouillon["equipement_choisi"]:
        par_famille = {"choix": [brouillon["equipement_choisi"]]}
    else:
        equipements = await get_equipements(ctx.token)
        texte = brouillon["equipement"] or ctx.message
        par_famille = equipements_mentionnes(texte, equipements)
        if par_famille:
            brouillon["equipement"] = texte
        elif brouillon["equipement"]:
            remarques.append(f"Je ne trouve aucun équipement correspondant à « {brouillon['equipement']} ».")
            brouillon["equipement"] = None

    # --- Informations manquantes : on les demande toutes en une fois ---
    manquants = [c for c in ("date", "heure_debut", "heure_fin") if not brouillon[c]]
    if not par_famille:
        manquants.insert(0, "equipement")
    if manquants:
        definir_etape(ctx.session, Etape.COLLECTE_RESERVATION, brouillon=brouillon)
        return ChatResponse(reponse=_question_informations_manquantes(remarques, manquants, par_famille), intention=Intention.RESERVER)

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


def _question_informations_manquantes(remarques: list[str], manquants: list[str], par_famille: dict) -> str:
    objet = ""
    if par_famille:
        noms = [candidats[0]["nom"] if len(candidats) == 1 else fam for fam, candidats in par_famille.items()]
        objet = f" {formater_liste(noms)}"
    question = f"Pour réserver{objet}, il me manque {formater_liste([LIBELLES_CHAMPS[c] for c in manquants])}."
    exemple = "« le microscope demain de 10h à 12h »" if "equipement" in manquants else "« demain de 10h à 12h »"
    return " ".join([*remarques, question, f"Par exemple : {exemple}."])


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
    lignes, options = [], []
    for i, (c, libre) in enumerate(zip(candidats, libres), start=1):
        statut = "disponible" if libre else "déjà réservé sur ce créneau"
        lignes.append(f"{i}. {c['nom']} ({c['laboratoire_nom']}) — {statut}")
        options.append(ChatOption(label=f"{c['nom']} ({c['laboratoire_nom']})", value=f"equip_{c['id']}"))

    definir_etape(ctx.session, Etape.SELECTION_EQUIPEMENT, creneau=creneau, resolus=resolus, ambigues=ambigues)
    return ChatResponse(
        reponse=f"Plusieurs équipements correspondent à « {famille} » :\n" + "\n".join(lignes) + "\n\nLequel souhaitez-vous réserver ?",
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
    definir_etape(ctx.session, Etape.CONFIRMATION_RESERVATION, reservation=reservation, equipements=equipements)

    # Croisement avec l'algorithme d'alerte d'usure du tableau de bord
    # technicien : l'utilisateur est prévenu AVANT de confirmer, et non
    # après coup sur un tableau de bord qu'il ne consulte jamais.
    alertes = await asyncio.gather(*(get_alerte_usure(ctx.token, e["id"]) for e in equipements))
    lignes = [f"{'🔴' if a['niveau'] == 'critique' else '🟠'} {a['message']}" for a in alertes if a]

    return ChatResponse(
        reponse="\n\n".join([*lignes, "Confirmez-vous cette réservation ?"]),
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
        return _proposer_alternatives(ctx, reservation, equipements, resultat.data)

    terminer_etape(ctx.session)
    if not resultat.ok:
        return ChatResponse(reponse=f"Je n'ai pas pu créer la réservation : {resultat.detail}")

    quand = f"{formater_date(reservation['date'])} {formater_plage(reservation['heure_debut'], reservation['heure_fin'])}"
    # Le statut initial est décidé par Django (règles de gestion) : un
    # étudiant ou un équipement sensible passe par une validation humaine.
    if resultat.data.get("statut") == "EN_ATTENTE":
        return ChatResponse(reponse=f"Votre demande pour {quand} est enregistrée. "
                                    "Elle est en attente de validation par un responsable : vous serez notifié de sa décision.")
    return ChatResponse(reponse=f"C'est confirmé ! Votre réservation pour {quand} est validée.")


# ---------------------------------------------------------------------------
# 4. Conflit de planning : alternatives proposées par Django
# ---------------------------------------------------------------------------

def _proposer_alternatives(ctx: Contexte, reservation: dict, equipements: list[dict], conflit: dict) -> ChatResponse:
    alternatives = conflit.get("alternatives") or {}
    # Créneaux libres pour TOUS les équipements demandés, classés par
    # proximité avec l'heure voulue ; chacun porte un message explicatif
    # (« disponible à partir de 11h00 »).
    memes = alternatives.get("creneaux", [])
    # Chaque équivalent indique l'équipement qu'il remplace ('remplace').
    equivalents = alternatives.get("equipements_equivalents", [])

    lignes = ["Ce créneau est déjà pris."]
    for c in conflit.get("conflits", []):
        lignes.append(f"- {c['equipement']} est réservé de {formater_plage(c['heure_debut'], c['heure_fin'])}")

    options = []
    if memes:
        lignes.append("\nCréneaux proposés :")
        for i, alt in enumerate(memes):
            quand = f"{formater_date(alt['date'])} {formater_plage(alt['heure_debut'], alt['heure_fin'])}"
            lignes.append(f"- {alt.get('message') or quand}")
            options.append(ChatOption(label=quand, value=f"alt_{i}"))
    if equivalents:
        lignes.append("\nÉquipements équivalents libres au même moment :")
        for equiv in equivalents:
            lignes.append(f"- {equiv['nom']}" + (f" (à la place de {equiv['remplace_nom']})" if equiv.get('remplace_nom') else ""))
            options.append(ChatOption(label=equiv["nom"], value=f"equiv_{equiv['id']}"))

    if not options:
        terminer_etape(ctx.session)
        lignes.append("\nAucune alternative n'est disponible dans les prochains jours - contactez un technicien.")
        return ChatResponse(reponse="\n".join(lignes))

    definir_etape(ctx.session, Etape.SELECTION_ALTERNATIVE, reservation=reservation, equipements=equipements,
                  alternatives=memes, equivalents=equivalents)
    return ChatResponse(reponse="\n".join(lignes), options=options)


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

async def reserver_equipement_choisi(ctx: Contexte, equipement: dict, date_iso: str) -> ChatResponse:
    """Démarre une réservation pour un équipement déjà identifié (clic dans la liste des disponibilités)."""
    brouillon = {**_brouillon_vide(), "equipement": equipement["nom"], "equipement_choisi": equipement, "date": date_iso}
    return await avancer_reservation(ctx, brouillon)
