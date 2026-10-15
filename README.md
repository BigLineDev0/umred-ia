---
title: SenLab Assistant IA
emoji: 🔬
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8001
pinned: false
---

<!-- L'en-tête YAML ci-dessus configure le déploiement sur Hugging Face Spaces (image Docker, port 8001). Ne pas le supprimer. -->

# SenLab — Service IA

Service **FastAPI** de SenLab, avec deux fonctionnalités :

1. **L'assistant conversationnel.** L'utilisateur formule une demande en français (« réserve le microscope demain de 10 h à 12 h », « mes réservations de la semaine », « pourquoi ma demande a été refusée ? »). L'assistant consulte les données réelles et guide l'utilisateur par des choix cliquables jusqu'à une confirmation explicite.
2. **La synthèse de l'activité.** Elle est rédigée en langage naturel par un modèle local (**Qwen2.5-1.5B-Instruct**), et chaque chiffre est vérifié avant affichage. Elle sert à la page « Aide à la décision » et à la synthèse hebdomadaire envoyée par n8n.

Le service **n'a pas de base de données**. Il agit pour le compte de l'utilisateur en appelant l'API Django avec son jeton JWT : les règles métier et les permissions restent appliquées par Django.

---

## Sommaire

1. [Principes de conception](#1-principes-de-conception)
2. [Fonctionnement de l'assistant](#2-fonctionnement-de-lassistant)
3. [Stack technique](#3-stack-technique)
4. [Installation et lancement](#4-installation-et-lancement)
5. [Variables d'environnement](#5-variables-denvironnement)
6. [Référence de l'API](#6-référence-de-lapi)
7. [Intentions reconnues](#7-intentions-reconnues)
8. [Modèle de langage](#8-modèle-de-langage)
9. [Synthèse de l'activité](#9-synthèse-de-lactivité)
10. [Sécurité](#10-sécurité)
11. [Structure du projet](#11-structure-du-projet)
12. [Tests](#12-tests)
13. [Déploiement](#13-déploiement)
14. [Limites connues](#14-limites-connues)
15. [Dépannage](#15-dépannage)

---

## 1. Principes de conception

- **Django reste la source de vérité.** Disponibilités, conflits, alternatives, permissions et création des réservations passent par l'API Django (`POST /reservations/verifier/`, `POST /reservations/`…).
- **Le modèle reconnaît, il ne décide pas.**
  - Le modèle choisit une **intention dans une liste fermée** et repère le **nom de l'équipement**.
  - Les **dates et heures** sont extraites par des règles.
  - Le modèle ne produit jamais de route, d'identifiant ni de verdict.
- **Routage déterministe** (`app/assistant/dialogue.py`). Une fois l'intention connue, c'est du code qui choisit les outils à appeler : c'est la partie « tool calling » du service.
- **Le modèle n'est jamais indispensable.** Tant qu'il n'est pas chargé, s'il est trop lent ou s'il renvoie un résultat incohérent, les règles prennent le relais (« mode dégradé »).
- **Aucune action sans confirmation.** Une réservation ou une annulation n'est exécutée qu'après un « oui » explicite de l'utilisateur.

## 2. Fonctionnement de l'assistant

```
message de l'utilisateur
   │
   ▼
① Étape en cours ?  ──oui──▶ gestionnaire de l'étape (choix d'un équipement, oui/non…)
   │ non                        │ si le message n'y répond pas : nouvelle demande
   ▼                            ▼
② Compréhension (NLU hybride)
   • modèle Qwen : intention + nom d'équipement (sortie JSON validée)
   • règles : dates, heures, mots-clés (repli si le modèle échoue)
   │
   ▼
③ Routeur déterministe ─▶ outils = appels à l'API Django avec le JWT de l'utilisateur
   │
   ▼
④ Réponse structurée pour Angular : texte, type d'affichage, options, actions de navigation, données
```

- **Contexte.** Chaque session conserve l'étape de dialogue en cours, une petite mémoire du sujet (dernier équipement, dernière date) et les 6 derniers échanges. L'assistant comprend ainsi « réserve-le » ou « le premier ».
- **Équipements en plusieurs exemplaires.** Si l'équipement existe dans plusieurs laboratoires, l'assistant ne devine pas : il propose les exemplaires avec leur disponibilité réelle sous forme de boutons.
- **Alerte d'usure.** Avant de confirmer, l'assistant signale un équipement qui approche de son seuil de maintenance.
- **Conflits.** Il applique la logique de Django : créneaux alternatifs, équipement équivalent, arbitrage par priorité de projet.
- **Navigation.** Il propose des liens vers les pages de l'application. Le catalogue `app/core/navigation.py` reproduit les routes Angular réelles et leurs rôles : une page n'est proposée qu'aux rôles qui peuvent l'ouvrir, sinon l'assistant explique le refus.

Étapes de dialogue gérées (`app/core/session_store.py`) :

- collecte des informations de réservation ;
- choix de l'équipement ;
- choix d'un créneau disponible ;
- confirmation de la réservation ;
- choix d'une alternative ;
- choix puis confirmation d'une annulation.

## 3. Stack technique

| Composant | Version | Rôle |
|---|---|---|
| Python | 3.12 | Langage |
| FastAPI / Starlette | 0.143 / 1.7 | API asynchrone, validation Pydantic, documentation Swagger |
| Uvicorn | 0.32 | Serveur ASGI |
| Pydantic / pydantic-settings | 2.9 / 2.6 | Schémas et configuration |
| Transformers / Accelerate | 5.17 / 1.15 | Chargement et exécution du modèle |
| PyTorch (CPU) | 2.14 | Inférence sur processeur |
| httpx | 0.27 | Client asynchrone vers l'API Django |
| PyJWT | 2.15 | Vérification des jetons émis par Django |
| dateparser, RapidFuzz | 1.2 / 3.10 | Dates en français, rapprochement approximatif des noms d'équipements |
| pytest | 9.1 | Tests |

## 4. Installation et lancement

### Avec Docker Compose (pile complète)

Depuis `../backend` : `docker compose up -d --build` (voir le README du backend). Le service est publié sur http://localhost:8011, et Compose renseigne automatiquement :

- `DJANGO_API_URL` ;
- `JWT_SECRET_KEY` (repris de la `SECRET_KEY` de Django) ;
- `TACHES_TOKEN` ;
- `CORS_ORIGINS`.

### En local

```bash
cd umred-labo-ia
python -m venv venv && source venv/bin/activate
pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu   # PyTorch CPU, bien plus léger
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # renseigner DJANGO_API_URL et JWT_SECRET_KEY
uvicorn main:app --reload --port 8001
```

- **Premier démarrage.** Le modèle (environ 3 Go) est téléchargé depuis Hugging Face, puis mis en cache dans `~/.cache/huggingface`.
- **Pendant le chargement**, le service répond déjà, en mode dégradé : `GET /health` renvoie `"modele": "mode_degrade"`, puis `"charge"`.
- **Documentation interactive** : http://localhost:8001/docs.

## 5. Variables d'environnement

Lues par `app/core/config.py` (pydantic-settings), depuis l'environnement ou le fichier `.env`. Les valeurs sensibles n'ont pas de valeur par défaut : si elles manquent, le service refuse de démarrer.

| Variable | Défaut | Description |
|---|---|---|
| `DJANGO_API_URL` | *(obligatoire)* | URL de l'API Django, sans barre finale. Exemple : `http://backend:8000/api` |
| `DJANGO_API_TIMEOUT` | `10.0` | Délai maximal d'un appel à Django (secondes) |
| `JWT_SECRET_KEY` | *(obligatoire)* | **Strictement identique** à la `SECRET_KEY` de Django, qui signe les jetons |
| `JWT_ALGORITHM` | `HS256` | Algorithme des jetons |
| `MODEL_NAME` | `Qwen/Qwen2.5-1.5B-Instruct` | Modèle Hugging Face à charger |
| `MODEL_QUEUE_TIMEOUT` | `20.0` | Attente maximale d'un créneau d'inférence avant de basculer sur les règles (secondes) |
| `HUGGINGFACE_API_KEY` | *(vide)* | Utile uniquement pour un modèle privé ou pour éviter les limites de téléchargement. Une valeur vide est ignorée. |
| `CORS_ORIGINS` | `["http://localhost:4200"]` | Origines autorisées, au format JSON |
| `RATE_LIMIT_PAR_MINUTE` | `20` | Messages autorisés par utilisateur et par minute |
| `TIME_ZONE` | `Africa/Dakar` | Fuseau horaire, identique au `TIME_ZONE` de Django (interprétation de « demain », « lundi »…) |
| `SESSION_TTL_MINUTES` | `30` | Durée de vie d'une conversation inactive |
| `SESSION_MAX` | `5000` | Nombre maximal de conversations gardées en mémoire (les plus anciennes sont purgées) |
| `CONVERSATION_LIBRE` | `false` | `true` : les messages hors commande reçoivent une réponse rédigée par le modèle (et les derniers échanges sont gardés en mémoire). Désactivé par défaut : ~10 s par réponse et réponses hors sujet mesurées (voir `../docs/PERFORMANCES_IA.md`) |
| `TACHES_TOKEN` | *(vide)* | Même secret que le `TACHES_TOKEN` de Django. Autorise le workflow n8n à appeler `POST /api/pilotage/synthese/rediger`. Vide : route désactivée (403). |

## 6. Référence de l'API

| Méthode | Route | Authentification | Description |
|---|---|---|---|
| GET | `/health` | aucune | État du service et du modèle (`charge` ou `mode_degrade`). N'expose aucune information interne. |
| POST | `/api/chat` | JWT utilisateur | Traite un message de l'assistant (limité à `RATE_LIMIT_PAR_MINUTE`) |
| GET | `/api/chat/accueil` | JWT utilisateur | Message d'accueil personnalisé selon le rôle |
| GET | `/api/pilotage/synthese?date_debut=&date_fin=` | JWT (ADMIN ou TECHNICIEN) | Synthèse de l'activité de l'établissement sur la période |
| POST | `/api/pilotage/synthese/rediger` | en-tête `X-Taches-Token` | Rédige une synthèse à partir d'indicateurs fournis (workflow n8n) |
| GET | `/api/metriques` | JWT (ADMIN ou SUPER_ADMIN) | Latence de l'assistant sur les 1 000 dernières requêtes : médiane, 95e percentile, par chemin (règles, modèle, étape), appels au modèle, erreurs. Aucune donnée personnelle |

### `POST /api/chat`

Requête :

```json
{ "session_id": "3f2b8c1e-9d4a-4c1b-a7f0-1e2d3c4b5a69", "message": "réserve le lecteur ELISA demain de 10h à 12h" }
```

- `session_id` : 8 à 64 caractères `[A-Za-z0-9_-]`. Le frontend génère un UUID par conversation.
- `message` : 1 à 1 000 caractères.

Réponse :

```json
{
  "reponse": "Confirmez-vous cette réservation ?",
  "type": "confirmation",
  "intention": "reserver",
  "necessite_confirmation": true,
  "options": [{ "label": "Oui, confirmer", "value": "oui" }, { "label": "Non, annuler", "value": "non" }],
  "details_confirmation": {
    "laboratoire": "Laboratoire d'Immunologie", "equipement": "Lecteur de microplaques ELISA",
    "date": "2026-10-13", "heure_debut": "10:00", "heure_fin": "12:00"
  },
  "actions": null,
  "data": null
}
```

Le champ `type` indique au frontend comment afficher la réponse : `message`, `navigation`, `clarification`, `confirmation`, `availability`, `reservations`, `reservation`, `maintenance`, `statistics`, `denied`, `error`.

### `POST /api/pilotage/synthese/rediger`

```bash
curl -X POST http://localhost:8011/api/pilotage/synthese/rediger \
     -H "X-Taches-Token: $TACHES_TOKEN" -H "Content-Type: application/json" \
     -d '{"indicateurs": { …réponse de GET /api/taches/synthese-hebdomadaire/ (Django)… }}'
# → {"synthese": "…", "source": "modele" | "regles", "periode": {"debut": "…", "fin": "…"}}
```

## 7. Intentions reconnues

| Intention | Exemple | Outils Django appelés |
|---|---|---|
| `reserver` | « réserve la centrifugeuse jeudi à 14 h » | équipements, créneaux occupés, `reservations/verifier/`, création |
| `consulter_disponibilite` | « quels équipements sont libres lundi ? » | équipements, créneaux occupés, vérification |
| `consulter_mes_reservations` | « mes réservations de la semaine » | réservations (filtres de période appliqués par Django) |
| `annuler` | « annule ma réservation de demain » | réservations, puis annulation après confirmation |
| `motif_refus` | « pourquoi ma demande a été refusée ? » | réservations refusées |
| `statut_demande` | « pourquoi ma demande est encore en attente ? » | réservations en attente, équipements (raison déduite des règles de Django) |
| `bilan` | « fais-moi le bilan de la semaine » | réservations de la période, archives comprises ; chiffres calculés par le code |
| `conseil_equipement` | « quel appareil pour une mesure d'absorbance ? » | équipements (nom, catégorie, description) |
| `aide` | « qui valide mes demandes ? », « horaires d'ouverture ? », « comment utiliser le microscope ? » | horaires de l'établissement, fiche de l'équipement, ou réponse fixe |
| `maintenance` | « le thermocycleur est-il en maintenance ? » | équipements, maintenances |
| `statistiques` | « combien de réservations ce mois-ci ? » | réservations, équipements, maintenances |
| `naviguer`, `creer_equipement` | « ouvre la page des consommables » | aucun : catalogue de pages |
| `salutation`, `remerciement`, `identite`, `mon_nom` | « bonjour », « merci ! », « qui es-tu ? » | aucun |
| `autre` | « quelle est la capitale du Japon ? » | réponse fixe « hors périmètre », aucune action lancée |

Détails, relances (« et jeudi après-midi ? », « le lendemain ») et règles de confirmation : `../docs/ASSISTANT_IA.md`.

L'ordre des mots-clés compte (`app/nlu/intentions.py`) : les formulations spécifiques sont testées en premier. Ainsi, « pourquoi ma réservation a été refusée » n'est pas pris pour une annulation, et « comment réserver ? » ne lance pas de réservation.

## 8. Modèle de langage

`app/nlu/model.py` gère le modèle :

- **Chargement en arrière-plan**, au démarrage (`lifespan` de `main.py`), jamais à l'import : importer le module dans un test ne télécharge rien.
- **Précision bfloat16** (`dtype="auto"`) : environ 3 Go de mémoire vive.
- **Une inférence à la fois**, sous verrou, dans un thread séparé : la boucle asynchrone continue de servir les autres requêtes. Une requête qui attend son tour plus de `MODEL_QUEUE_TIMEOUT` bascule sur les règles.
- **Prompts système précalculés.** Le cache clé/valeur des prompts fixes est calculé au démarrage. D'après la mesure notée dans le code, une extraction passe ainsi de 60 à 75 s à environ 10 s sur processeur.

Pourquoi Qwen2.5-1.5B plutôt que la version 0.5B ?

- meilleure fiabilité de la sortie JSON structurée, indispensable quand le résultat déclenche une vraie réservation ;
- bonne prise en charge du français ;
- licence Apache 2.0.

## 9. Synthèse de l'activité

`app/assistant/synthese.py` : **le modèle rédige, il ne calcule pas.**

1. Les indicateurs sont calculés par Django (`/api/pilotage/indicateurs/`). Le modèle ne reçoit qu'un sous-ensemble compact : volumes, équipements les plus occupés, heures de pointe, recommandations.
2. Il rédige 4 à 6 phrases, avec la consigne de n'utiliser que les chiffres fournis.
3. **Contrôle anti-hallucination** : chaque nombre du texte doit figurer dans les faits fournis. Sinon, ou si le texte est trop court, la synthèse est remplacée par la version rédigée par gabarits (`synthese_regles`), toujours exacte.
4. Le champ `source` indique l'origine du texte retenu : `modele` ou `regles`.

## 10. Sécurité

- **Vérification des jetons.** Signature HS256 vérifiée avec la clé partagée. Les champs `exp` et `user_id` sont obligatoires, et seuls les jetons d'**accès** sont acceptés : un jeton de rafraîchissement ou un jeton non signé est refusé (401).
- **Transmission du jeton.** Le jeton est transmis tel quel à Django pour chaque appel métier. Le service ne peut rien faire que l'utilisateur ne pourrait faire lui-même depuis l'interface.
- **Limitation de débit** par utilisateur (et non par IP : derrière le réseau d'une université, tout le monde partage souvent la même IP).
- **Entrées bornées.** Messages limités à 1 000 caractères, identifiant de session au format contrôlé.
- **Liens internes uniquement.** Les actions de navigation ne peuvent contenir qu'une route interne commençant par une seule barre oblique : aucun lien externe n'est possible.
- **CORS restreint** aux origines configurées, méthodes `GET` et `POST` uniquement.
- **`/health`** n'expose ni l'URL de Django ni les versions.

## 11. Structure du projet

```
umred-labo-ia/
├── main.py                    Application FastAPI, chargement du modèle en arrière-plan, CORS, /health
├── app/
│   ├── routes/
│   │   ├── chat.py            POST /api/chat, GET /api/chat/accueil
│   │   └── pilotage.py        Synthèse (utilisateur et n8n)
│   ├── nlu/
│   │   ├── extractor.py       Compréhension hybride modèle + règles
│   │   ├── model.py           Chargement et inférence du modèle
│   │   ├── intentions.py      Liste fermée d'intentions et mots-clés
│   │   ├── regles.py          Extraction par règles (dates, heures)
│   │   ├── normalisation.py   Normalisation des dates et heures
│   │   ├── conversation.py    Réponses conversationnelles
│   │   └── texte.py, domaine.py, extraction.py
│   ├── assistant/
│   │   ├── dialogue.py        Routeur : intention → traitement
│   │   ├── reservation.py     Parcours de réservation (exemplaires, usure, conflits)
│   │   ├── annulation.py      Parcours d'annulation
│   │   ├── consultation.py    Réservations, maintenances, statistiques
│   │   ├── disponibilites.py  Créneaux libres
│   │   ├── navigation.py      Réponses de navigation
│   │   ├── synthese.py        Rédaction et contrôle de la synthèse
│   │   └── contexte.py, formatage.py
│   ├── services/
│   │   ├── django_client.py   Un appel à l'API Django = une fonction (avec le JWT de l'utilisateur)
│   │   ├── matching.py        Rapprochement des noms d'équipements
│   │   └── disponibilite.py, reservations.py
│   ├── core/
│   │   ├── config.py          Configuration
│   │   ├── security.py        Vérification des JWT, jeton des tâches n8n
│   │   ├── rate_limit.py      Limitation de débit par utilisateur
│   │   ├── session_store.py   Sessions de conversation en mémoire (LRU + expiration)
│   │   ├── navigation.py      Catalogue des routes Angular par rôle
│   │   └── temps.py, constantes.py
│   └── schemas/chat.py        Schémas des requêtes et réponses
├── tests/                     Tests pytest (Django simulé, sans modèle)
├── Dockerfile
├── requirements.txt / requirements-dev.txt
└── .env.example
```

## 12. Tests

```bash
pytest            # 220 tests, moins de 3 secondes
pytest tests/test_nlu.py -v
```

Les tests tournent **sans modèle de langage et sans Django** :

- le modèle n'est jamais chargé, donc l'extraction passe par les règles ;
- Django est simulé par un faux backend branché sur le client httpx (`httpx.MockTransport`), ce qui teste aussi le vrai code du client.

| Fichier | Ce qui est vérifié |
|---|---|
| `test_nlu.py` | Reconnaissance des intentions, dates et heures en français |
| `test_dialogue.py` | Réservation complète, informations complétées au tour suivant, équipement ambigu puis sélection, identifiant forgé refusé, confirmations, changement de sujet, isolation des sessions, message trop long, limite de débit |
| `test_assistant_intelligent.py` | Navigation selon le rôle (routes Angular réelles uniquement), conflits et alternatives fournis par Django, jours fermés, mémoire du contexte (« réserve-le », « le premier »), dates et périodes, erreurs de Django expliquées |
| `test_securite.py` | Jetons refusés (expiré, de rafraîchissement, non signé, mauvaise clé), `/health` sans fuite d'information |
| `test_synthese.py` | Rejet des chiffres inventés, repli sur les gabarits, route n8n protégée par jeton |
| `test_ameliorations.py` | Confirmation stricte (« oui mais… »), confirmations simultanées et délai dépassé, « après-midi », fautes sur les dates, relances (« le lendemain », « et jeudi ? »), statut d'une demande, bilan calculé, conseil d'équipement, aide, hors périmètre sans modèle, annulation pour autrui refusée, verrou du modèle |

Test d'intégration avec le **modèle réel** (lent, hors pytest) : `scripts/banc_assistant.py`, voir `../docs/PERFORMANCES_IA.md`.

## 13. Déploiement

### Image Docker

Le `Dockerfile` (Python 3.12 slim) installe **PyTorch en version CPU** depuis l'index PyTorch, puis les autres dépendances. La version doit rester identique à celle de `requirements.txt` : sinon pip la remplace par la version CUDA, plus lourde de plusieurs Go.

Lancement : `uvicorn main:app --host 0.0.0.0 --port 8001`.

Dans `../backend/docker-compose.yml`, le cache Hugging Face est monté sur le volume `ia_hf_cache` : le modèle n'est téléchargé qu'une fois.

### Hugging Face Spaces

L'en-tête YAML de ce fichier configure un Space de type Docker sur le port 8001. Renseignez les variables `DJANGO_API_URL`, `JWT_SECRET_KEY`, `CORS_ORIGINS` (et `TACHES_TOKEN` pour n8n) dans les paramètres du Space.

### Dimensionnement

Prévoyez au moins 4 Go de mémoire vive pour ce service seul. Sur processeur, comptez une dizaine de secondes par message traité par le modèle.

> Un seul processus Uvicorn : les sessions de conversation et les compteurs de débit sont en mémoire. Pour plusieurs instances, il faudrait les déplacer dans un stockage partagé (Redis, par exemple).

## 14. Limites connues

- **Temps de réponse.** Environ 10 s par message sur processeur quand le modèle est sollicité.
- **Sessions volatiles.** Les sessions sont perdues au redémarrage (mémoire du processus).
- **Périmètre de compréhension.** Le modèle ne reconnaît que les intentions de la liste fermée : une demande hors périmètre reçoit une réponse explicative, sans action.
- **Pas de maintenance prédictive.** L'alerte d'usure consultée vient de Django, qui la calcule par seuils fixes.

## 15. Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| 401 sur chaque message alors que l'utilisateur est connecté | `JWT_SECRET_KEY` ≠ `SECRET_KEY` de Django | Utiliser la même valeur. Un retour à la ligne copié dans `.env` suffit à créer l'écart : comparez la longueur des deux valeurs. |
| `/health` reste sur `mode_degrade` | Téléchargement du modèle en cours ou en échec | Consulter les journaux. Vérifier l'accès réseau à Hugging Face, l'espace disque (environ 3 Go) et la mémoire. |
| Réponses lentes, puis réponses « par règles » | File d'attente du modèle saturée (une inférence à la fois) | Normal sous charge ; augmenter `MODEL_QUEUE_TIMEOUT` ou le CPU disponible |
| 429 « Trop de messages envoyés » | Limite par utilisateur atteinte | Attendre une minute ou ajuster `RATE_LIMIT_PAR_MINUTE` |
| 502 sur `/api/pilotage/synthese` | API Django injoignable depuis le service | Vérifier `DJANGO_API_URL` (dans Docker : `http://backend:8000/api`) |
| 403 sur `/api/pilotage/synthese/rediger` | `TACHES_TOKEN` absent ou différent de celui de Django et de n8n | Aligner les trois valeurs |
| L'image Docker pèse plusieurs Go de trop | PyTorch CUDA installé à la place de la version CPU | Garder la même version de torch dans le `Dockerfile` et `requirements.txt` |
