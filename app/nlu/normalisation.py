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