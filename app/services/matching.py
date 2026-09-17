from rapidfuzz import process, fuzz


def resoudre_equipement(nom_extrait: str, equipements: list[dict]) -> dict | None:
    """
    equipements : liste d'objets {"id": ..., "nom": ...} venant de l'API Django.
    Ne renvoie une correspondance QUE si elle est suffisamment fiable —
    sinon on préfère demander une clarification plutôt que deviner.
    """
    if not nom_extrait or not equipements:
        return None

    noms = [e["nom"] for e in equipements]
    resultat = process.extractOne(nom_extrait, noms, scorer=fuzz.WRatio, score_cutoff=70)
    if resultat is None:
        return None

    nom_trouve, score, index = resultat
    return equipements[index]