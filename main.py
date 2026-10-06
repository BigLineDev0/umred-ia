import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.assistant.synthese import CONSIGNES
from app.nlu.conversation import CONVERSATION_SYSTEM_PROMPT
from app.nlu.extractor import SYSTEM_PROMPT
from app.nlu.model import charger_modele, modele_disponible
from app.routes.chat import router as chat_router
from app.routes.pilotage import router as pilotage_router
from app.services.django_client import fermer_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Chargement du modèle en ARRIÈRE-PLAN : au premier démarrage il faut
    # télécharger ~3 Go, et le service doit répondre pendant ce temps. Tant
    # que le modèle n'est pas prêt, modele_disponible() vaut False et
    # l'assistant fonctionne avec les règles (mode dégradé), puis bascule
    # automatiquement sur le modèle une fois chargé. Les prompts système
    # fixes sont calculés pendant ce chargement (voir app/nlu/model.py).
    prompts = [SYSTEM_PROMPT, CONVERSATION_SYSTEM_PROMPT, CONSIGNES]
    chargement = asyncio.create_task(asyncio.to_thread(charger_modele, prompts))
    yield
    chargement.cancel()
    await fermer_client()


app = FastAPI(title="UMRED Labo — Service IA", version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health")
def health_check():
    # Aucune information interne (URL de Django, versions...) n'est exposée.
    return {"status": "ok", "modele": "charge" if modele_disponible() else "mode_degrade"}


app.include_router(chat_router, prefix="/api")
app.include_router(pilotage_router, prefix="/api")
