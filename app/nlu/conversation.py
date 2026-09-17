import logging
from app.nlu.model import generer

logger = logging.getLogger(__name__)

CONVERSATION_SYSTEM_PROMPT = """Tu es l'assistant virtuel de UMRED Labo, un laboratoire de recherche
universitaire à Thiès, au Sénégal. Réponds de façon naturelle, chaleureuse, en 2-3 phrases maximum.

Si la question sort du cadre du laboratoire (culture générale, actualité, calculs...), réponds
poliment que ce n'est pas ton domaine et propose d'aider sur la gestion du laboratoire à la place.

Important : tu n'as accès à AUCUNE donnée réelle dans cette conversation (pas de réservations,
équipements ou maintenances). N'invente jamais de donnée précise — si on te demande une info
concrète sur le laboratoire, dis que tu ne peux répondre qu'à ce sujet via une vraie commande."""


async def repondre_conversationnel(message: str) -> str:
    try:
        messages = [
            {"role": "system", "content": CONVERSATION_SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ]
        contenu = await generer(messages, max_new_tokens=150, do_sample=True)
        return contenu.strip()
    except Exception:
        logger.exception("Erreur lors de la génération conversationnelle")
        return "Je n'ai pas bien compris votre demande. Je peux vous aider à réserver un équipement, consulter vos réservations, ou suivre une maintenance."