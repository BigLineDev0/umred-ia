import re

from app.nlu.texte import contient_un, normaliser_texte

# Mots-clés en minuscules et sans accents : le message est normalisé de la
# même façon avant comparaison.
MOTS_CLES_DOMAINE = (
    "equipement", "laboratoire", "labo", "reserv", "maintenance", "panne",
    "dispo", "planifi", "creneau", "umred", "chercheur", "etudiant", "technicien",
)


def concerne_le_labo(message: str) -> bool:
    """
    Le message parle du laboratoire mais n'a été reconnu comme aucune
    commande : plutôt que de laisser le modèle improviser une réponse (et
    risquer d'inventer une procédure), on affiche l'aide.
    """
    return contient_un(normaliser_texte(message), MOTS_CLES_DOMAINE)


# Vocabulaire plus large, pour décider s'il vaut la peine de consulter le
# modèle de langage : en plus du laboratoire, les rubriques et verbes de
# gestion de la plateforme (« je voudrais m'occuper des comptes »).
MOTS_PLATEFORME = MOTS_CLES_DOMAINE + (
    "senlab", "plateforme", "utilisateur", "compte", "rapport", "journal", "page", "gerer", "gestion", "occuper",
    "consommable", "stock", "projet", "notification", "profil", "tableau de bord", "dashboard", "valid", "demande",
    "parametr", "planning", "agenda", "seance", "manip", "analyse", "experience", "tp ", "prevu", "resa",
)


# Le message parle de l'utilisateur (« résume-moi mon activité », « j'ai
# cassé la lampe ») : il vise probablement la plateforme. Mesuré sur le
# corpus de contrôle : sans ce critère, de vraies demandes recevaient la
# réponse « hors périmètre ». Une question impersonnelle (« quelle est la
# capitale du Japon ? ») reste écartée sans appel au modèle.
# Le message s'adresse aussi à l'assistant (« tu fais quoi ? », « pouvez-vous… ») : idem.
_PREMIERE_PERSONNE = re.compile(
    r"(?:^|[\s,.;:!?'-])(?:je|j|me|m|moi|mon|ma|mes|nous|nos|notre|tu|t|toi|te|vous)(?=[\s,.;:!?'-]|$)"
)


def peut_concerner_la_plateforme(message: str) -> bool:
    texte = normaliser_texte(message)
    return contient_un(texte, MOTS_PLATEFORME) or _PREMIERE_PERSONNE.search(texte) is not None
