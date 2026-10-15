"""
Suivi des demandes : « où en est ma demande ? » et bilan chiffré d'une
période (« fais-moi le bilan de la semaine »).

Les chiffres sont CALCULÉS ici, à partir des réservations renvoyées par
Django avec les droits de l'utilisateur ; aucun n'est produit par le
modèle de langage. Le bilan est contrôlé avant affichage (la somme des
statuts doit égaler le total).
"""
import logging
from collections import Counter
from datetime import date, datetime, timedelta

from rapidfuzz import fuzz

from app.assistant.contexte import Contexte
from app.assistant.formatage import accorder, formater_date, formater_liste, formater_plage
from app.assistant.navigation import actions_pages
from app.core import temps
from app.core.constantes import LABELS_STATUT_RESERVATION, ROLES_VUE_GLOBALE, Role
from app.nlu.texte import contient_un, normaliser_texte
from app.schemas.chat import ChatResponse
from app.services.django_client import get_equipements, get_reservations
from app.services.reservations import reservations_actives_a_venir

logger = logging.getLogger(__name__)

NOMBRE_MAX_DEMANDES_AFFICHEES = 5
MOTS_PORTEE_GLOBALE = ("tous les utilisateurs", "au total", "toute la plateforme", "de la plateforme", "global",
                       "de l'etablissement", "du labo")
# Ordre d'affichage des statuts dans un bilan.
ORDRE_STATUTS = ["VALIDEE", "EN_ATTENTE", "TERMINEE", "REFUSEE", "ANNULEE"]
STATUTS_UTILISATION = {"VALIDEE", "TERMINEE"}
LIBELLES_BILAN = {"VALIDEE": ("validée", "validées"), "EN_ATTENTE": ("en attente", "en attente"),
                  "TERMINEE": ("terminée", "terminées"), "REFUSEE": ("refusée", "refusées"),
                  "ANNULEE": ("annulée", "annulées")}


def _donnees(r: dict) -> dict:
    return {"id": r["id"], "date": r["date"], "heure_debut": r["heure_debut"][:5], "heure_fin": r["heure_fin"][:5],
            "statut": r["statut"], "statut_libelle": LABELS_STATUT_RESERVATION.get(r["statut"], r["statut"]),
            "laboratoire": r.get("laboratoire_nom"), "equipements": r.get("equipements_noms") or []}


def _filtrer_par_equipement(reservations: list[dict], texte: str | None) -> list[dict]:
    if not texte:
        return reservations
    cible = normaliser_texte(texte)
    filtrees = [r for r in reservations
                if any(fuzz.partial_ratio(cible, normaliser_texte(n)) >= 85 for n in r.get("equipements_noms") or [])]
    return filtrees or reservations  # filtre ignoré s'il élimine tout (nom mal reconnu)


# ---------------------------------------------------------------------------
# « Où en est ma demande ? »
# ---------------------------------------------------------------------------

def _depuis(date_creation: str | None, maintenant: datetime) -> str:
    if not date_creation:
        return ""
    try:
        cree = datetime.fromisoformat(date_creation.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if cree.tzinfo is not None:
        cree = cree.astimezone(temps.FUSEAU).replace(tzinfo=None)
    jours = (maintenant.date() - cree.date()).days
    if jours <= 0:
        return " (déposée aujourd'hui)"
    return f" (déposée il y a {accorder(jours, 'jour')})"


def _raison_attente(r: dict, sensibles: set[int], role: str | None) -> str:
    """
    Raison de l'attente, déduite des règles de gestion de Django
    (Reservation.statut_initial) et des données réelles de l'équipement.
    """
    if any(i in sensibles for i in r.get("equipements") or []):
        return "un équipement sensible est concerné : un technicien doit valider la demande"
    if role == Role.ETUDIANT:
        return "les demandes des étudiants sont validées par leur encadrant ou, à défaut, par un technicien"
    return "d'autres demandes visaient le même créneau : un validateur arbitre selon les priorités des projets"


async def _statut_du_jour(ctx: Contexte, date_iso: str) -> ChatResponse:
    """« Ma réservation de lundi, elle est confirmée ? » : l'état réel de chacune ce jour-là."""
    ext = ctx.extraction
    actions = actions_pages(ctx.user.role, "mes_reservations")
    du_jour = sorted(await get_reservations(ctx.token, date_debut=date_iso, date_fin=date_iso),
                     key=lambda r: r["heure_debut"])
    du_jour = _filtrer_par_equipement(du_jour, ext.equipement)
    if not du_jour:
        return ChatResponse(type="reservations", data=[], actions=actions, intention="statut_demande",
                            reponse=f"Vous n'avez aucune réservation {formater_date(date_iso)}.")
    sensibles = {e["id"] for e in await get_equipements(ctx.token) if e.get("necessite_validation")}
    lignes = []
    for r in du_jour[:NOMBRE_MAX_DEMANDES_AFFICHEES]:
        quoi = ", ".join(r.get("equipements_noms") or []) or r.get("laboratoire_nom", "")
        etat = {"VALIDEE": "confirmée", "TERMINEE": "terminée", "ANNULEE": "annulée", "REFUSEE": "refusée"}.get(r["statut"])
        if r["statut"] == "EN_ATTENTE":
            etat = f"en attente de validation : {_raison_attente(r, sensibles, ctx.user.role)}"
        lignes.append(f"{quoi} {formater_plage(r['heure_debut'], r['heure_fin'])} : {etat or r['statut']}")
    introduction = f"Vos réservations {formater_date(date_iso)} :"
    reponse = (f"Votre réservation {formater_date(date_iso)}, {lignes[0]}." if len(lignes) == 1
               else "\n".join([introduction, *[f"- {ligne}" for ligne in lignes]]))
    return ChatResponse(type="reservations", reponse=reponse, actions=actions, intention="statut_demande",
                        data=[_donnees(r) for r in du_jour[:NOMBRE_MAX_DEMANDES_AFFICHEES]])


async def statut_demandes(ctx: Contexte) -> ChatResponse:
    ext = ctx.extraction
    if ext and ext.date:
        return await _statut_du_jour(ctx, ext.date)
    actions = actions_pages(ctx.user.role, "mes_reservations")
    en_attente = reservations_actives_a_venir(await get_reservations(ctx.token, statut="EN_ATTENTE"))
    en_attente = _filtrer_par_equipement(en_attente, ext.equipement if ext else None)

    if not en_attente:
        return ChatResponse(type="reservations", data=[], actions=actions, intention="statut_demande",
                            reponse="Vous n'avez aucune demande en attente de validation. "
                                    "Pour une demande refusée, demandez-moi « pourquoi ma demande a été refusée ? ».")

    sensibles = {e["id"] for e in await get_equipements(ctx.token) if e.get("necessite_validation")}
    maintenant = temps.maintenant()
    affichees = en_attente[:NOMBRE_MAX_DEMANDES_AFFICHEES]
    if len(affichees) == 1:
        r = affichees[0]
        quoi = ", ".join(r.get("equipements_noms") or []) or r.get("laboratoire_nom", "")
        reponse = (f"Votre demande pour {quoi}, {formater_date(r['date'])} {formater_plage(r['heure_debut'], r['heure_fin'])}"
                   f"{_depuis(r.get('date_creation'), maintenant)}, est en attente de validation : "
                   f"{_raison_attente(r, sensibles, ctx.user.role)}. Vous serez notifié dès qu'une décision sera prise.")
    else:
        lignes = [f"Vous avez {accorder(len(en_attente), 'demande')} en attente de validation :"]
        for r in affichees:
            quoi = ", ".join(r.get("equipements_noms") or []) or r.get("laboratoire_nom", "")
            lignes.append(f"- {formater_date(r['date'])} {formater_plage(r['heure_debut'], r['heure_fin'])} · {quoi}"
                          f"{_depuis(r.get('date_creation'), maintenant)} : {_raison_attente(r, sensibles, ctx.user.role)}")
        lignes.append("Vous serez notifié dès qu'une décision sera prise.")
        reponse = "\n".join(lignes)
    return ChatResponse(type="reservations", reponse=reponse, actions=actions, intention="statut_demande",
                        data=[_donnees(r) for r in affichees])


# ---------------------------------------------------------------------------
# Bilan d'une période
# ---------------------------------------------------------------------------

def periode_du_bilan(periode: str, message_normalise: str, aujourd_hui: date | None = None) -> tuple[date, date, str]:
    """(début, fin, libellé). Par défaut : la semaine en cours, du lundi au dimanche."""
    aujourd_hui = aujourd_hui or temps.aujourd_hui()
    if "mois" in message_normalise:
        debut = aujourd_hui.replace(day=1)
        suivant = (debut + timedelta(days=32)).replace(day=1)
        return debut, suivant - timedelta(days=1), "ce mois-ci"
    lundi = aujourd_hui - timedelta(days=aujourd_hui.weekday())
    if periode == "semaine_derniere":
        lundi -= timedelta(days=7)
        libelle = "la semaine dernière"
    elif periode == "semaine_prochaine":
        lundi += timedelta(days=7)
        libelle = "la semaine prochaine"
    else:
        libelle = "cette semaine"
    return lundi, lundi + timedelta(days=6), libelle


def _duree_heures(r: dict) -> float:
    debut = datetime.strptime(r["heure_debut"][:5], "%H:%M")
    fin = datetime.strptime(r["heure_fin"][:5], "%H:%M")
    return max((fin - debut).total_seconds() / 3600, 0.0)


def calculer_bilan(reservations: list[dict]) -> dict:
    par_statut = Counter(r["statut"] for r in reservations)
    utilisees = [r for r in reservations if r["statut"] in STATUTS_UTILISATION]
    equipements = Counter(nom for r in utilisees for nom in r.get("equipements_noms") or [])
    bilan = {
        "total": len(reservations),
        "par_statut": {s: par_statut[s] for s in ORDRE_STATUTS if par_statut.get(s)},
        "heures_utilisation": round(sum(_duree_heures(r) for r in utilisees), 1),
        "equipement_principal": equipements.most_common(1)[0] if equipements else None,
    }
    # Contrôle de cohérence avant affichage : chaque réservation a un et un
    # seul statut connu.
    bilan["coherent"] = sum(bilan["par_statut"].values()) == bilan["total"]
    return bilan


def _heures(valeur: float) -> str:
    entier = int(valeur)
    minutes = round((valeur - entier) * 60)
    return f"{entier} h" + (f" {minutes:02d}" if minutes else "")


async def bilan(ctx: Contexte) -> ChatResponse:
    ext = ctx.extraction
    debut, fin, libelle = periode_du_bilan(ext.periode if ext else "semaine", ctx.message_normalise)
    globale = contient_un(ctx.message_normalise, MOTS_PORTEE_GLOBALE) and ctx.user.role in ROLES_VUE_GLOBALE
    reservations = await get_reservations(ctx.token, tous=globale, date_debut=debut.isoformat(),
                                          date_fin=fin.isoformat(), archivees=True)
    chiffres = calculer_bilan(reservations)
    qui = "de la plateforme" if globale else "de vos réservations"
    intervalle = f"du {formater_date(debut.isoformat())} au {formater_date(fin.isoformat())}"
    actions = actions_pages(ctx.user.role, "pilotage", "mes_reservations")

    if not chiffres["coherent"]:
        logger.warning("Bilan incohérent (statuts inattendus) : chiffres non affichés.")
        return ChatResponse(type="error", intention="bilan", actions=actions,
                            reponse="Je n'ai pas pu établir un bilan fiable pour cette période. "
                                    "Consultez la liste de vos réservations.")
    if chiffres["total"] == 0:
        return ChatResponse(type="statistics", intention="bilan", actions=actions, data=chiffres,
                            reponse=f"Aucune réservation {libelle} ({intervalle}).")

    details = formater_liste([accorder(n, *LIBELLES_BILAN[s]) for s, n in chiffres["par_statut"].items()])
    phrases = [f"Bilan {qui} {libelle} ({intervalle}) : {accorder(chiffres['total'], 'réservation')}, dont {details}."]
    if chiffres["heures_utilisation"]:
        phrases.append(f"Temps d'utilisation réservé (validées et terminées) : {_heures(chiffres['heures_utilisation'])}.")
    if chiffres["equipement_principal"]:
        nom, nombre = chiffres["equipement_principal"]
        phrases.append(f"Équipement le plus réservé : {nom} ({accorder(nombre, 'réservation')}).")
    if globale is False and contient_un(ctx.message_normalise, MOTS_PORTEE_GLOBALE):
        phrases.append("Le bilan de toute la plateforme est réservé aux administrateurs ; voici le vôtre.")
    return ChatResponse(type="statistics", intention="bilan", reponse=" ".join(phrases), actions=actions,
                        data={**chiffres, "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()},
                              "portee": "plateforme" if globale else "personnelle"})
