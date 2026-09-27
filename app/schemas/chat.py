from typing import Optional

from pydantic import BaseModel, Field


class ChatOption(BaseModel):
    """Bouton cliquable : le frontend affiche `label` et renvoie `value`."""

    label: str
    value: str


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


class ChatResponse(BaseModel):
    reponse: str
    intention: Optional[str] = None
    necessite_confirmation: bool = False
    options: Optional[list[ChatOption]] = None
    details_confirmation: Optional[DetailsConfirmation] = None
