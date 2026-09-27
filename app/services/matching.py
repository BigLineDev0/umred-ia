"""
Retrouver les équipements dont parle l'utilisateur, malgré les fautes de
frappe, les accents oubliés et les formulations libres.

Outil : la distance d'édition de RapidFuzz. `partial_ratio` cherche le
meilleur alignement de la chaîne la plus courte à l'intérieur de la plus
longue, et renvoie un score de 0 à 100. Ainsi « microscop » trouve
« Microscope optique Zeiss », et « Microscope optique » est repéré dans
« réserve-moi le microscope optique demain ».
"""
import re

from rapidfuzz import fuzz

from app.nlu.texte import normaliser_texte

SEUIL_NOM_COMPLET = 88  # le nom exact (ou presque) de l'équipement est cité
SEUIL_FAMILLE = 85      # seul le type d'équipement est cité (« le microscope »)
LONGUEUR_MIN_FAMILLE = 4  # évite qu'un mot court (« pc ») matche n'importe quoi
# Mots présents dans des noms d'équipements mais trop courants dans les
# messages pour identifier quoi que ce soit.
MOTS_NON_DISTINCTIFS = {"des", "les", "pour", "avec", "sur", "laboratoire", "labo", "salle", "equipement"}


def famille(nom: str) -> str:
    """
    Regroupement par premier mot du nom : « Microscope optique » et
    « Microscope électronique » forment la famille « microscope ».
    Heuristique simple, assumée : elle suffit à distinguer des types
    d'équipements différents sans maintenir une vraie taxonomie.
    """
    return normaliser_texte(nom).split(" ")[0]


def _score(texte_normalise: str, equipement: dict) -> tuple[int, float]:
    """
    Renvoie (niveau, score) : niveau 2 = nom complet reconnu, 1 = seule la
    famille est reconnue, 0 = aucune correspondance. Le niveau permet de
    préférer « Microscope optique » (cité en entier) aux autres microscopes.
    """
    nom = normaliser_texte(equipement["nom"])
    score_nom = fuzz.partial_ratio(nom, texte_normalise)
    if score_nom >= SEUIL_NOM_COMPLET:
        return 2, score_nom
    fam = famille(nom)
    if len(fam) >= LONGUEUR_MIN_FAMILLE:
        score_famille = fuzz.partial_ratio(fam, texte_normalise)
        if score_famille >= SEUIL_FAMILLE:
            return 1, score_famille
    # Mot distinctif cité seul (« le PCR » pour « Thermocycleur PCR ») :
    # correspondance EXACTE d'un mot, pour ne pas créer de faux positifs.
    mots_texte = set(re.findall(r"[a-z0-9-]+", texte_normalise))
    mots_nom = [m for m in re.findall(r"[a-z0-9-]+", nom)[1:] if len(m) >= 3 and m not in MOTS_NON_DISTINCTIFS]
    if any(mot in mots_texte for mot in mots_nom):
        return 1, float(SEUIL_FAMILLE)
    return 0, 0.0


def equipements_mentionnes(texte: str, equipements: list[dict]) -> dict[str, list[dict]]:
    """
    Repère TOUTES les familles d'équipements mentionnées, ce qui permet de
    gérer à la fois :
    - une demande ambiguë (« le microscope » alors qu'il y en a deux) ;
    - une demande multiple (« le microscope ET la centrifugeuse »).
    Renvoie {famille: [équipements candidats]}. Dans une famille, si un
    équipement est cité par son nom complet, seuls ceux-là sont gardés.
    """
    texte_normalise = normaliser_texte(texte)
    par_famille: dict[str, list[tuple[int, dict]]] = {}
    for e in equipements:
        niveau, _ = _score(texte_normalise, e)
        if niveau:
            par_famille.setdefault(famille(e["nom"]), []).append((niveau, e))

    resultat = {}
    for fam, candidats in par_famille.items():
        meilleur_niveau = max(niveau for niveau, _ in candidats)
        resultat[fam] = [e for niveau, e in candidats if niveau == meilleur_niveau]
    return resultat


def resoudre_equipement(texte: str, equipements: list[dict]) -> dict | None:
    """Meilleure correspondance unique — pour les demandes portant sur UN équipement (maintenance, disponibilité)."""
    if not texte or not equipements:
        return None
    texte_normalise = normaliser_texte(texte)
    niveau, score, equipement = max(
        ((*_score(texte_normalise, e), e) for e in equipements), key=lambda t: (t[0], t[1])
    )
    return equipement if niveau else None
