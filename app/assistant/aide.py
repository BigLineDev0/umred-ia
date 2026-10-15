"""
Questions sur le FONCTIONNEMENT de la plateforme et conseil d'équipement.

Toutes les réponses sont déterministes : soit elles décrivent une règle
appliquée par Django (qui valide quoi, quand annuler), soit elles citent
des données réelles lues dans l'API (horaires de l'établissement, fiche
d'un équipement). Le modèle de langage n'intervient pas : un petit modèle
sans accès aux données inventerait une procédure ou un horaire.
"""
import re
from collections import Counter

from rapidfuzz import fuzz

from app.assistant.contexte import Contexte
from app.assistant.formatage import formater_heure, formater_liste
from app.assistant.navigation import actions_pages
from app.core.constantes import STATUTS_EQUIPEMENT_NON_RESERVABLES, Role
from app.nlu.normalisation import JOURS_SEMAINE
from app.nlu.texte import contient_un, normaliser_texte
from app.schemas.chat import ChatAction, ChatOption, ChatResponse
from app.services.django_client import get_equipements, get_horaires
from app.services.matching import resoudre_equipement, version_actuelle

NOMBRE_MAX_CONSEILS = 4
LONGUEUR_MAX_EXTRAIT = 300

MOTS_MODE_EMPLOI = ("comment utiliser", "mode d'emploi", "consignes de securite", "instructions")
MOTS_HORAIRES = ("horaire", "heures d'ouverture", "heure d'ouverture", "ouvre", "ferme")
MOTS_VALIDATION = ("valide", "validation")
MOTS_ANNULATION = ("annul",)
MOTS_PANNE = ("panne", "signaler", "casse", "marche pas", "marche plus", "fonctionne pas", "fonctionne plus",
              "defectueu", "est hs", "abime")

# Mots sans valeur pour identifier un besoin (« je veux faire une analyse »).
MOTS_VIDES = {
    "quel", "quelle", "quels", "quelles", "equipement", "equipements", "appareil", "appareils", "machine", "instrument",
    "utiliser", "faire", "realiser", "dois", "dois-je", "faut", "faut-il", "pour", "avec", "une", "des", "les", "mon",
    "mes", "veux", "voudrais", "aimerais", "analyse", "analyses", "analyser", "manipulation", "experience", "conseil",
    "conseille", "conseiller", "recommande", "recommander", "adapte", "adaptee", "lequel", "laquelle", "besoin", "est",
    "sont", "qui", "que", "quoi", "dans", "labo", "laboratoire", "mesure", "mesurer", "travail", "travaux", "pratique",
    "pratiques", "moi", "nous", "vous", "peux", "pouvez", "plus", "sur", "par",
}


# ---------------------------------------------------------------------------
# Questions sur le fonctionnement
# ---------------------------------------------------------------------------

async def repondre_aide(ctx: Contexte) -> ChatResponse:
    m = ctx.message_normalise
    role = ctx.user.role
    if contient_un(m, MOTS_MODE_EMPLOI):
        return await _mode_emploi(ctx)
    if contient_un(m, MOTS_HORAIRES):
        return await _horaires(ctx)
    if contient_un(m, MOTS_VALIDATION):
        return ChatResponse(reponse=_qui_valide(role), intention="aide",
                            actions=actions_pages(role, "mes_reservations"))
    if contient_un(m, MOTS_ANNULATION):
        return ChatResponse(
            intention="aide", actions=actions_pages(role, "mes_reservations"),
            reponse="Vous pouvez annuler une réservation en attente ou validée tant que son créneau n'a pas commencé : "
                    "demandez-moi par exemple « Annule ma réservation de demain », ou passez par « Mes réservations ». "
                    "Je vous demanderai toujours une confirmation avant d'annuler.",
        )
    if contient_un(m, MOTS_PANNE):
        return ChatResponse(
            intention="aide", actions=actions_pages(role, "equipements"),
            reponse="Pour signaler une panne, utilisez le bouton « Signaler une panne » de votre tableau de bord ou de "
                    "la fiche de l'équipement. Un technicien est alors prévenu et l'équipement n'est plus proposé "
                    "à la réservation jusqu'à sa remise en service.",
        )
    return ChatResponse(
        intention="aide", actions=actions_pages(role, "reservation_formulaire", "mes_reservations"),
        reponse="Pour réserver, dites-moi simplement l'équipement et le créneau, par exemple « Réserve le microscope "
                "demain de 10h à 12h ». Je vérifie la disponibilité auprès de la plateforme, puis je vous demande "
                "une confirmation avant d'enregistrer quoi que ce soit. Vous pouvez aussi utiliser le formulaire.",
    )


def _qui_valide(role: str | None) -> str:
    """Miroir de Reservation.statut_initial côté Django (règles de gestion)."""
    if role == Role.ETUDIANT:
        return ("Vos demandes sont validées par votre encadrant, ou par un technicien si vous n'en avez pas. "
                "Si un équipement sensible est concerné, c'est un technicien qui valide. "
                "Vous êtes notifié dès qu'une décision est prise.")
    return ("Vos réservations sont confirmées immédiatement, sauf deux cas : un équipement sensible (validation par "
            "un technicien) ou d'autres demandes en attente sur le même créneau (arbitrage selon les priorités "
            "des projets). Dans ces cas, vous êtes notifié de la décision.")


async def _horaires(ctx: Contexte) -> ChatResponse:
    horaires = await get_horaires(ctx.token)
    if horaires is None:
        return ChatResponse(intention="aide",
                            reponse="Je n'arrive pas à consulter les horaires de l'établissement pour le moment. "
                                    "Réessayez dans quelques instants.")
    ouverture, fermeture, par_jour = horaires
    if not par_jour:
        texte = f"Les réservations sont possibles tous les jours de {formater_heure(ouverture)} à {formater_heure(fermeture)}."
    else:
        lignes = []
        for numero, jour in enumerate(JOURS_SEMAINE):
            ferme, ouv, ferm = par_jour.get(numero, (False, ouverture, fermeture))
            lignes.append(f"- {jour} : {'fermé' if ferme else f'{formater_heure(ouv)} – {formater_heure(ferm)}'}")
        texte = "Horaires de réservation de l'établissement :\n" + "\n".join(lignes)
    return ChatResponse(intention="aide", reponse=texte)


def _extrait(texte: str) -> str:
    texte = " ".join(texte.split())
    return texte if len(texte) <= LONGUEUR_MAX_EXTRAIT else texte[:LONGUEUR_MAX_EXTRAIT].rsplit(" ", 1)[0] + "…"


async def _mode_emploi(ctx: Contexte) -> ChatResponse:
    equipements = await get_equipements(ctx.token)
    equipement = (resoudre_equipement(ctx.extraction.equipement or ctx.message, equipements)
                  or version_actuelle(ctx.equipement_du_contexte, equipements))
    if equipement is None:
        return ChatResponse(type="clarification", intention="aide",
                            reponse="De quel équipement souhaitez-vous le mode d'emploi ? "
                                    "Par exemple : « comment utiliser le microscope ? ».",
                            actions=actions_pages(ctx.user.role, "equipements"))
    fiche = ChatAction(label=f"Voir {equipement['nom']}", route=f"/equipements/{int(equipement['id'])}")
    instructions = (equipement.get("instructions_utilisation") or "").strip()
    securite = (equipement.get("consignes_securite") or "").strip()
    if not instructions and not securite:
        return ChatResponse(intention="aide", actions=[fiche],
                            reponse=f"Aucune instruction d'utilisation n'est renseignée pour {equipement['nom']}. "
                                    "Sa fiche peut contenir un manuel ; sinon, adressez-vous à un technicien.")
    parties = [f"Instructions pour {equipement['nom']} :"]
    if instructions:
        parties.append(_extrait(instructions))
    if securite:
        parties.append(f"Consignes de sécurité : {_extrait(securite)}")
    return ChatResponse(intention="aide", actions=[fiche], reponse="\n\n".join(parties))


# ---------------------------------------------------------------------------
# Conseil d'équipement
# ---------------------------------------------------------------------------

def _mots_du_besoin(message_normalise: str) -> list[str]:
    mots = re.findall(r"[a-z0-9-]+", message_normalise)
    return [m for m in mots if len(m) >= 3 and m not in MOTS_VIDES]


def _texte_fiche(equipement: dict) -> str:
    champs = ("nom", "categorie", "description", "marque", "modele")
    return normaliser_texte(" ".join(str(equipement.get(c) or "") for c in champs))


def _pertinence(mots: list[str], equipement: dict) -> int:
    """Nombre de mots du besoin retrouvés (à une faute près) dans la fiche de l'équipement."""
    fiche = _texte_fiche(equipement)
    return sum(1 for mot in mots if fuzz.partial_ratio(mot, fiche) >= 90)


async def conseiller_equipement(ctx: Contexte) -> ChatResponse:
    """
    « Quel équipement pour une PCR ? » : seuls les équipements dont la fiche
    (nom, catégorie, description) correspond au besoin exprimé sont
    proposés. Sans indice exploitable, l'assistant demande une précision en
    citant les catégories réellement présentes, plutôt que de deviner.
    """
    equipements = [e for e in await get_equipements(ctx.token) if e.get("statut") not in STATUTS_EQUIPEMENT_NON_RESERVABLES]
    mots = _mots_du_besoin(ctx.message_normalise)
    scores = sorted(((_pertinence(mots, e), e) for e in equipements), key=lambda t: -t[0])
    retenus = [e for score, e in scores if score > 0][:NOMBRE_MAX_CONSEILS]

    if not retenus:
        categories = Counter((e.get("categorie") or "").strip() for e in equipements)
        categories.pop("", None)
        exemples = formater_liste([c for c, _ in categories.most_common(5)], "ou") if categories else None
        precision = f" Les équipements disponibles relèvent par exemple de : {exemples}." if exemples else ""
        return ChatResponse(
            type="clarification", intention="conseil_equipement",
            reponse="Pour vous orienter, pouvez-vous préciser le type d'analyse ou la technique visée "
                    f"(par exemple « une PCR », « une mesure d'absorbance »)?{precision}",
            actions=actions_pages(ctx.user.role, "equipements"),
        )

    options = [ChatOption(label=f"Réserver {e['nom']}", value=f"Je veux réserver {e['nom']}",
                          description=e.get("laboratoire_nom")) for e in retenus]
    noms = formater_liste([e["nom"] for e in retenus], "ou")
    return ChatResponse(
        intention="conseil_equipement", options=options,
        reponse=f"D'après les fiches des équipements, {noms} "
                f"{'correspond' if len(retenus) == 1 else 'correspondent'} à votre besoin. "
                "Vérifiez la fiche pour confirmer qu'il convient à votre protocole.",
        actions=[ChatAction(label=f"Fiche : {e['nom']}", route=f"/equipements/{int(e['id'])}") for e in retenus[:2]],
        data=[{"id": e["id"], "nom": e["nom"], "laboratoire": e.get("laboratoire_nom"), "categorie": e.get("categorie")}
              for e in retenus],
    )


# ---------------------------------------------------------------------------
# Hors périmètre
# ---------------------------------------------------------------------------

REPONSE_HORS_PERIMETRE = (
    "Je suis l'assistant de SenLab : je ne réponds qu'aux questions sur la plateforme (réservations, "
    "disponibilités, équipements, maintenances). Que puis-je faire pour vous ?"
)
