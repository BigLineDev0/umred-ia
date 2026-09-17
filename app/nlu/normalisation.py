import dateparser


def normaliser_date(date_brute: str | None) -> str | None:
    """
    Convertit n'importe quelle expression de date (« demain », « vendredi
    prochain », ou déjà un format ISO) en une vraie date YYYY-MM-DD.
    Appelée systématiquement après l'extraction, que la source soit le
    LLM (qui renvoie du texte brut) ou le fallback par règles (qui
    renvoie déjà de l'ISO) — dateparser reconnaît les deux sans problème,
    donc appliquer cette normalisation deux fois ne casse rien.
    """
    if not date_brute:
        return None

    resultat = dateparser.parse(
        date_brute,
        languages=["fr"],
        settings={"PREFER_DATES_FROM": "future"},
    )
    if not resultat:
        return None
    return resultat.strftime("%Y-%m-%d")


VALEURS_NULLES = {"null", "none", "n/a", "aucun", "aucune", ""}


def nettoyer_extraction(extraction: dict) -> dict:
    """
    Les petits modèles hallucinent parfois la CHAÎNE "null" plutôt que
    le vrai null JSON. Sans ce nettoyage, "null" (texte) est considéré
    comme une valeur présente par nos vérifications de slots manquants.
    """
    for cle in ["equipement", "date", "heure_debut", "heure_fin"]:
        valeur = extraction.get(cle)
        if isinstance(valeur, str) and valeur.strip().lower() in VALEURS_NULLES:
            extraction[cle] = None
    return extraction