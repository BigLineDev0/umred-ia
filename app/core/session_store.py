from typing import Optional

# Stockage en mémoire — limite assumée pour la V1 (perdu au redémarrage,
# ne fonctionne que sur un seul process). Migration naturelle vers Redis
# le jour où le service tournera en plusieurs instances.
_sessions: dict[str, dict] = {}


def get_session(session_id: str) -> dict:
    return _sessions.setdefault(session_id, {"slots": {}, "intention": None})


def update_session(session_id: str, **kwargs) -> None:
    session = get_session(session_id)
    session.update(kwargs)


def reset_session(session_id: str) -> None:
    _sessions[session_id] = {"slots": {}, "intention": None}