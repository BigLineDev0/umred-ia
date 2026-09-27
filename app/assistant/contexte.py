import re
from dataclasses import dataclass
from typing import Any

from app.core.security import Utilisateur
from app.nlu.extraction import Extraction
from app.nlu.texte import normaliser_texte
from app.schemas.chat import ChatOption

REPONSES_OUI = {"oui", "yes", "ok", "okay", "d'accord", "daccord", "confirme", "confirmer", "je confirme", "valider", "vas-y"}
REPONSES_NON = {"non", "no", "annule", "annuler", "stop", "laisse tomber", "pas maintenant", "non merci"}


@dataclass
class Contexte:
    """
    Tout ce dont un traitement a besoin, regroupé dans un seul objet : chaque
    fonction du dialogue a ainsi la même signature `(ctx) -> ChatResponse`,
    ce qui permet de les ranger dans de simples tables de routage.
    """

    user: Utilisateur
    session: dict[str, Any]
    message: str
    message_normalise: str
    extraction: Extraction | None = None

    @classmethod
    def creer(cls, user: Utilisateur, session: dict[str, Any], message: str) -> "Contexte":
        return cls(user=user, session=session, message=message, message_normalise=normaliser_texte(message))

    @property
    def token(self) -> str:
        return self.user.token

    @property
    def contexte_etape(self) -> dict[str, Any]:
        return self.session["contexte"]


def _sans_ponctuation(texte: str) -> str:
    return re.sub(r"[^\w' -]", "", texte).strip()


def est_oui(message_normalise: str) -> bool:
    texte = _sans_ponctuation(message_normalise)
    return texte in REPONSES_OUI or texte.startswith("oui")


def est_non(message_normalise: str) -> bool:
    texte = _sans_ponctuation(message_normalise)
    return texte in REPONSES_NON or texte.startswith("non")


def options_confirmation() -> list[ChatOption]:
    return [
        ChatOption(label="Oui, confirmer", value="oui"),
        ChatOption(label="Non, annuler", value="non"),
    ]


def lire_choix(message_normalise: str, prefixe: str) -> int | None:
    """
    Lit la valeur technique d'un bouton (« equip_12 » -> 12). Renvoie None si
    le message n'est pas un clic sur ce type de bouton.
    """
    correspondance = re.fullmatch(rf"{prefixe}_(\d+)", message_normalise.strip())
    return int(correspondance.group(1)) if correspondance else None


def lire_numero(message_normalise: str, nombre_choix: int) -> int | None:
    """Permet aussi de répondre au clavier par « 1 », « 2 »... (index 0-based renvoyé)."""
    texte = _sans_ponctuation(message_normalise)
    if texte.isdigit() and 1 <= int(texte) <= nombre_choix:
        return int(texte) - 1
    return None
