---
title: UMRED Assistant IA
emoji: 🔬
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8001
pinned: false
---

# UMRED — Service IA

Service FastAPI de la plateforme UMRED : assistant conversationnel et synthèse
hebdomadaire des indicateurs, rédigée par un modèle de langage local
(Qwen2.5-1.5B-Instruct) dont chaque chiffre est vérifié avant affichage.

L'en-tête YAML ci-dessus configure le déploiement sur Hugging Face Spaces
(image Docker, port 8001).

## Variables d'environnement

| Variable | Rôle |
|---|---|
| `DJANGO_API_URL` | URL de l'API Django, ex. `https://umred-api.onrender.com/api` |
| `JWT_SECRET_KEY` | **Identique** à la `SECRET_KEY` de Django (vérification des JWT) |
| `CORS_ORIGINS` | Origines autorisées, ex. `["https://umred.vercel.app"]` |
| `MODEL_NAME` | Facultatif, défaut `Qwen/Qwen2.5-1.5B-Instruct` |

Tant que le modèle se charge, le service répond avec des règles (mode dégradé) ;
`GET /health` indique `"modele": "mode_degrade"` puis `"charge"`.

## Développement local

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # puis compléter
uvicorn main:app --reload --port 8001
pytest
```
