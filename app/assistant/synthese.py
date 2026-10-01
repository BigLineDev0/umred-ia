"""
Synthèse hebdomadaire en langage naturel des indicateurs de pilotage.

Le modèle de langage REDIGE, il ne CALCULE pas : tous les chiffres viennent
de Django (apps/core/analytique.py), et le texte produit est contrôlé avant
d'être montré. Si le modèle cite un nombre absent des indicateurs (une
« hallucination »), la synthèse est rejetée et remplacée par la version
rédigée par des gabarits, qui est toujours exacte.
"""
import json
import logging
import re

from app.nlu.model import ModeleIndisponible, generer, modele_disponible

logger = logging.getLogger(__name__)

CONSIGNES = (
    "Tu es l'assistant d'un responsable de laboratoire universitaire. À partir des FAITS fournis en JSON, "
    "rédige en français une synthèse de la semaine de 4 à 6 phrases, au ton professionnel. "
    "Commence par le bilan d'activité, puis les tensions ou anomalies, puis les actions recommandées. "
    "Règles strictes : n'utilise QUE les chiffres présents dans les faits, n'invente aucun nom ni chiffre, "
    "pas de liste à puces, pas de titre."
)

NOMBRE = re.compile(r"\d+(?:[.,]\d+)?")


def _faits(ind: dict) -> dict:
    """Sous-ensemble compact des indicateurs : un petit modèle s'égare avec trop de contexte."""
    v = ind["volumes"]
    return {
        "periode": ind["periode"],
        "reservations": v["reservations"],
        "heures_utilisation": v["heures_utilisation"],
        "utilisateurs_actifs": v["utilisateurs_actifs"],
        "taux_annulation_pourcent": v["taux_annulation"],
        "delai_moyen_validation_heures": v["delai_moyen_validation_h"],
        "demandes_en_attente_depuis_48h": v["demandes_en_attente_48h"],
        "equipements_les_plus_occupes": [
            {"nom": e["nom"], "occupation_pourcent": e["taux_occupation"]} for e in ind["occupation_equipements"][:3]
        ],
        "heures_de_pointe": ind["heures_de_pointe"][:2],
        "recommandations": [{"titre": r["titre"], "detail": r["message"]} for r in ind["recommandations"][:4]],
    }


def _nombres(texte: str) -> set[str]:
    return {n.replace(",", ".").rstrip("0").rstrip(".") or "0" for n in NOMBRE.findall(texte)}


def chiffres_verifies(texte: str, faits: dict) -> bool:
    """Chaque nombre de la synthèse doit figurer dans les faits (dates comprises)."""
    autorises = _nombres(json.dumps(faits, ensure_ascii=False))
    inventes = _nombres(texte) - autorises
    if inventes:
        logger.warning("Synthèse rejetée : nombres absents des faits %s", sorted(inventes))
    return not inventes


async def rediger_synthese(indicateurs: dict) -> tuple[str, str]:
    """Renvoie (texte, source) avec source = 'modele' ou 'regles'."""
    repli = indicateurs.get("synthese_regles", "")
    if not modele_disponible():
        return repli, "regles"

    faits = _faits(indicateurs)
    messages = [
        {"role": "system", "content": CONSIGNES},
        {"role": "user", "content": json.dumps(faits, ensure_ascii=False)},
    ]
    try:
        texte = (await generer(messages, max_new_tokens=320, temperature=0.3)).strip()
    except ModeleIndisponible:
        return repli, "regles"

    if len(texte) < 80 or not chiffres_verifies(texte, faits):
        return repli, "regles"
    return texte, "modele"
