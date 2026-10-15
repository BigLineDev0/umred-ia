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
from app.assistant.formatage import accorder, formater_date, formater_date_absolue, formater_liste, formater_plage
from app.assistant.navigation import actions_pages
from app.core import temps
from app.core.constantes import LABELS_STATUT_RESERVATION, ROLES_SUPERVISEURS, ROLES_VUE_GLOBALE, Role
from app.nlu.texte import contient_un, normaliser_texte
from app.schemas.chat import ChatResponse
from app.services.django_client import get_equipements, get_file_attente, get_reservations
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


# « … en ne comptant que les réservations validées » : statuts retenus et libellé.
FILTRES_STATUT = [
    (("validee",), {"VALIDEE", "TERMINEE"}, "validées (y compris celles déjà terminées)"),
    (("annulee",), {"ANNULEE"}, "annulées"),
    (("refusee",), {"REFUSEE"}, "refusées"),
    (("en attente",), {"EN_ATTENTE"}, "en attente"),
    (("terminee",), {"TERMINEE"}, "terminées"),
]
MOTS_RESTRICTION = ("ne comptant que", "uniquement", "seulement", "que les")
MOTS_REPRISE = ("recommence", "refais", "le meme", "meme resume", "meme bilan")
MOTS_CLASSEMENT = ("plus utilise", "plus reserve", "plus demande")


def _filtre_statut(m: str) -> tuple[set[str] | None, str | None]:
    if contient_un(m, MOTS_RESTRICTION):
        for mots, statuts, libelle in FILTRES_STATUT:
            if contient_un(m, mots):
                return statuts, libelle
    return None, None


def _intervalle(debut: date, fin: date) -> str:
    return f"du {formater_date_absolue(debut.isoformat())} au {formater_date_absolue(fin.isoformat())}"


def _detail_statuts(chiffres: dict) -> str:
    return formater_liste([accorder(n, *LIBELLES_BILAN[s]) for s, n in chiffres["par_statut"].items()])


async def bilan(ctx: Contexte) -> ChatResponse:
    m = ctx.message_normalise
    if contient_un(m, MOTS_CLASSEMENT):
        return await _classement(ctx)
    if "compare" in m:
        return await _comparaison(ctx)

    ext = ctx.extraction
    memoire = ctx.session["memoire"]
    precedent = memoire.get("bilan")
    if precedent and contient_un(m, MOTS_REPRISE) and not contient_un(m, ("semaine", "mois")):
        # « Recommence le résumé… » : même période que le bilan précédent.
        debut, fin, libelle = date.fromisoformat(precedent["debut"]), date.fromisoformat(precedent["fin"]), precedent["libelle"]
    else:
        debut, fin, libelle = periode_du_bilan(ext.periode if ext else "semaine", m)
    statuts, libelle_filtre = _filtre_statut(m)
    globale = contient_un(m, MOTS_PORTEE_GLOBALE) and ctx.user.role in ROLES_VUE_GLOBALE
    reservations = await get_reservations(ctx.token, tous=globale, date_debut=debut.isoformat(),
                                          date_fin=fin.isoformat(), archivees=True)
    if statuts:
        reservations = [r for r in reservations if r["statut"] in statuts]
    chiffres = calculer_bilan(reservations)
    memoire["bilan"] = {"debut": debut.isoformat(), "fin": fin.isoformat(), "libelle": libelle}
    qui = "de la plateforme" if globale else "de vos réservations"
    intervalle = _intervalle(debut, fin)
    actions = actions_pages(ctx.user.role, "pilotage", "mes_reservations")
    donnees = {**chiffres, "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()},
               "portee": "plateforme" if globale else "personnelle", "filtre": sorted(statuts) if statuts else None}

    if not chiffres["coherent"]:
        logger.warning("Bilan incohérent (statuts inattendus) : chiffres non affichés.")
        return ChatResponse(type="error", intention="bilan", actions=actions,
                            reponse="Je n'ai pas pu établir un bilan fiable pour cette période. "
                                    "Consultez la liste de vos réservations.")
    filtre = f" (en ne comptant que les réservations {libelle_filtre})" if libelle_filtre else ""
    if chiffres["total"] == 0:
        return ChatResponse(type="statistics", intention="bilan", actions=actions, data=donnees,
                            reponse=f"Aucune réservation {libelle}{filtre} ({intervalle}).")

    phrases = [f"Bilan {qui} {libelle}{filtre}, {intervalle} : {accorder(chiffres['total'], 'réservation')}, "
               f"dont {_detail_statuts(chiffres)}."]
    if chiffres["heures_utilisation"]:
        phrases.append(f"Temps d'utilisation réservé (validées et terminées) : {_heures(chiffres['heures_utilisation'])}.")
    if chiffres["equipement_principal"]:
        nom, nombre = chiffres["equipement_principal"]
        phrases.append(f"Équipement le plus réservé : {nom} ({accorder(nombre, 'réservation')}).")
    phrases.append(f"Période utilisée : {intervalle} ({'semaine du lundi au dimanche' if 'semaine' in libelle else 'mois civil'}), "
                   "d'après les réservations enregistrées sur la plateforme, archives comprises.")
    if globale is False and contient_un(m, MOTS_PORTEE_GLOBALE):
        phrases.append("Le bilan de toute la plateforme est réservé aux administrateurs ; voici le vôtre.")
    return ChatResponse(type="statistics", intention="bilan", reponse=" ".join(phrases), actions=actions, data=donnees)


async def _comparaison(ctx: Contexte) -> ChatResponse:
    """« Compare cette semaine à la semaine dernière » (ou ce mois-ci au mois dernier)."""
    m = ctx.message_normalise
    aujourd_hui = temps.aujourd_hui()
    if "mois" in m:
        debut = aujourd_hui.replace(day=1)
        fin = (debut + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        fin_prec = debut - timedelta(days=1)
        periodes = [("ce mois-ci", debut, fin), ("le mois dernier", fin_prec.replace(day=1), fin_prec)]
    else:
        lundi = aujourd_hui - timedelta(days=aujourd_hui.weekday())
        periodes = [("cette semaine", lundi, lundi + timedelta(days=6)),
                    ("la semaine dernière", lundi - timedelta(days=7), lundi - timedelta(days=1))]
    resultats = []
    for libelle, debut, fin in periodes:
        chiffres = calculer_bilan(await get_reservations(ctx.token, date_debut=debut.isoformat(), date_fin=fin.isoformat(),
                                                         archivees=True))
        resultats.append((libelle, debut, fin, chiffres))
    lignes = []
    for libelle, debut, fin, c in resultats:
        detail = f" ({_detail_statuts(c)})" if c["total"] else ""
        lignes.append(f"- {libelle[:1].upper()}{libelle[1:]}, {_intervalle(debut, fin)} : {accorder(c['total'], 'réservation')}{detail}")
    ecart = resultats[0][3]["total"] - resultats[1][3]["total"]
    conclusion = ("Autant de réservations sur les deux périodes." if ecart == 0 else
                  f"Soit {accorder(abs(ecart), 'réservation')} de {'plus' if ecart > 0 else 'moins'} {resultats[0][0]}.")
    return ChatResponse(type="statistics", intention="bilan", reponse="\n".join(["Comparaison de vos réservations :", *lignes, conclusion]),
                        data={libelle: {"debut": d.isoformat(), "fin": f.isoformat(), **c} for libelle, d, f, c in resultats})


async def _classement(ctx: Contexte) -> ChatResponse:
    """« Quels équipements ai-je le plus utilisés ? » : sur toutes les réservations enregistrées."""
    reservations = await get_reservations(ctx.token, archivees=True)
    utilisees = [r for r in reservations if r["statut"] in STATUTS_UTILISATION]
    if not utilisees:
        return ChatResponse(type="statistics", intention="bilan", data=[],
                            reponse="Aucune de vos réservations n'a encore été validée : pas de classement possible.")
    compte, heures = Counter(), Counter()
    for r in utilisees:
        for nom in r.get("equipements_noms") or []:
            compte[nom] += 1
            heures[nom] += _duree_heures(r)
    premier_jour = min(r["date"] for r in utilisees)
    lignes = [f"- {nom} : {accorder(n, 'réservation')}, {_heures(round(heures[nom], 1))}" for nom, n in compte.most_common(5)]
    return ChatResponse(
        type="statistics", intention="bilan",
        reponse="\n".join([f"Vos équipements les plus utilisés depuis le {formater_date_absolue(premier_jour)} "
                            "(ensemble de vos réservations validées ou terminées) :", *lignes]),
        data=[{"equipement": nom, "reservations": n, "heures": round(heures[nom], 1)} for nom, n in compte.most_common(5)])


# ---------------------------------------------------------------------------
# « Ma réservation est-elle vraiment enregistrée ? » / file des validateurs
# ---------------------------------------------------------------------------

async def etat_enregistrement(ctx: Contexte) -> ChatResponse:
    """Hors confirmation (celle-ci est traitée par son étape) : la réponse vient de Django."""
    derniere = ctx.session["memoire"].get("derniere_reservation")
    actions = actions_pages(ctx.user.role, "mes_reservations")
    if derniere:
        du_jour = await get_reservations(ctx.token, date_debut=derniere["date"], date_fin=derniere["date"], archivees=True)
        trouvee = next((r for r in du_jour if r["id"] == derniere["id"]), None)
        if trouvee:
            return ChatResponse(type="reservations", actions=actions, data=[_donnees(trouvee)],
                                reponse=f"Oui : votre réservation {formater_date(trouvee['date'])} "
                                        f"{formater_plage(trouvee['heure_debut'], trouvee['heure_fin'])} est bien enregistrée "
                                        f"sur la plateforme (statut : {LABELS_STATUT_RESERVATION.get(trouvee['statut'], trouvee['statut'])}).")
        return ChatResponse(type="reservations", actions=actions, data=[],
                            reponse="Je ne retrouve pas cette réservation sur la plateforme : elle n'est pas enregistrée.")
    actives = reservations_actives_a_venir(await get_reservations(ctx.token, a_venir=True))
    reponse = ("Une réservation n'est enregistrée qu'après votre « oui » et l'accord de la plateforme ; une proposition "
               "en attente de confirmation ne l'est pas. Je n'ai aucune proposition en cours avec vous.")
    if actives:
        reponse += " Vos réservations enregistrées à venir : " + "; ".join(resume_court(r) for r in actives[:5]) + "."
    return ChatResponse(type="reservations", reponse=reponse, actions=actions, data=[_donnees(r) for r in actives[:5]])


def resume_court(r: dict) -> str:
    quoi = ", ".join(r.get("equipements_noms") or []) or r.get("laboratoire_nom", "")
    return (f"{formater_date(r['date'])} {formater_plage(r['heure_debut'], r['heure_fin'])} · {quoi} "
            f"({LABELS_STATUT_RESERVATION.get(r['statut'], r['statut'])})")


async def file_validation(ctx: Contexte) -> ChatResponse:
    """
    Demandes qui attendent une décision. Validateurs : la file de Django
    (/reservations/file_attente/, déjà filtrée selon leurs droits). Autres
    rôles : aucun accès à cette file, seulement leurs propres demandes.
    """
    role = ctx.user.role
    if role not in ROLES_SUPERVISEURS:
        en_attente = reservations_actives_a_venir(await get_reservations(ctx.token, statut="EN_ATTENTE"))
        reponse = ("Seuls les validateurs (encadrant, technicien ou administrateur) statuent sur les demandes : vous "
                   "n'avez pas accès à leur file. ")
        reponse += ("Vos demandes en attente de décision : " + "; ".join(resume_court(r) for r in en_attente) + "."
                    if en_attente else "Aucune de vos demandes n'est en attente de décision.")
        return ChatResponse(type="reservations", intention="file_validation", reponse=reponse,
                            data=[_donnees(r) for r in en_attente], actions=actions_pages(role, "mes_reservations"))
    file = await get_file_attente(ctx.token)
    actions = actions_pages(role, "reservations_a_valider")
    if not file:
        return ChatResponse(type="reservations", intention="file_validation", data=[], actions=actions,
                            reponse="Aucune demande n'attend votre décision.")
    lignes = [f"{accorder(len(file), 'demande attend', 'demandes attendent')} votre décision :"]
    lignes += [f"- {resume_court(r).rsplit(' (', 1)[0]} · {r.get('demandeur_nom', '')}" for r in file[:NOMBRE_MAX_DEMANDES_AFFICHEES]]
    return ChatResponse(type="reservations", intention="file_validation", reponse="\n".join(lignes), actions=actions,
                        data=[_donnees(r) for r in file[:NOMBRE_MAX_DEMANDES_AFFICHEES]])
