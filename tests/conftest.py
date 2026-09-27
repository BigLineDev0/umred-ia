"""
Les tests tournent SANS modèle de langage et SANS Django :
- le modèle n'est jamais chargé -> l'extraction passe par les règles ;
- Django est simulé par un faux backend branché sur le client httpx
  (httpx.MockTransport), ce qui teste aussi le vrai code du client.
"""
import os
import time
from datetime import date, timedelta

os.environ.setdefault("DJANGO_API_URL", "http://django.test/api")
os.environ.setdefault("JWT_SECRET_KEY", "cle-de-test-suffisamment-longue-32o")

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit, session_store  # noqa: E402
from app.services import django_client  # noqa: E402
from main import app  # noqa: E402

DEMAIN = (date.today() + timedelta(days=1)).isoformat()


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
        ]
        self.reservations = [
            {"id": 100, "date": DEMAIN, "heure_debut": "09:00:00", "heure_fin": "11:00:00", "statut": "VALIDEE",
             "laboratoire_nom": "Labo Biologie", "equipements_noms": ["Centrifugeuse Eppendorf"]},
        ]
        self.creneaux_occupes: list[dict] = []
        self.reponse_creation = (201, {"id": 200, "statut": "VALIDEE"})
        self.appels: list[tuple[str, str, bytes]] = []
        self.panne = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.panne:
            raise httpx.ConnectError("Django arrêté", request=request)
        chemin, methode = request.url.path.removeprefix("/api"), request.method
        self.appels.append((methode, chemin, request.content))

        if methode == "GET" and chemin == "/equipements/":
            return httpx.Response(200, json=self.equipements)
        if methode == "GET" and chemin.endswith("/alerte_usure/"):
            return httpx.Response(200, json={"niveau": None})
        if methode == "GET" and chemin == "/reservations/creneaux_occupes/":
            return httpx.Response(200, json=self.creneaux_occupes)
        if methode == "GET" and chemin == "/reservations/":
            return httpx.Response(200, json=self.reservations)
        if methode == "POST" and chemin == "/reservations/":
            code, corps = self.reponse_creation
            return httpx.Response(code, json=corps)
        if methode == "POST" and chemin.endswith("/annuler/"):
            return httpx.Response(200, json={"statut": "ANNULEE"})
        if methode == "GET" and chemin == "/maintenances/":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": "Non trouvé."})

    def appels_post(self, chemin: str) -> list[bytes]:
        return [corps for m, c, corps in self.appels if m == "POST" and c == chemin]


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

    def _envoyer(message, token=None, session_id="session-test-1"):
        resp = client.post(
            "/api/chat",
            json={"session_id": session_id, "message": message},
            headers={"Authorization": f"Bearer {token or fabriquer_token()}"},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    return _envoyer
