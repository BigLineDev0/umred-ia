import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ChatOption(BaseModel):
    """Bouton cliquable : le frontend affiche `label` et renvoie `value`."""

    label: str
    value: str


_ROUTE_INTERNE = re.compile(r"/[A-Za-z0-9/_-]*")


class ChatAction(BaseModel):
    """
    Lien vers une page de l'application, affiché comme un bouton. La route
    vient toujours du catalogue app/core/navigation.py (ou d'un identifiant
    renvoyé par Django), jamais du modèle de langage. Le motif interdit
    tout lien externe : une route interne commence par un seul « / ».
    """

    type: Literal["navigate"] = "navigate"
    label: str
    route: str = Field(max_length=200)

    @field_validator("route")
    @classmethod
    def _route_interne(cls, route: str) -> str:
        if not _ROUTE_INTERNE.fullmatch(route) or route.startswith("//"):
            raise ValueError("Seules les routes internes de l'application sont autorisées.")
        return route


class ChatRequest(BaseModel):
    # Le frontend génère un UUID (crypto.randomUUID) : on n'accepte que ce
    # format, ce qui borne la taille des clés stockées en mémoire.
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    # Borne haute : un message géant coûterait cher à l'inférence (DoS).
    message: str = Field(min_length=1, max_length=1000)


class DetailsConfirmation(BaseModel):
    """Récapitulatif affiché sous forme de carte avant une confirmation."""

    laboratoire: Optional[str] = None
    equipement: Optional[str] = None
    date: Optional[str] = None
    heure_debut: Optional[str] = None
    heure_fin: Optional[str] = None


# Nature de la réponse : le frontend choisit son affichage en fonction
# (bouton de navigation, liste de créneaux, cartes de réservations...).
TypeReponse = Literal[
    "message", "navigation", "clarification", "confirmation", "availability",
    "reservations", "reservation", "maintenance", "statistics", "denied", "error",
]


class ChatResponse(BaseModel):
    reponse: str
    type: TypeReponse = "message"
    intention: Optional[str] = None
    necessite_confirmation: bool = False
    options: Optional[list[ChatOption]] = None
    details_confirmation: Optional[DetailsConfirmation] = None
    actions: Optional[list[ChatAction]] = None
    # Données issues de Django, déjà filtrées (jamais produites par le modèle).
    data: Optional[Any] = None
