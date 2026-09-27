from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

bearer_scheme = HTTPBearer()


@dataclass(frozen=True)
class Utilisateur:
    """
    Utilisateur authentifié, reconstruit à partir des claims du JWT émis par
    Django (voir UmredTokenObtainPairSerializer.get_token).

    On conserve aussi le token brut : il est RETRANSMIS tel quel à Django pour
    chaque appel métier. Ainsi ce sont les permissions Django (rôle,
    propriétaire de la réservation...) qui s'appliquent réellement — FastAPI
    ne fait jamais une action que l'utilisateur n'aurait pas le droit de
    faire lui-même depuis l'interface.
    """

    id: int
    role: str | None
    prenom: str | None
    nom: str | None
    token: str


def _refuser(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_token(token: str) -> dict:
    """
    Vérifie la signature ET l'expiration du token. L'algorithme est imposé
    par la configuration (jamais lu dans l'en-tête du token) : c'est ce qui
    empêche l'attaque classique "alg: none" ou la confusion d'algorithmes.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "user_id"]},
        )
    except jwt.ExpiredSignatureError:
        raise _refuser("Token expiré.")
    except jwt.PyJWTError:
        raise _refuser("Token invalide.")

    # Un refresh token est lui aussi signé par Django : on le refuse, sinon
    # un token valable 7 jours servirait d'accès permanent au service IA.
    if payload.get("token_type") != "access":
        raise _refuser("Ce token n'est pas un token d'accès valide.")

    return payload


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> Utilisateur:
    token = credentials.credentials
    payload = decode_token(token)
    try:
        # Selon la version de SimpleJWT, user_id est un entier ou une chaîne.
        user_id = int(payload["user_id"])
    except (TypeError, ValueError):
        raise _refuser("Token sans identifiant utilisateur valide.")

    return Utilisateur(
        id=user_id,
        role=payload.get("role"),
        prenom=payload.get("prenom"),
        nom=payload.get("nom"),
        token=token,
    )
