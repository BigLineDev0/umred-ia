"""
Données de test du Django d'INTÉGRATION. Lancé par tests/integration/conftest.py
avec le Python du backend, dans le dossier backend/, sur une base SQLite
temporaire. Refuse de s'exécuter sur toute autre base (garde-fou : jamais
sur PostgreSQL ni sur des données réelles).

Affiche en JSON les identifiants créés (utilisateurs, équipements, réservations).
"""
import json
import os
import sys
from datetime import time, timedelta

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.utils import timezone  # noqa: E402

base = settings.DATABASES["default"]
if base["ENGINE"] != "django.db.backends.sqlite3" or "senlab-integration" not in str(base["NAME"]):
    sys.exit(f"Refus : base {base['ENGINE']} {base['NAME']} — les données de test ne s'écrivent que dans une base SQLite temporaire.")

from apps.equipements.models import Equipement, StatutEquipement  # noqa: E402
from apps.laboratoires.models import Laboratoire  # noqa: E402
from apps.organisations.models import Organisation  # noqa: E402
from apps.reservations.models import Reservation, StatutReservation  # noqa: E402
from apps.utilisateurs.models import Role, Utilisateur  # noqa: E402

MOT_DE_PASSE = os.environ["SENLAB_MOT_DE_PASSE_TEST"]

org = Organisation.objects.create(nom="Université de test", slug="universite-test")
bio = Laboratoire.objects.create(nom="Labo Biologie", localisation="Bât. A", organisation=org)
chimie = Laboratoire.objects.create(nom="Labo Chimie", localisation="Bât. B", organisation=org)


def equipement(nom, labo, serie, statut=StatutEquipement.DISPONIBLE, categorie=""):
    return Equipement.objects.create(nom=nom, laboratoire=labo, numero_serie=serie, statut=statut, categorie=categorie)


microscope = equipement("Microscope optique Zeiss", bio, "T-001", categorie="Microscopie")
centrifugeuse = equipement("Centrifugeuse Eppendorf", bio, "T-002", categorie="Centrifugation")
spectro_uv = equipement("Spectrophotomètre UV", chimie, "T-003", StatutEquipement.EN_PANNE, "Spectrophotométrie")
spectro_visible = equipement("Spectrophotomètre visible", chimie, "T-004", categorie="Spectrophotométrie")


def utilisateur(email, role, prenom, **extra):
    return Utilisateur.objects.create_user(email=email, password=MOT_DE_PASSE, nom="Test", prenom=prenom, role=role,
                                           organisation=org, **extra)


chercheur = utilisateur("chercheur@test.sn", Role.CHERCHEUR, "Moussa")
awa = utilisateur("awa@test.sn", Role.ETUDIANT, "Awa", encadrant=chercheur)
fatou = utilisateur("fatou@test.sn", Role.ETUDIANT, "Fatou")
technicien = utilisateur("technicien@test.sn", Role.TECHNICIEN, "Ibrahima")
admin = utilisateur("admin@test.sn", Role.ADMIN, "Aminata")

# Jour de test : dans 3 jours (l'établissement n'a pas d'horaires par jour : ouvert de 8h à 19h).
jour = timezone.localdate() + timedelta(days=3)


def reservation(demandeur, equip, debut, fin, statut):
    r = Reservation.objects.create(demandeur=demandeur, laboratoire=equip.laboratoire, date=jour, heure_debut=time(debut),
                                   heure_fin=time(fin), motif="Test d'intégration", statut=statut)
    r.equipements.set([equip])
    return r


r_fatou = reservation(fatou, microscope, 9, 11, StatutReservation.VALIDEE)
r_awa = reservation(awa, spectro_visible, 15, 17, StatutReservation.EN_ATTENTE)

print(json.dumps({
    "jour": jour.isoformat(),
    "utilisateurs": {"awa": "awa@test.sn", "fatou": "fatou@test.sn", "chercheur": "chercheur@test.sn",
                     "technicien": "technicien@test.sn", "admin": "admin@test.sn"},
    "equipements": {"microscope": microscope.id, "centrifugeuse": centrifugeuse.id, "spectro_uv": spectro_uv.id,
                    "spectro_visible": spectro_visible.id},
    "reservations": {"fatou": r_fatou.id, "awa": r_awa.id},
}))
