import json
import logging
import re

from app.nlu.model import generer
from app.nlu.fallback import extraire_intention_par_regles

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
Tu es un extracteur d'informations pour un laboratoire universitaire.
Analyse le message et extrais UNIQUEMENT ce qui est explicitement écrit.

RÈGLE ABSOLUE : n'invente JAMAIS une date ou une heure absente du message.
Si une information n'est pas explicitement présente, utilise le JSON null
(sans guillemets), jamais le texte "null".

Intentions possibles : reserver, creer_equipement, consulter_disponibilite,
consulter_mes_reservations, annuler, maintenance, statistiques, autre.

Distinction importante :
- "réserve-moi le microscope demain à 14h" → intention: reserver
- "ajoute un équipement microscope" ou "je veux créer un équipement" → intention: creer_equipement
- "comment faire une réservation" (question sur le fonctionnement, pas une vraie demande) → intention: autre

Format obligatoire, sans texte autour :
{"intention": "...", "equipement": "nom ou null", "date": "expression brute ou null",
 "heure_debut": "HH:MM ou null", "heure_fin": "HH:MM ou null"}
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