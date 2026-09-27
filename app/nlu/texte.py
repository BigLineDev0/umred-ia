import unicodedata


def sans_accents(texte: str) -> str:
    """
    « Réservé » -> « Reserve ». Décomposition Unicode (NFD) : chaque lettre
    accentuée devient lettre + accent combinant, puis on retire les accents
    (catégorie « Mn »). Indispensable car les utilisateurs tapent souvent
    sans accents (« reserver », « equipement »).
    """
    decompose = unicodedata.normalize("NFD", texte)
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


def normaliser_texte(texte: str) -> str:
    """Forme canonique pour toutes les comparaisons par mots-clés."""
    return sans_accents(texte).lower().replace("’", "'").strip()


def contient_un(texte_normalise: str, mots_cles: list[str] | tuple[str, ...] | set[str]) -> bool:
    return any(mot in texte_normalise for mot in mots_cles)
