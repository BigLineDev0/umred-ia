from rapidfuzz import fuzz


def _famille(nom: str) -> str:
    """
    Regroupement grossier par premier mot du nom ('Microscope optique' et
    'Microscope électronique' partagent la famille 'Microscope'). C'est
    une heuristique simple, assumée comme telle — elle suffit pour
    distinguer des types d'équipements réellement différents dans un
    message, sans avoir besoin d'une vraie taxonomie d'équipements.
    """
    return nom.strip().split(' ')[0].lower()


def equipements_mentionnes(message: str, equipements: list[dict], seuil: int = 78) -> dict[str, list[dict]]:
    """
    Repère, dans un message libre, TOUTES les familles d'équipements
    mentionnées (pas seulement la meilleure correspondance) — condition
    nécessaire pour supporter à la fois :
    - une demande ambiguë ("le microscope" alors qu'il y en a deux)
    - une demande multiple ("le microscope ET la centrifugeuse")
    Renvoie un dict {famille: [équipements correspondants]}.
    """
    message_lower = message.lower()
    par_famille: dict[str, list[dict]] = {}

    for e in equipements:
        score = fuzz.partial_ratio(message_lower, e['nom'].lower())
        if score >= seuil:
            fam = _famille(e['nom'])
            par_famille.setdefault(fam, []).append(e)

    return par_famille


def resoudre_equipement(texte: str, equipements: list[dict]) -> dict | None:
    """Conservé pour les intentions simples (maintenance, disponibilité) qui n'ont besoin que d'UN équipement."""
    from rapidfuzz import process
    if not texte or not equipements:
        return None
    noms = [e['nom'] for e in equipements]
    resultat = process.extractOne(texte, noms, scorer=fuzz.partial_ratio, score_cutoff=75)
    if resultat is None:
        return None
    _, score, index = resultat
    return equipements[index]