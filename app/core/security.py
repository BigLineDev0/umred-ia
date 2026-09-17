from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from app.core.config import settings

bearer_scheme = HTTPBearer()


def get_bearer_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> str:
    """
    Récupère le token brut envoyé dans l'en-tête Authorization.
    On le garde tel quel : il servira aussi à être RETRANSMIS à Django
    lors des vrais appels métier (vérifier une disponibilité, créer une
    réservation), pour que ce soit Django — pas FastAPI — qui applique
    les permissions réelles (rôle, propriétaire, etc.).
    """
    return credentials.credentials


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré.",
        )

    if payload.get("token_type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ce token n'est pas un token d'accès valide.",
        )

    return payload


def get_current_user(token: str = Depends(get_bearer_token)) -> dict:
    payload = decode_token(token)
    user_id = payload.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token sans identifiant utilisateur.")
    return {
        "user_id": user_id,
        "role": payload.get("role"),
        "prenom": payload.get("prenom"),
        "nom": payload.get("nom"),
    }