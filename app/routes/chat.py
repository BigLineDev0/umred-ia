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
MESSAGE_PERMISSION_REFUSEE = "Je ne peux pas effectuer cette action avec votre rôle actuel."
MESSAGE_INTROUVABLE = "Je n'ai pas trouvé l'élément demandé : il n'existe pas ou vous n'y avez pas accès."


def _erreur_django(exc: DjangoAPIError) -> ChatResponse:
    # Un 401 de Django (token révoqué, compte désactivé...) doit remonter
    # tel quel : le frontend redirige alors vers la connexion.
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expirée, veuillez vous reconnecter.")
    # 403 : Django a refusé l'action pour ce rôle. On le dit clairement,
    # sans laisser croire à une panne.
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        return ChatResponse(type="denied", reponse=MESSAGE_PERMISSION_REFUSEE)
    if exc.status_code == status.HTTP_404_NOT_FOUND:
        return ChatResponse(type="error", reponse=MESSAGE_INTROUVABLE)
    logger.warning("Erreur Django : %s", exc)
    return ChatResponse(type="error", reponse=MESSAGE_SERVICE_INDISPONIBLE)


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
        return ChatResponse(type="error", reponse=MESSAGE_ERREUR_INTERNE)

    ajouter_historique(session, message, reponse.reponse)
    return reponse


@router.get("/chat/accueil", response_model=ChatResponse)
async def accueil(user: Utilisateur = Depends(get_current_user)) -> ChatResponse:
    try:
        return await message_accueil(user)
    except DjangoAPIError as exc:
        return _erreur_django(exc)
