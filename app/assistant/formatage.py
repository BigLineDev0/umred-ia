"""
Mise en forme des réponses en français lisible (« lundi 28 septembre de
10h à 12h » plutôt que « 2026-09-28 de 10:00:00 à 12:00:00 »). Noms des
jours et des mois codés en dur : aucune dépendance à la locale du serveur.
"""
from datetime import date

from app.core.constantes import LABELS_STATUT_RESERVATION
from app.nlu.normalisation import JOURS_SEMAINE

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


def formater_date(iso: str, aujourd_hui: date | None = None) -> str:
    aujourd_hui = aujourd_hui or date.today()
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    texte = f"{JOURS_SEMAINE[d.weekday()]} {d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]}"
    if d.year != aujourd_hui.year:
        texte += f" {d.year}"
    ecart = (d - aujourd_hui).days
    if ecart == 0:
        return f"aujourd'hui ({texte})"
    if ecart == 1:
        return f"demain ({texte})"
    return texte


def formater_heure(heure: str) -> str:
    """« 14:00:00 » -> « 14h », « 09:30 » -> « 9h30 »."""
    heures, minutes = str(heure)[:5].split(":")
    return f"{int(heures)}h{'' if minutes == '00' else minutes}"


def formater_plage(debut: str, fin: str) -> str:
    return f"de {formater_heure(debut)} à {formater_heure(fin)}"


def formater_liste(elements: list[str]) -> str:
    """["a", "b", "c"] -> « a, b et c »."""
    if len(elements) <= 1:
        return "".join(elements)
    return f"{', '.join(elements[:-1])} et {elements[-1]}"


def resume_reservation(r: dict) -> str:
    """Une ligne lisible décrivant une réservation renvoyée par Django."""
    quoi = ", ".join(r.get("equipements_noms") or []) or r.get("laboratoire_nom", "")
    statut = LABELS_STATUT_RESERVATION.get(r.get("statut", ""), r.get("statut", ""))
    return f"{formater_date(r['date'])} {formater_plage(r['heure_debut'], r['heure_fin'])} · {quoi} — {statut}"
