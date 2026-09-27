import time

import jwt
import pytest
from fastapi import HTTPException

from app.core.security import decode_token
from tests.conftest import fabriquer_token


def test_token_valide():
    assert decode_token(fabriquer_token())["user_id"] == 1


@pytest.mark.parametrize("token", [
    fabriquer_token(token_type="refresh"),             # un refresh token n'ouvre pas l'accès
    fabriquer_token(expire_dans=-10),                   # expiré
    fabriquer_token(cle="une-autre-cle-de-signature-de-32-octets"),               # signature invalide
    jwt.encode({"user_id": 1, "token_type": "access", "exp": int(time.time()) + 60}, key=None, algorithm="none"),
    "pas-un-jwt",
])
def test_tokens_refuses(token):
    with pytest.raises(HTTPException) as exc:
        decode_token(token)
    assert exc.value.status_code == 401


def test_chat_sans_token(client, django):
    assert client.post("/api/chat", json={"session_id": "session-test-1", "message": "bonjour"}).status_code in (401, 403)


def test_health_n_expose_pas_l_url_interne(client):
    assert "django" not in client.get("/health").text
