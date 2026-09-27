from app.nlu.texte import contient_un, normaliser_texte

# Mots-clés en minuscules et SANS accents : le message est normalisé de
# la même façon avant comparaison (l'ancienne version comparait
# "UMRED" à un message en minuscules, ce qui ne pouvait jamais matcher).
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
