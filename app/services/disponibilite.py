from datetime import date, timedelta

HEURE_OUVERTURE = "08:00"
HEURE_FERMETURE = "20:00"


def calculer_creneaux_libres(occupes: list[dict], jours: list[str]) -> dict[str, list[str]]:
    """
    Calcul simple par différence : sur chaque jour, on part de la
    plage d'ouverture complète et on retire les créneaux déjà pris.
    Hypothèse assumée : le laboratoire ouvre de 8h à 18h — à ajuster
    facilement si un jour l'UMRED fournit ses vrais horaires par labo.
    """
    resultat = {}
    for jour in jours:
        occupes_du_jour = sorted([o for o in occupes if o["date"] == jour], key=lambda o: o["heure_debut"])
        curseur = HEURE_OUVERTURE
        libres = []
        for creneau in occupes_du_jour:
            if creneau["heure_debut"][:5] > curseur:
                libres.append(f"{curseur}–{creneau['heure_debut'][:5]}")
            curseur = max(curseur, creneau["heure_fin"][:5])
        if curseur < HEURE_FERMETURE:
            libres.append(f"{curseur}–{HEURE_FERMETURE}")
        resultat[jour] = libres
    return resultat


def jours_de_la_periode(periode: str) -> list[str]:
    aujourdhui = date.today()
    if periode == "semaine":
        return [(aujourdhui + timedelta(days=i)).isoformat() for i in range(7)]
    return [aujourdhui.isoformat()]