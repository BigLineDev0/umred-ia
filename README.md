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

## Fonctionnement de l'assistant

```
message ─> NLU (règles + modèle, sortie JSON : une intention dans une liste fermée)
        ─> routeur déterministe (app/assistant/dialogue.py)
        ─> outils = appels à l'API Django avec le JWT de l'utilisateur (app/services/django_client.py)
        ─> réponse structurée pour Angular : type, texte, boutons, actions de navigation, données
```

- **Django reste la source de vérité** : disponibilités, conflits, alternatives
  (`POST /reservations/verifier/`), permissions et création. Le service IA n'a
  aucun accès à la base de données.
- **Le modèle ne produit jamais de route, d'identifiant ni de verdict** : il
  reconnaît une intention ; dates et heures sont extraites par des règles.
- **Navigation** : `app/core/navigation.py` recense les routes Angular réelles
  et leurs rôles (miroir des `roleGuard`). Une page n'est proposée qu'aux rôles
  qui peuvent l'ouvrir ; sinon l'assistant explique le refus et propose une
  page alternative.
- **Contexte** : une étape de dialogue à la fois (`session_store.py`) et une
  petite mémoire du sujet en cours (dernier équipement / date) pour comprendre
  « réserve-le » ou « le premier ».
- **Mode dégradé** : sans modèle, les règles couvrent toutes les intentions.

## Développement local

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # puis compléter
uvicorn main:app --reload --port 8001
pytest
```
