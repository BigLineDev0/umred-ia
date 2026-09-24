from pydantic import BaseModel
from typing import Optional


class ChatOption(BaseModel):
    label: str
    value: str


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    reponse: str
    intention: Optional[str] = None
    necessite_confirmation: bool = False
    options: Optional[list[ChatOption]] = None
    
    
class DetailsConfirmation(BaseModel):
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