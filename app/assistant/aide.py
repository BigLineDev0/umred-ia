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
from datetime import date

from rapidfuzz import fuzz

from app.assistant.contexte import Contexte
from app.assistant.formatage import formater_date, formater_date_absolue, formater_heure, formater_liste
from app.assistant.navigation import actions_pages
from app.core.constantes import STATUTS_EQUIPEMENT_NON_RESERVABLES, Role
from app.nlu.normalisation import JOURS_SEMAINE
from app.nlu.texte import contient_un, normaliser_texte
from app.schemas.chat import ChatAction, ChatOption, ChatResponse
from app.nlu.fautes import corriger_dates
from app.nlu.normalisation import normaliser_date
from app.nlu.regles import _DATE
from app.services.disponibilite import LIBELLES_MOMENTS, fenetre_moment
from app.services.django_client import get_equipements, get_horaires, get_reservations
from app.services.matching import resoudre_equipement, version_actuelle

NOMBRE_MAX_CONSEILS = 4
LONGUEUR_MAX_EXTRAIT = 300

MOTS_DEFINITION = ("que signifie", "veut dire", "difference entre", "quelle date", "c'est quoi le statut", "statut ")

# Textes décrivant des règles appliquées par Django (sources citées).
# apps/reservations/models.py : statut_initial, STATUTS_BLOQUANTS, annuler.
ETAPES_RESERVATION = (
    "Une réservation se fait en quatre étapes :\n"
    "1. Vous indiquez l'équipement et le créneau, ici ou dans le formulaire de réservation.\n"
    "2. La plateforme vérifie la demande : horaires d'ouverture, conflits avec les réservations validées, "
    "maintenance prévue ce jour-là et état de l'équipement.\n"
    "3. Je vous présente un récapitulatif et vous demande une confirmation : rien n'est enregistré avant votre « oui ».\n"
    "4. La demande est enregistrée : validée immédiatement, ou en attente de validation (demande d'étudiant, "
    "équipement sensible, ou autres demandes sur le même créneau). Vous êtes notifié de la décision."
)
STATUTS_RESERVATION = (
    "Statuts d'une réservation :\n"
    "- En attente : la demande est enregistrée mais pas encore acceptée. Elle ne bloque pas le créneau : d'autres "
    "demandes peuvent viser le même, et un validateur arbitre selon les priorités.\n"
    "- Validée : le créneau vous est attribué et il est bloqué pour les autres.\n"
    "- Refusée : écartée par un validateur (ou au profit d'une demande prioritaire), avec un motif quand il est renseigné.\n"
    "- Annulée : retirée avant le début du créneau.\n"
    "- Terminée : le créneau est passé."
)
# apps/equipements/models.py : StatutEquipement ; apps/reservations/models.py : STATUTS_EQUIPEMENT_NON_RESERVABLES.
STATUTS_EQUIPEMENT = (
    "Statuts d'un équipement :\n"
    "- Disponible : réservable sur ses créneaux libres.\n"
    "- En panne : une panne a été signalée ; l'équipement n'est plus réservable jusqu'à sa remise en service par un technicien.\n"
    "- En maintenance : une intervention est en cours ; l'équipement n'est pas réservable.\n"
    "- Hors service : retiré du parc, non réservable."
)
# apps/maintenances/models.py : cycle de vie et effet sur l'équipement.
FONCTIONNEMENT_MAINTENANCE = (
    "Une maintenance est préventive (prévue à l'avance) ou corrective (après une panne). Son cycle :\n"
    "- Signalée : une panne est déclarée ; l'équipement passe « en panne » et n'est plus réservable.\n"
    "- Planifiée : une date d'intervention est fixée. L'équipement reste réservable, sauf le jour de l'intervention, "
    "fermé aux réservations.\n"
    "- En cours : l'intervention a commencé ; l'équipement est immobilisé (« en maintenance »), non réservable.\n"
    "- Terminée : l'équipement est remis en service. Une maintenance peut aussi être annulée.\n"
    "Les techniciens planifient, démarrent et clôturent les maintenances ; vous pouvez signaler une panne."
)
PLUSIEURS_EQUIPEMENTS = (
    "Oui : une réservation peut regrouper plusieurs équipements utilisés ensemble, à condition qu'ils soient dans le "
    "même laboratoire (règle de la plateforme). Par exemple : « Réserve le PCR et la centrifugeuse demain de 10h à "
    "12h ». Si l'un d'eux est déjà pris, je vous propose un autre créneau commun ou de réserver seulement ceux qui "
    "sont libres."
)
ALTERNATIVES = (
    "Oui. Si le créneau demandé est occupé, la plateforme calcule des alternatives que je vous propose : d'autres "
    "créneaux libres proches de l'horaire voulu, un équipement équivalent libre au même créneau, ou la réservation des "
    "seuls équipements libres. L'alternative choisie est revérifiée avant votre confirmation. Dans le formulaire de "
    "réservation, vous pouvez aussi demander à être prévenu si le créneau se libère."
)
SAVOIR_SI_DISPONIBLE = (
    "Demandez-le-moi, par exemple « Le microscope optique est-il libre demain matin ? » : je lis dans la plateforme "
    "les créneaux déjà pris et les horaires de l'établissement, puis je vous donne les plages libres. Un équipement en "
    "panne, en maintenance ou hors service n'est jamais proposé. Le calendrier de votre tableau de bord affiche aussi "
    "vos réservations."
)

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
    if contient_un(m, MOTS_HORAIRES) or "autorise" in m:
        return await _horaires(ctx)
    expressions = expressions_de_date(ctx.message)
    if expressions and contient_un(m, MOTS_DEFINITION):
        return await _expliquer_dates(ctx, expressions)
    if contient_un(m, MOTS_DEFINITION):
        return ChatResponse(intention="aide", reponse=_glossaire(m))
    if "maintenance" in m:
        return ChatResponse(intention="aide", reponse=FONCTIONNEMENT_MAINTENANCE,
                            actions=actions_pages(role, "maintenances", "equipements"))
    if "notification" in m:
        return await _notifications(ctx)
    if contient_un(m, ("plusieurs equipements", "deux equipements")):
        return ChatResponse(intention="aide", reponse=PLUSIEURS_EQUIPEMENTS)
    if contient_un(m, ("autre creneau", "alternative")):
        return ChatResponse(intention="aide", reponse=ALTERNATIVES)
    if contient_un(m, ("comment savoir", "disponib", "libre")):
        return ChatResponse(intention="aide", reponse=SAVOIR_SI_DISPONIBLE,
                            options=[ChatOption(label="Voir les disponibilités", value="Quels équipements sont disponibles aujourd'hui ?")])
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
    return ChatResponse(intention="aide", reponse=ETAPES_RESERVATION,
                        actions=actions_pages(role, "reservation_formulaire", "mes_reservations"))


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
    ext = ctx.extraction
    if ext and ext.date:
        # « Demain matin correspond-il à un créneau autorisé ? » : ce jour-là précisément.
        return ChatResponse(intention="aide", reponse=_horaires_du_jour(horaires, ext.date, ext.moment))
    if not par_jour:
        texte = f"Les réservations sont possibles tous les jours de {formater_heure(ouverture)} à {formater_heure(fermeture)}."
    else:
        lignes = []
        for numero, jour in enumerate(JOURS_SEMAINE):
            ferme, ouv, ferm = par_jour.get(numero, (False, ouverture, fermeture))
            lignes.append(f"- {jour} : {'fermé' if ferme else f'{formater_heure(ouv)} – {formater_heure(ferm)}'}")
        texte = "Horaires de réservation de l'établissement :\n" + "\n".join(lignes)
    return ChatResponse(intention="aide", reponse=texte)


def _plage_du_jour(horaires: tuple, jour_iso: str) -> tuple[bool, str, str]:
    ouverture, fermeture, par_jour = horaires
    return par_jour.get(date.fromisoformat(jour_iso).weekday(), (False, ouverture, fermeture))


def _horaires_du_jour(horaires: tuple, jour_iso: str, moment: str | None) -> str:
    ferme, ouv, ferm = _plage_du_jour(horaires, jour_iso)
    jour = formater_date(jour_iso)
    if ferme:
        return f"L'établissement est fermé {jour} : aucune réservation n'est possible ce jour-là."
    texte = f"{jour[:1].upper()}{jour[1:]}, les réservations sont possibles de {formater_heure(ouv)} à {formater_heure(ferm)}."
    if moment:
        debut, fin = fenetre_moment(moment, ouv, ferm)
        if debut < fin:
            texte += f" {LIBELLES_MOMENTS[moment][:1].upper()}{LIBELLES_MOMENTS[moment][1:]} correspond à {formater_heure(debut)} – {formater_heure(fin)}."
        else:
            texte += f" Il n'y a pas de créneau {LIBELLES_MOMENTS[moment]} ce jour-là."
    return texte + " Un créneau doit aussi être libre et respecter la durée autorisée : je le vérifie auprès de la plateforme pour un équipement donné."


def expressions_de_date(message: str) -> list[str]:
    """Expressions de date du message, dans l'ordre (« jeudi », « demain », « après-demain matin »)."""
    return [m.group(1) for m in _DATE.finditer(corriger_dates(message).lower())]


async def _expliquer_dates(ctx: Contexte, expressions: list[str]) -> ChatResponse:
    """« Que signifie après-demain matin ? », « jeudi ou demain : quelle date retiens-tu ? »."""
    horaires = await get_horaires(ctx.token)
    moment = ctx.extraction.moment if ctx.extraction else None
    lignes = []
    for expression in expressions:
        iso = normaliser_date(re.sub(r"^le\s+", "", expression))
        if iso is None:
            lignes.append(f"« {expression} » ne correspond à aucune date valide.")
            continue
        ligne = f"« {expression} » désigne le {formater_date_absolue(iso)}"
        if moment and horaires and len(expressions) == 1:
            ferme, ouv, ferm = _plage_du_jour(horaires, iso)
            debut, fin = fenetre_moment(moment, ouv, ferm)
            ligne += (f" ; {LIBELLES_MOMENTS[moment]}, ce jour-là, va de {formater_heure(debut)} (ouverture) à "
                      f"{formater_heure(fin)}" if not ferme and debut < fin else " ; l'établissement est fermé ce jour-là")
        lignes.append(ligne + ".")
    if len(expressions) > 1:
        lignes.append("Quand vous indiquez une nouvelle date, elle remplace la précédente : je retiens toujours la "
                      "dernière date que vous avez donnée, et je la reformule avant toute confirmation.")
    return ChatResponse(intention="aide", reponse="\n".join(lignes))


def _glossaire(m: str) -> str:
    if "maintenance" in m:
        return FONCTIONNEMENT_MAINTENANCE
    if contient_un(m, ("reservation", "attente", "validee", "refusee", "annulee", "terminee", "demande")):
        return STATUTS_RESERVATION
    return STATUTS_EQUIPEMENT


async def _notifications(ctx: Contexte) -> ChatResponse:
    """Jamais « la notification a été envoyée » : seulement l'état réel des demandes."""
    a_venir = await get_reservations(ctx.token, a_venir=True)
    en_attente = [r for r in a_venir if r["statut"] == "EN_ATTENTE"]
    validees = [r for r in a_venir if r["statut"] == "VALIDEE"]
    lignes = ["La notification de validation n'existe qu'une fois la demande validée par un responsable."]
    if en_attente:
        lignes.append("Vos demandes encore en attente (aucune notification de validation possible pour l'instant) : "
                      + ", ".join(f"{formater_date(r['date'])} ({', '.join(r.get('equipements_noms') or [])})" for r in en_attente) + ".")
    if validees:
        lignes.append(f"Vous avez {len(validees)} réservation{'s' if len(validees) > 1 else ''} validée{'s' if len(validees) > 1 else ''} à venir : "
                      "leur notification doit apparaître dans la cloche, en haut de page.")
    lignes.append("Vérifiez la page « Mes notifications ». L'e-mail est un envoi complémentaire qui peut échouer sans "
                  "bloquer la validation : je ne peux pas savoir s'il vous a été remis.")
    return ChatResponse(intention="aide", reponse="\n\n".join(lignes), actions=actions_pages(ctx.user.role, "notifications", "mes_reservations"))


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
