from pydantic import BaseModel
from typing import Optional


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    reponse: str
    intention: Optional[str] = None
    necessite_confirmation: bool = False