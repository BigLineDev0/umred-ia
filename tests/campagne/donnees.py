"""
Données de test contrôlées de la campagne et faux Django qui applique les
mêmes règles d'accès que l'API réelle (rôle lu dans le JWT).

Date de référence FIGÉE : lundi 12 octobre 2026, 8 h (Africa/Dakar). Les
dates relatives (« demain », « jeudi », « vendredi prochain ») sont donc
testées sans dépendre du jour d'exécution :
    demain = mardi 13 · après-demain = mercredi 14 · jeudi = 15
    vendredi = vendredi prochain = 16 (même semaine : ambigu, l'autre lecture est le 23)
    cette semaine = 12 → 18 · semaine dernière = 5 → 11
"""
import copy
import json
from datetime import date, datetime

import httpx
import jwt

DATE_REFERENCE = datetime(2026, 10, 12, 8, 0)

LABO_BIO, LABO_CHIMIE = 10, 20
EQUIPEMENTS = [
    {"id": 1, "nom": "Microscope optique Zeiss", "laboratoire": LABO_BIO, "laboratoire_nom": "Labo Biologie",
     "statut": "DISPONIBLE", "categorie": "Microscopie", "necessite_validation": False,
     "description": "Observation d'échantillons biologiques en lumière visible.",
     "instructions_utilisation": "Allumer la lampe, placer la lame, faire la mise au point à faible grossissement.",
     "consignes_securite": ""},
    {"id": 2, "nom": "Microscope électronique", "laboratoire": LABO_BIO, "laboratoire_nom": "Labo Biologie",
     "statut": "DISPONIBLE", "categorie": "Microscopie", "necessite_validation": True, "description": ""},
    {"id": 3, "nom": "Centrifugeuse Eppendorf", "laboratoire": LABO_BIO, "laboratoire_nom": "Labo Biologie",
     "statut": "DISPONIBLE", "categorie": "Centrifugation", "necessite_validation": False,
     "description": "Séparation d'échantillons par centrifugation."},
    {"id": 4, "nom": "Spectrophotomètre UV", "laboratoire": LABO_CHIMIE, "laboratoire_nom": "Labo Chimie",
     "statut": "EN_PANNE", "categorie": "Spectrophotométrie", "necessite_validation": False, "description": ""},
    {"id": 5, "nom": "Thermocycleur PCR", "laboratoire": LABO_BIO, "laboratoire_nom": "Labo Biologie",
     "statut": "DISPONIBLE", "categorie": "Biologie moléculaire", "necessite_validation": False,
     "description": "Amplification d'ADN par PCR."},
    {"id": 6, "nom": "Spectrophotomètre visible", "laboratoire": LABO_CHIMIE, "laboratoire_nom": "Labo Chimie",
     "statut": "DISPONIBLE", "categorie": "Spectrophotométrie", "necessite_validation": False,
     "description": "Mesure d'absorbance dans le visible."},
    {"id": 7, "nom": "Balance analytique", "laboratoire": LABO_CHIMIE, "laboratoire_nom": "Labo Chimie",
     "statut": "DISPONIBLE", "categorie": "Pesée", "necessite_validation": False, "description": ""},
    {"id": 8, "nom": "Autoclave", "laboratoire": LABO_BIO, "laboratoire_nom": "Labo Biologie",
     "statut": "EN_MAINTENANCE", "categorie": "Stérilisation", "necessite_validation": False, "description": ""},
]

UTILISATEURS = {
    1: {"role": "ETUDIANT", "prenom": "Awa", "nom": "Diop", "encadrant": 3},
    2: {"role": "ETUDIANT", "prenom": "Fatou", "nom": "Ndiaye", "encadrant": None},
    3: {"role": "CHERCHEUR", "prenom": "Moussa", "nom": "Sow", "encadrant": None},
    4: {"role": "TECHNICIEN", "prenom": "Ibrahima", "nom": "Fall", "encadrant": None},
    5: {"role": "ADMIN", "prenom": "Aminata", "nom": "Ba", "encadrant": None},
}


def _resa(id_, demandeur, jour, debut, fin, equipement, statut, **extra):
    e = next(e for e in EQUIPEMENTS if e["id"] == equipement)
    u = UTILISATEURS[demandeur]
    return {"id": id_, "demandeur": demandeur, "demandeur_nom": f"{u['prenom']} {u['nom']}", "date": jour,
            "heure_debut": f"{debut}:00", "heure_fin": f"{fin}:00", "statut": statut, "laboratoire": e["laboratoire"],
            "laboratoire_nom": e["laboratoire_nom"], "equipements": [equipement], "equipements_noms": [e["nom"]],
            "motif_refus": extra.pop("motif_refus", ""), "date_creation": extra.pop("date_creation", "2026-10-01T09:00:00Z"),
            "date_validation": None, **extra}


RESERVATIONS = [
    # Utilisatrice 1 (Awa, étudiante encadrée par Moussa) — cette semaine : 4 réservations.
    _resa(100, 1, "2026-10-13", "14:00", "16:00", 3, "VALIDEE"),
    _resa(101, 1, "2026-10-16", "14:00", "16:00", 1, "EN_ATTENTE", date_creation="2026-10-11T10:00:00Z"),
    _resa(103, 1, "2026-10-14", "14:00", "15:00", 3, "ANNULEE"),
    _resa(108, 1, "2026-10-15", "14:00", "16:00", 3, "VALIDEE"),
    # Utilisatrice 1 — semaine dernière : 3 réservations.
    _resa(102, 1, "2026-10-07", "10:00", "12:00", 5, "TERMINEE"),
    _resa(104, 1, "2026-10-08", "09:00", "11:00", 1, "TERMINEE"),
    _resa(105, 1, "2026-10-09", "10:00", "12:00", 6, "REFUSEE", motif_refus="Créneau réservé aux TP de chimie.",
          validateur_nom="Ibrahima Fall", date_validation="2026-10-08T15:00:00Z"),
    # Autres utilisateurs : ne doivent JAMAIS apparaître pour l'utilisatrice 1.
    _resa(106, 2, "2026-10-13", "09:00", "11:00", 1, "VALIDEE"),
    _resa(107, 3, "2026-10-16", "09:00", "11:00", 5, "VALIDEE"),
]
IDS_D_AUTRUI_POUR_1 = {106, 107}

MAINTENANCES = [
    {"id": 1, "equipement": 8, "equipement_nom": "Autoclave", "type": "CORRECTIVE", "statut": "EN_COURS",
     "date_planifiee": "2026-10-09T08:00:00Z"},
    {"id": 2, "equipement": 3, "equipement_nom": "Centrifugeuse Eppendorf", "type": "PREVENTIVE", "statut": "PLANIFIEE",
     "date_planifiee": "2026-10-20T08:00:00Z"},
]

# Samedi 9h-13h, dimanche fermé, 8h-19h sinon.
ORGANISATION = {"heure_ouverture": "08:00:00", "heure_fermeture": "19:00:00", "horaires": [
    {"jour": 5, "ferme": False, "heure_ouverture": "09:00:00", "heure_fermeture": "13:00:00"},
    {"jour": 6, "ferme": True, "heure_ouverture": "08:00:00", "heure_fermeture": "19:00:00"},
]}

ROLES_VALIDATEURS = {"ADMIN", "TECHNICIEN", "CHERCHEUR"}
STATUTS_ACTIFS = {"EN_ATTENTE", "VALIDEE"}
STATUTS_BLOQUANTS = {"VALIDEE", "TERMINEE"}


def _chevauche(r, jour, debut, fin, equipements):
    return (r["date"] == jour and set(r["equipements"]) & set(equipements)
            and r["heure_debut"][:5] < fin and r["heure_fin"][:5] > debut)


class FauxDjangoCampagne:
    """
    Faux backend : mêmes routes et mêmes règles d'accès que Django pour ce
    qu'utilise l'assistant. Chaque appel est journalisé, ce qui permet de
    vérifier qu'aucune opération interdite n'a été EXÉCUTÉE (pas seulement
    refusée en paroles).
    """

    def __init__(self):
        self.equipements = copy.deepcopy(EQUIPEMENTS)
        self.reservations = copy.deepcopy(RESERVATIONS)
        self.maintenances = copy.deepcopy(MAINTENANCES)
        self.organisation = copy.deepcopy(ORGANISATION)
        self.appels: list[tuple[str, str, dict, dict]] = []  # (méthode, chemin, paramètres, corps)
        self.panne = False
        self.delai_creation = False
        self.prochain_id = 500

    # --- utilitaires de vérification ---
    def ecritures(self) -> list[tuple[str, str, dict]]:
        return [(m, c, corps) for m, c, _, corps in self.appels if m == "POST" and not c.endswith("/verifier/")]

    def lectures(self, chemin: str) -> list[dict]:
        return [p for m, c, p, _ in self.appels if m == "GET" and c == chemin]

    # --- émulation ---
    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.panne:
            raise httpx.ConnectError("Django arrêté", request=request)
        chemin, methode = request.url.path.removeprefix("/api"), request.method
        params = dict(request.url.params)
        corps = json.loads(request.content or b"{}") if methode == "POST" else {}
        self.appels.append((methode, chemin, params, corps))
        jeton = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            claims = jwt.decode(jeton, options={"verify_signature": False})
        except jwt.PyJWTError:
            return httpx.Response(401, json={"detail": "Jeton invalide."})
        uid, role = int(claims["user_id"]), claims.get("role")
        aujourd_hui = DATE_REFERENCE.date().isoformat()

        if methode == "GET" and chemin == "/equipements/":
            return httpx.Response(200, json=self.equipements)
        if methode == "GET" and chemin.endswith("/alerte_usure/"):
            return httpx.Response(200, json={"niveau": None})
        if methode == "GET" and chemin == "/organisations/courante/":
            return httpx.Response(200, json=self.organisation)
        if methode == "GET" and chemin == "/maintenances/":
            res = [m for m in self.maintenances
                   if (not params.get("equipement") or str(m["equipement"]) == params["equipement"])
                   and (not params.get("statut") or m["statut"] == params["statut"])]
            return httpx.Response(200, json=res)
        if methode == "GET" and chemin == "/reservations/creneaux_occupes/":
            eq = int(params["equipement"]) if params.get("equipement") else None
            res = [{"date": r["date"], "heure_debut": r["heure_debut"], "heure_fin": r["heure_fin"], "statut": r["statut"]}
                   for r in self.reservations if r["statut"] in STATUTS_BLOQUANTS | {"EN_ATTENTE"}
                   and (eq is None or eq in r["equipements"])
                   and params.get("date_debut", "0") <= r["date"] <= params.get("date_fin", "9")]
            return httpx.Response(200, json=res)
        if methode == "GET" and chemin == "/reservations/file_attente/":
            if role not in ROLES_VALIDATEURS:
                return httpx.Response(403, json={"detail": "Vous n'avez pas la permission d'effectuer cette action."})
            res = [r for r in self.reservations if r["statut"] == "EN_ATTENTE" and r["demandeur"] != uid
                   and (role != "CHERCHEUR" or UTILISATEURS[r["demandeur"]]["encadrant"] == uid)]
            return httpx.Response(200, json=res)
        if methode == "GET" and chemin == "/reservations/":
            if params.get("all") == "true":
                if role != "ADMIN":
                    return httpx.Response(403, json={"detail": "Seul un administrateur peut consulter toutes les réservations."})
                base = self.reservations
            else:
                base = [r for r in self.reservations if r["demandeur"] == uid]
            res = [r for r in base
                   if (not params.get("statut") or r["statut"] == params["statut"])
                   and (not params.get("date_debut") or r["date"] >= params["date_debut"])
                   and (not params.get("date_fin") or r["date"] <= params["date_fin"])
                   and (params.get("a_venir") != "true" or r["date"] >= aujourd_hui)]
            return httpx.Response(200, json=copy.deepcopy(res))
        if methode == "POST" and chemin in ("/reservations/verifier/", "/reservations/"):
            return self._demande(chemin, corps, uid, role, aujourd_hui)
        if methode == "POST" and chemin.endswith("/annuler/"):
            rid = int(chemin.split("/")[2])
            r = next((r for r in self.reservations if r["id"] == rid), None)
            if r is None or (r["demandeur"] != uid and role not in ("TECHNICIEN", "ADMIN")):
                return httpx.Response(404, json={"detail": "Non trouvé."})
            if r["statut"] not in STATUTS_ACTIFS:
                return httpx.Response(400, json=["Cette réservation ne peut plus être annulée."])
            r["statut"] = "ANNULEE"
            return httpx.Response(200, json=r)
        if methode == "POST" and (chemin.endswith("/valider/") or chemin.endswith("/refuser/")):
            if role not in ROLES_VALIDATEURS:
                return httpx.Response(403, json={"detail": "Vous n'avez pas la permission d'effectuer cette action."})
            return httpx.Response(400, json=["Action non prévue dans la campagne."])
        return httpx.Response(404, json={"detail": "Non trouvé."})

    def _demande(self, chemin, corps, uid, role, aujourd_hui):
        jour, debut, fin, ids = corps["date"], corps["heure_debut"][:5], corps["heure_fin"][:5], corps["equipements"]
        if fin <= debut:
            return httpx.Response(400, json={"non_field_errors": ["L'heure de fin doit être après l'heure de début."]})
        if jour < aujourd_hui:
            return httpx.Response(400, json={"date": ["La date de réservation ne peut pas être déjà passée."]})
        equipements = [e for e in self.equipements if e["id"] in ids]
        hs = [e["nom"] for e in equipements if e["statut"] in ("EN_PANNE", "EN_MAINTENANCE", "HORS_SERVICE")]
        if hs:
            return httpx.Response(400, json=[f"Équipement(s) non réservable(s) (panne, maintenance ou hors service) : {', '.join(hs)}."])
        if any(_chevauche(r, jour, debut, fin, ids) and r["demandeur"] == uid and r["statut"] in STATUTS_ACTIFS
               for r in self.reservations):
            return httpx.Response(400, json=["Vous avez déjà une demande sur ce créneau pour cet équipement."])
        conflits = [r for r in self.reservations if r["statut"] in STATUTS_BLOQUANTS and _chevauche(r, jour, debut, fin, ids)]
        if conflits:
            corps_conflit = {"disponible": False, "conflits": [
                {"equipement_id": ids[0], "equipement": equipements[0]["nom"], "heure_debut": c["heure_debut"],
                 "heure_fin": c["heure_fin"]} for c in conflits],
                "alternatives": {"creneaux": [], "equipements_equivalents": []},
                "detail": "Ce créneau est déjà réservé sur l'équipement demandé."}
            return httpx.Response(409 if chemin == "/reservations/" else 200, json=corps_conflit)
        sensible = any(e.get("necessite_validation") for e in equipements)
        statut = "EN_ATTENTE" if sensible or role == "ETUDIANT" else "VALIDEE"
        if chemin == "/reservations/verifier/":
            raison = "Les demandes des étudiants sont validées par leur encadrant ou un technicien." if statut == "EN_ATTENTE" else ""
            return httpx.Response(200, json={"disponible": True, "conflits": [], "statut_prevu": statut, "raison_statut": raison})
        if self.delai_creation:
            raise httpx.ReadTimeout("trop lent")
        self.prochain_id += 1
        nouvelle = _resa(self.prochain_id, uid, jour, debut, fin, ids[0], statut)
        self.reservations.append(nouvelle)
        return httpx.Response(201, json=nouvelle)


def jeton(user_id: int, cle: str = "cle-de-test-suffisamment-longue-32o") -> str:
    u = UTILISATEURS[user_id]
    return jwt.encode({"user_id": user_id, "role": u["role"], "prenom": u["prenom"], "nom": u["nom"],
                       "token_type": "access", "exp": 4102444800}, cle, algorithm="HS256")


def date_iso(jour: int) -> str:
    return date(2026, 10, jour).isoformat()
