from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.assistant.synthese import rediger_synthese
from app.core.rate_limit import limiter_debit
from app.core.security import Utilisateur
from app.services.django_client import DjangoAPIError, get_indicateurs

router = APIRouter()

ROLES_PILOTAGE = {"ADMIN", "TECHNICIEN"}


class SyntheseResponse(BaseModel):
    synthese: str
    source: str  # 'modele' (rédigée par le modèle de langage) ou 'regles' (gabarits)
    periode: dict


@router.get("/pilotage/synthese", response_model=SyntheseResponse)
async def synthese(
    date_debut: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_fin: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    user: Utilisateur = Depends(limiter_debit),
) -> SyntheseResponse:
    if user.role not in ROLES_PILOTAGE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Réservé aux administrateurs et techniciens.")
    try:
        indicateurs = await get_indicateurs(user.token, date_debut, date_fin)
    except DjangoAPIError as exc:
        code = exc.status_code if exc.status_code in (401, 403) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=exc.detail)

    texte, source = await rediger_synthese(indicateurs)
    return SyntheseResponse(synthese=texte, source=source, periode=indicateurs["periode"])
