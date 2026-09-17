import json
import logging
import re

from app.nlu.model import generer
from app.nlu.fallback import extraire_intention_par_regles

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
Tu es un extracteur d'informations pour un laboratoire universitaire.

Ta tâche est d'analyser le message de l'utilisateur et d'extraire
son intention ainsi que les informations utiles.

Tu dois répondre UNIQUEMENT avec un objet JSON valide.

Les intentions possibles sont :
- reserver
- consulter_disponibilite
- consulter_mes_reservations
- annuler
- maintenance
- statistiques
- autre

Format obligatoire :
{"intention": "reserver", "equipement": "nom ou null", "date": "expression brute ou null",
 "heure_debut": "HH:MM ou null", "heure_fin": "HH:MM ou null"}

Règles :
- Ne donne aucune explication, aucun texte avant ou après le JSON.
- Si une information n'est pas présente, utilise null.
- Pour les heures, utilise toujours le format HH:MM.
"""


def nettoyer_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    debut, fin = text.find("{"), text.rfind("}")
    return text[debut:fin + 1] if debut != -1 and fin != -1 else text


async def extraire_intention(message: str) -> dict:
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ]
        content = await generer(messages, max_new_tokens=150, do_sample=False)
        return json.loads(nettoyer_json(content))
    except Exception:
        logger.exception("Erreur lors de l'extraction de l'intention")
        return await extraire_intention_par_regles(message)