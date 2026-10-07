"""
Catalogue des pages de l'application Angular vers lesquelles l'assistant
peut orienter l'utilisateur.

Chaque entrée reprend une route qui EXISTE dans le frontend
(app.routes.ts et Features/*/*.routes.ts) avec les rôles de son
roleGuard — eux-mêmes alignés sur les permissions Django. Deux garanties :
- aucune route n'est inventée : le modèle de langage ne produit jamais de
  route, il ne fait que reconnaître une intention ; la page est choisie ici,
  par des règles ;
- une page n'est proposée qu'aux rôles qui peuvent l'ouvrir. Cela ne
  remplace pas les contrôles de Django : c'est un confort d'interface, les
  données de la page restent protégées par l'API.

Ajouter une page = ajouter une entrée ici.
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.core.constantes import Role

GESTION = frozenset({Role.ADMIN, Role.TECHNICIEN})
ADMIN = frozenset({Role.ADMIN})

VERBES_CREATION = ("ajout", "creer", "cree ", "nouvel", "nouveau", "nouvelle", "enregistrer", "declarer")
VERBES_CONSULTATION = ("gerer", "gestion", "liste", "voir", "consulter", "page", "acceder", "afficher", "ouvrir",
                       "aller", "parcourir", "catalogue")
# Une question sur la disponibilité, une statistique ou une réservation
# n'est pas une demande de navigation, même si elle cite « les équipements ».
EXCLUSIONS_LISTES = ("dispo", "libre", "reserv", "combien", "nombre de", "statistique")
EXCLUSIONS_PERSONNELLES = ("mes ", "ma ", "mon ")


@lru_cache(maxsize=None)
def _motif(termes: tuple[str, ...]) -> re.Pattern:
    # Chaque terme doit commencer un mot : « voir » ne doit pas être trouvé
    # dans « avoir » ou « pouvoir », ni « labo » dans « collaborer ».
    return re.compile("|".join(rf"(?<![a-z0-9]){re.escape(t)}" for t in termes))


def _cite(message_normalise: str, termes: tuple[str, ...]) -> bool:
    return bool(termes) and _motif(termes).search(message_normalise) is not None


@dataclass(frozen=True)
class Page:
    cle: str
    label: str
    # Route unique, ou une route par rôle (tableaux de bord, « mes réservations »).
    route: str | dict[str, str]
    message: str
    # None : toute personne connectée peut ouvrir la page.
    roles: frozenset[str] | None = None
    # La page correspond si le message cite un des OBJETS et, quand la liste
    # n'est pas vide, un des VERBES ; et aucune EXCLUSION.
    objets: tuple[str, ...] = ()
    verbes: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    # Page proposée à la place si le rôle n'a pas accès à celle-ci.
    alternative: str | None = None

    def correspond(self, message_normalise: str) -> bool:
        return (
            bool(self.objets)
            and _cite(message_normalise, self.objets)
            and (not self.verbes or _cite(message_normalise, self.verbes))
            and not _cite(message_normalise, self.exclusions)
        )

    def autorisee(self, role: str | None) -> bool:
        if self.roles is not None and role not in self.roles:
            return False
        return self.route_pour(role) is not None

    def route_pour(self, role: str | None) -> str | None:
        if isinstance(self.route, dict):
            return self.route.get(role or "")
        return self.route


# L'ORDRE COMPTE : la première page qui correspond gagne, les plus
# spécifiques (« ajouter un équipement ») passent avant les listes.
PAGES: list[Page] = [
    Page("equipement_ajouter", "Ajouter un équipement", "/equipements/ajouter",
         "Vous pouvez ajouter un nouvel équipement depuis cette page.",
         roles=GESTION, objets=("equipement",), verbes=VERBES_CREATION, alternative="equipements"),
    Page("laboratoire_ajouter", "Ajouter un laboratoire", "/laboratoires/ajouter",
         "Vous pouvez créer un nouveau laboratoire depuis cette page.",
         roles=ADMIN, objets=("laboratoire", "labo"), verbes=VERBES_CREATION, alternative="laboratoires"),
    Page("consommable_ajouter", "Ajouter un consommable", "/consommables/ajouter",
         "Vous pouvez ajouter un consommable depuis cette page.",
         roles=GESTION, objets=("consommable",), verbes=VERBES_CREATION, alternative="consommables"),
    Page("reservation_formulaire", "Créer une réservation", "/reservations/ajouter",
         "Voici le formulaire de réservation. Vous pouvez aussi me demander directement, par exemple "
         "« Réserve le microscope demain de 10h à 12h ».",
         objets=("formulaire de reservation", "page de reservation", "formulaire de demande")),
    Page("reservations_a_valider", "Réservations à valider", "/reservations/a-valider",
         "Voici les demandes de réservation à traiter.",
         roles=frozenset({Role.ADMIN, Role.TECHNICIEN, Role.CHERCHEUR}),
         objets=("reservations", "demandes"), verbes=("valider", "traiter", "gerer", "gestion", "en attente", "a valider"),
         exclusions=EXCLUSIONS_PERSONNELLES + ("annul",), alternative="mes_reservations"),
    Page("pannes", "Équipements en panne", "/maintenances/pannes",
         "Voici la liste des équipements signalés en panne.",
         roles=GESTION, objets=("en panne", "pannes"), verbes=VERBES_CONSULTATION + ("quels", "lesquels"),
         alternative="equipements"),
    Page("maintenances", "Maintenances", "/maintenances",
         "Voici la page de suivi des maintenances.",
         roles=GESTION, objets=("maintenances", "interventions"), verbes=VERBES_CONSULTATION + ("suivre", "toutes"),
         exclusions=("combien", "nombre de", "statistique")),
    Page("utilisateurs", "Gérer les utilisateurs", "/utilisateurs",
         "Vous pouvez gérer les comptes (invitations, rôles, activation) depuis cette page.",
         roles=ADMIN, objets=("utilisateur", "comptes", "membres"),
         verbes=VERBES_CONSULTATION + VERBES_CREATION + ("inviter",), exclusions=("combien", "nombre de")),
    Page("journal", "Journal d'activité", "/journal-activite",
         "Le journal d'activité retrace les actions effectuées sur la plateforme.",
         roles=ADMIN, objets=("journal", "historique des actions")),
    Page("rapports", "Rapports & statistiques", "/rapports",
         "Les rapports détaillés sont disponibles sur cette page.",
         roles=ADMIN, objets=("rapport",)),
    Page("pilotage", "Aide à la décision", "/pilotage",
         "La page d'aide à la décision présente les indicateurs et la synthèse de la semaine.",
         roles=GESTION, objets=("aide a la decision", "pilotage", "synthese de la semaine", "synthese hebdo")),
    Page("etablissement", "Mon établissement", "/etablissement",
         "Les paramètres de l'établissement (horaires, règles de réservation) se gèrent ici.",
         roles=ADMIN, objets=("etablissement",), verbes=VERBES_CONSULTATION + ("parametr", "configur", "modifier", "regler")),
    Page("equipements", "Voir les équipements", "/equipements",
         "Voici la liste des équipements de l'établissement.",
         objets=("equipements", "parc", "inventaire"), verbes=VERBES_CONSULTATION, exclusions=EXCLUSIONS_LISTES),
    Page("laboratoires", "Voir les laboratoires", "/laboratoires",
         "Voici la liste des laboratoires.",
         objets=("laboratoire", "labos"), verbes=VERBES_CONSULTATION, exclusions=EXCLUSIONS_LISTES),
    Page("consommables", "Voir les consommables", "/consommables",
         "Voici la liste des consommables.",
         objets=("consommables",), verbes=VERBES_CONSULTATION + ("stock",), exclusions=("combien", "nombre de")),
    Page("notifications", "Mes notifications", "/notifications",
         "Voici vos notifications.",
         objets=("notification",), verbes=VERBES_CONSULTATION + ("mes ", "lire")),
    Page("profil", "Mon profil", "/profil",
         "Vous pouvez modifier vos informations et votre mot de passe depuis votre profil.",
         objets=("profil", "mot de passe", "mes informations"), verbes=VERBES_CONSULTATION + ("modifier", "changer", "mon ")),
    Page("tableau_de_bord", "Tableau de bord", {
        Role.SUPER_ADMIN: "/plateforme", Role.ADMIN: "/admin/dashboard", Role.TECHNICIEN: "/technicien/dashboard",
        Role.CHERCHEUR: "/enseignant/dashboard", Role.ETUDIANT: "/etudiant/dashboard",
    }, "Voici votre tableau de bord.", objets=("tableau de bord", "dashboard")),
    Page("mes_reservations", "Voir mes réservations", {
        Role.ADMIN: "/admin/reservations", Role.TECHNICIEN: "/technicien/reservations",
        Role.CHERCHEUR: "/enseignant/reservations", Role.ETUDIANT: "/etudiant/mes-demandes",
    }, "Retrouvez toutes vos réservations sur cette page.",
         objets=("page mes reservations", "toutes mes reservations", "historique de mes reservations")),
]

PAGES_PAR_CLE: dict[str, Page] = {p.cle: p for p in PAGES}


@dataclass
class Destination:
    """Résultat de la recherche de page : soit une page autorisée, soit un refus motivé."""

    page: Page
    autorisee: bool
    alternative: Page | None = field(default=None)


def page_demandee(message_normalise: str) -> Page | None:
    """Première page du catalogue citée par le message, sans tenir compte du rôle."""
    return next((p for p in PAGES if p.correspond(message_normalise)), None)


def destination(message_normalise: str, role: str | None) -> Destination | None:
    page = page_demandee(message_normalise)
    if page is None:
        return None
    if page.autorisee(role):
        return Destination(page, autorisee=True)
    alternative = PAGES_PAR_CLE.get(page.alternative or "")
    return Destination(page, autorisee=False, alternative=alternative if alternative and alternative.autorisee(role) else None)


def pages_accessibles(role: str | None) -> list[Page]:
    return [p for p in PAGES if p.autorisee(role)]
