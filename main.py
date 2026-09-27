import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.nlu.model import charger_modele, modele_disponible
from app.routes.chat import router as chat_router
from app.services.django_client import fermer_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Chargement du modèle dans un thread : plusieurs secondes de calcul
    # qui ne doivent pas bloquer la boucle d'événements au démarrage.
    await asyncio.to_thread(charger_modele)
    yield
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
