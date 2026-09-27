"""
Couche HTTP uniquement : authentification, session, gestion des erreurs.
Toute la logique de conversation vit dans app/assistant/.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.assistant.consultation import message_accueil
from app.assistant.dialogue import traiter_message
from app.core.rate_limit import limiter_debit
from app.core.security import Utilisateur, get_current_user
from app.core.session_store import ajouter_historique, cle_session, get_session
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.django_client import DjangoAPIError

logger = logging.getLogger(__name__)

router = APIRouter()

MESSAGE_SERVICE_INDISPONIBLE = (
    "Je n'arrive pas à joindre le service de gestion du laboratoire pour le moment. "
    "Réessayez dans quelques instants ou utilisez les pages de la plateforme."
)
MESSAGE_ERREUR_INTERNE = "Désolé, une erreur inattendue s'est produite. Pouvez-vous reformuler votre demande ?"


def _erreur_django(exc: DjangoAPIError) -> ChatResponse:
    # Un 401 de Django (token révoqué, compte désactivé...) doit remonter
    # tel quel : le frontend redirige alors vers la connexion.
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expirée, veuillez vous reconnecter.")
    logger.warning("Erreur Django : %s", exc)
    return ChatResponse(reponse=MESSAGE_SERVICE_INDISPONIBLE)


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, user: Utilisateur = Depends(limiter_debit)) -> ChatResponse:
    session = get_session(cle_session(user.id, payload.session_id))
    message = payload.message.strip()
    try:
        reponse = await traiter_message(user, session, message)
    except DjangoAPIError as exc:
        return _erreur_django(exc)
    except HTTPException:
        raise
    except Exception:
        # Filet de sécurité : l'utilisateur garde une conversation
        # utilisable, et la trace complète part dans les logs.
        logger.exception("Erreur inattendue pendant le traitement du message")
        return ChatResponse(reponse=MESSAGE_ERREUR_INTERNE)

    ajouter_historique(session, message, reponse.reponse)
    return reponse


@router.get("/chat/accueil", response_model=ChatResponse)
async def accueil(user: Utilisateur = Depends(get_current_user)) -> ChatResponse:
    try:
        return await message_accueil(user)
    except DjangoAPIError as exc:
        return _erreur_django(exc)
