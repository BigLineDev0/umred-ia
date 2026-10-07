"""
Valeurs métier partagées avec le backend Django. Elles reprennent
exactement les TextChoices des modèles Django : les centraliser ici évite
de semer des chaînes "magiques" ("ADMIN", "VALIDEE"...) dans tout le code.
"""
from enum import StrEnum


class Role(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    TECHNICIEN = "TECHNICIEN"
    CHERCHEUR = "CHERCHEUR"
    ETUDIANT = "ETUDIANT"


LABELS_ROLE = {
    Role.SUPER_ADMIN: "super administrateur",
    Role.ADMIN: "administrateur",
    Role.TECHNICIEN: "technicien",
    Role.CHERCHEUR: "enseignant-chercheur",
    Role.ETUDIANT: "étudiant",
}
LABELS_ROLE_PLURIEL = {
    Role.SUPER_ADMIN: "super administrateurs",
    Role.ADMIN: "administrateurs",
    Role.TECHNICIEN: "techniciens",
    Role.CHERCHEUR: "enseignants-chercheurs",
    Role.ETUDIANT: "étudiants",
}

# Rôles qui valident des réservations (EstValidateur côté Django).
ROLES_SUPERVISEURS = {Role.ADMIN, Role.TECHNICIEN, Role.CHERCHEUR}
# Seul un administrateur peut lister TOUTES les réservations (?all=true) :
# Django répond 403 aux autres rôles (ReservationViewSet.get_queryset).
ROLES_VUE_GLOBALE = {Role.ADMIN}
# Rôles autorisés à créer / modifier des équipements.
ROLES_GESTION_EQUIPEMENTS = {Role.ADMIN, Role.TECHNICIEN}

# Une réservation "active" occupe réellement le créneau (même logique que
# la détection de conflit côté Django).
STATUTS_RESERVATION_ACTIFS = {"EN_ATTENTE", "VALIDEE"}
LABELS_STATUT_RESERVATION = {
    "EN_ATTENTE": "en attente de validation",
    "VALIDEE": "validée",
    "REFUSEE": "refusée",
    "ANNULEE": "annulée",
    "TERMINEE": "terminée",
}

# Miroir de STATUTS_EQUIPEMENT_NON_RESERVABLES côté Django : inutile de
# proposer un équipement que Django refusera de toute façon.
STATUTS_EQUIPEMENT_NON_RESERVABLES = {"EN_MAINTENANCE", "EN_PANNE", "HORS_SERVICE"}

STATUT_MAINTENANCE_PLANIFIEE = "PLANIFIEE"
