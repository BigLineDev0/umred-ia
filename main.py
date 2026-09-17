from fastapi import FastAPI
from fastapi.params import Depends
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.routes.chat import router as chat_router
from app.core.security import get_current_user

app = FastAPI(title="UMRED Labo — Service IA", version="1.0.0")

# -- CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "ok", "django_api_url": settings.django_api_url}


@app.get("/me")
def me(user_id: int = Depends(get_current_user)):
    return {"user_id": user_id}

app.include_router(chat_router, prefix="/api")