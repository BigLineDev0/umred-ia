"""
Tests d'INTÉGRATION avec les vraies API : un vrai Django (runserver) sur une
base SQLite temporaire et le vrai service IA (Uvicorn) qui l'appelle en HTTP.

    SENLAB_INTEGRATION=1 venv/bin/python -m pytest tests/integration -v

Isolation : clé secrète et mot de passe générés pour la session, base dans un
dossier temporaire, e-mails en mémoire (locmem), webhook n8n, S3 et Brevo
désactivés. La pile Docker et sa base PostgreSQL ne sont jamais contactées.
Le service IA tourne en mode règles (modèle volontairement introuvable) :
l'intégration teste les API et les permissions ; Qwen est évalué à part.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

RACINE_IA = Path(__file__).resolve().parents[2]
# Code du service IA à tester (défaut : ce dépôt ; une copie pour comparer deux versions).
CODE_IA = Path(os.environ.get("SENLAB_IA_CODE", RACINE_IA))
BACKEND = Path(os.environ.get("SENLAB_BACKEND", RACINE_IA.parent / "backend"))
PYTHON_BACKEND = os.environ.get("SENLAB_BACKEND_PYTHON", str(BACKEND / ".venv" / "bin" / "python"))
PYTHON_IA = sys.executable

def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _attendre(url: str, processus: subprocess.Popen, journal: Path, delai: float = 60) -> None:
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if processus.poll() is not None:
            raise RuntimeError(f"Le serveur s'est arrêté :\n{journal.read_text()[-3000:]}")
        try:
            httpx.get(url, timeout=1)
            return
        except httpx.HTTPError:
            time.sleep(0.3)
    raise RuntimeError(f"Serveur non joignable : {url}")


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    if os.environ.get("SENLAB_INTEGRATION") != "1":
        pytest.skip("tests d'intégration : lancer avec SENLAB_INTEGRATION=1")
    dossier = tmp_path_factory.mktemp("senlab-integration")
    cle = secrets.token_urlsafe(48)
    mot_de_passe = secrets.token_urlsafe(16) + "Aa1!"
    base = dossier / "senlab-integration.sqlite3"
    env_django = {
        **os.environ,
        "SECRET_KEY": cle, "DATABASE_URL": f"sqlite:///{base}", "DEBUG": "False",
        "ALLOWED_HOSTS": "127.0.0.1,localhost", "SECURE_SSL_REDIRECT": "False", "COOKIES_SECURISES": "False",
        "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend", "N8N_WEBHOOK_URL": "", "S3_BUCKET": "",
        "BREVO_API_KEY": "", "TACHES_TOKEN": "", "FRONTEND_URL": "http://localhost:4200",
        "SENLAB_MOT_DE_PASSE_TEST": mot_de_passe,
    }
    assert env_django["DATABASE_URL"].startswith("sqlite:///") and str(dossier) in env_django["DATABASE_URL"]
    subprocess.run([PYTHON_BACKEND, "manage.py", "migrate", "--noinput"], cwd=BACKEND, env=env_django, check=True,
                   capture_output=True)
    sortie = subprocess.run([PYTHON_BACKEND, str(Path(__file__).with_name("peupler_django.py"))], cwd=BACKEND,
                            env=env_django, check=True, capture_output=True, text=True).stdout
    donnees = json.loads(sortie.strip().splitlines()[-1])

    port_django, port_ia = _port_libre(), _port_libre()
    journal_django, journal_ia = dossier / "django.log", dossier / "ia.log"
    django_proc = subprocess.Popen([PYTHON_BACKEND, "manage.py", "runserver", f"127.0.0.1:{port_django}", "--noreload"],
                                   cwd=BACKEND, env=env_django, stdout=journal_django.open("w"), stderr=subprocess.STDOUT)
    env_ia = {
        **os.environ, "DJANGO_API_URL": f"http://127.0.0.1:{port_django}/api", "JWT_SECRET_KEY": cle,
        "MODEL_NAME": str(dossier / "modele-absent"), "HF_HUB_OFFLINE": "1", "RATE_LIMIT_PAR_MINUTE": "1000",
        "TACHES_TOKEN": "", "CORS_ORIGINS": '["http://localhost:4200"]',
    }
    ia_proc = subprocess.Popen([PYTHON_IA, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port_ia)],
                               cwd=CODE_IA, env=env_ia, stdout=journal_ia.open("w"), stderr=subprocess.STDOUT)
    try:
        _attendre(f"http://127.0.0.1:{port_django}/api/auth/login/", django_proc, journal_django)
        _attendre(f"http://127.0.0.1:{port_ia}/health", ia_proc, journal_ia)
        jetons = {}
        for nom, email in donnees["utilisateurs"].items():
            r = httpx.post(f"http://127.0.0.1:{port_django}/api/auth/login/", json={"email": email, "password": mot_de_passe})
            r.raise_for_status()
            jetons[nom] = r.json()["access"]
        yield {"django": f"http://127.0.0.1:{port_django}/api", "ia": f"http://127.0.0.1:{port_ia}/api",
               "jetons": jetons, "donnees": donnees, "journal_ia": journal_ia, "journal_django": journal_django,
               "arreter_django": lambda: (django_proc.terminate(), django_proc.wait(timeout=10))}
    finally:
        for p in (ia_proc, django_proc):
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=10)


class Conversation:
    """Une conversation avec le service IA réel, pour un utilisateur."""

    def __init__(self, env, utilisateur: str, session: str):
        self.env, self.utilisateur, self.session = env, utilisateur, session

    def dire(self, message: str) -> dict:
        r = httpx.post(f"{self.env['ia']}/chat", json={"session_id": self.session, "message": message},
                       headers={"Authorization": f"Bearer {self.env['jetons'][self.utilisateur]}"}, timeout=30)
        assert r.status_code == 200, r.text
        return r.json()


def django_get(env, chemin: str, utilisateur: str = "admin", **params) -> httpx.Response:
    return httpx.get(f"{env['django']}{chemin}", params=params,
                     headers={"Authorization": f"Bearer {env['jetons'][utilisateur]}"}, timeout=10)


def toutes_les_reservations(env) -> list[dict]:
    """État réel de la base, lu par l'administrateur de test."""
    r = django_get(env, "/reservations/", all="true", archivees="true")
    r.raise_for_status()
    return r.json()
