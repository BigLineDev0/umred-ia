"""
Les tests tournent SANS modèle de langage et SANS Django :
- le modèle n'est jamais chargé -> l'extraction passe par les règles ;
- Django est simulé par un faux backend branché sur le client httpx
  (httpx.MockTransport), ce qui teste aussi le vrai code du client.
"""
import os
import time
from datetime import timedelta

os.environ.setdefault("DJANGO_API_URL", "http://django.test/api")
os.environ.setdefault("JWT_SECRET_KEY", "cle-de-test-suffisamment-longue-32o")

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit, session_store  # noqa: E402
from app.core.temps import aujourd_hui  # noqa: E402
from app.services import django_client  # noqa: E402
from main import app  # noqa: E402

AUJOURD_HUI = aujourd_hui()
DEMAIN = (AUJOURD_HUI + timedelta(days=1)).isoformat()
HIER = (AUJOURD_HUI - timedelta(days=1)).isoformat()
DANS_3_JOURS = (AUJOURD_HUI + timedelta(days=3)).isoformat()


def fabriquer_token(user_id=1, role="CHERCHEUR", prenom="Awa", nom="Diop", token_type="access", expire_dans=300, cle="cle-de-test-suffisamment-longue-32o"):
    payload = {"user_id": user_id, "role": role, "prenom": prenom, "nom": nom,
               "token_type": token_type, "exp": int(time.time()) + expire_dans}
    return jwt.encode(payload, cle, algorithm="HS256")


class FauxDjango:
    """Backend Django en mémoire, avec un journal des appels reçus."""

    def __init__(self):
        self.equipements = [
            {"id": 1, "nom": "Microscope optique Zeiss", "laboratoire": 10, "laboratoire_nom": "Labo Biologie", "statut": "DISPONIBLE"},
            {"id": 2, "nom": "Microscope électronique", "laboratoire": 10, "laboratoire_nom": "Labo Biologie", "statut": "DISPONIBLE"},
            {"id": 3, "nom": "Centrifugeuse Eppendorf", "laboratoire": 10, "laboratoire_nom": "Labo Biologie", "statut": "DISPONIBLE"},
            {"id": 4, "nom": "Spectrophotomètre UV", "laboratoire": 20, "laboratoire_nom": "Labo Chimie", "statut": "EN_PANNE"},
            {"id": 5, "nom": "Thermocycleur PCR", "laboratoire": 10, "laboratoire_nom": "Labo Biologie", "statut": "DISPONIBLE"},
        ]
        self.reservations = [
            {"id": 100, "date": DEMAIN, "heure_debut": "09:00:00", "heure_fin": "11:00:00", "statut": "VALIDEE",
             "laboratoire_nom": "Labo Biologie", "equipements": [3], "equipements_noms": ["Centrifugeuse Eppendorf"]},
            # Passée : sert à « le même équipement que la dernière fois ».
            {"id": 90, "date": HIER, "heure_debut": "14:00:00", "heure_fin": "16:00:00", "statut": "TERMINEE",
             "laboratoire_nom": "Labo Biologie", "equipements": [5], "equipements_noms": ["Thermocycleur PCR"]},
            {"id": 95, "date": DANS_3_JOURS, "heure_debut": "10:00:00", "heure_fin": "12:00:00", "statut": "REFUSEE",
             "motif_refus": "Le microscope est réservé à un TP ce jour-là.", "validateur_nom": "Moussa Ndiaye",
             "date_validation": f"{HIER}T10:00:00", "laboratoire_nom": "Labo Biologie",
             "equipements": [1], "equipements_noms": ["Microscope optique Zeiss"]},
        ]
        self.creneaux_occupes: list[dict] = []
        self.organisation = {"heure_ouverture": "08:00:00", "heure_fermeture": "19:00:00", "horaires": []}
        self.reponse_creation = (201, {"id": 200, "statut": "VALIDEE"})
        self.reponse_verification = (200, {"disponible": True, "conflits": [], "statut_prevu": "VALIDEE", "raison_statut": ""})
        self.reponse_annulation = (200, {"statut": "ANNULEE"})
        self.appels: list[tuple[str, str, bytes]] = []
        self.appels_detailles: list[tuple[str, str, httpx.QueryParams]] = []
        self.panne = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.panne:
            raise httpx.ConnectError("Django arrêté", request=request)
        chemin, methode = request.url.path.removeprefix("/api"), request.method
        self.appels.append((methode, chemin, request.content))
        self.appels_detailles.append((methode, chemin, request.url.params))

        if methode == "GET" and chemin == "/equipements/":
            return httpx.Response(200, json=self.equipements)
        if methode == "GET" and chemin.endswith("/alerte_usure/"):
            return httpx.Response(200, json={"niveau": None})
        if methode == "GET" and chemin == "/reservations/creneaux_occupes/":
            return httpx.Response(200, json=self.creneaux_occupes)
        if methode == "GET" and chemin == "/reservations/":
            # Mêmes filtres que ReservationViewSet.get_queryset.
            params = request.url.params
            if params.get("all") == "true":
                return httpx.Response(403, json={"detail": "Seul un administrateur peut consulter toutes les réservations."})
            resultat = [r for r in self.reservations
                        if (not params.get("statut") or r["statut"] == params["statut"])
                        and (not params.get("date_debut") or r["date"] >= params["date_debut"])
                        and (not params.get("date_fin") or r["date"] <= params["date_fin"])]
            return httpx.Response(200, json=resultat)
        if methode == "POST" and chemin == "/reservations/verifier/":
            code, corps = self.reponse_verification
            return httpx.Response(code, json=corps)
        if methode == "POST" and chemin == "/reservations/":
            code, corps = self.reponse_creation
            return httpx.Response(code, json=corps)
        if methode == "POST" and chemin.endswith("/annuler/"):
            code, corps = self.reponse_annulation
            return httpx.Response(code, json=corps)
        if methode == "GET" and chemin == "/organisations/courante/":
            return httpx.Response(200, json=self.organisation)
        if methode == "GET" and chemin == "/maintenances/":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": "Non trouvé."})

    def appels_post(self, chemin: str) -> list[bytes]:
        return [corps for m, c, corps in self.appels if m == "POST" and c == chemin]

    def appels_get(self, chemin: str) -> list[dict]:
        return [dict(params) for m, c, params in self.appels_detailles if m == "GET" and c == chemin]


@pytest.fixture
def django():
    faux = FauxDjango()
    django_client._client = httpx.AsyncClient(base_url="http://django.test/api", transport=httpx.MockTransport(faux))
    session_store.reinitialiser_sessions()
    rate_limit._requetes.clear()
    yield faux
    django_client._client = None


@pytest.fixture
def client():
    # Sans « with TestClient(...) », le lifespan ne s'exécute pas : le
    # modèle de langage n'est donc pas chargé (mode règles seules).
    return TestClient(app)


@pytest.fixture
def envoyer(client, django):
    """envoyer("message") -> réponse JSON, pour l'utilisateur 1 et une session fixe."""

    def _envoyer(message, token=None, session_id="session-test-1", role=None):
        token = token or (fabriquer_token(role=role) if role else None)
        resp = client.post(
            "/api/chat",
            json={"session_id": session_id, "message": message},
            headers={"Authorization": f"Bearer {token or fabriquer_token()}"},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    return _envoyer
