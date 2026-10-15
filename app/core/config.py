from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuration lue depuis les variables d'environnement (ou le fichier
    .env). Aucune valeur sensible n'a de valeur par défaut : si elle manque,
    le service refuse de démarrer plutôt que de tourner avec un secret bidon.
    """

    # protected_namespaces=() : autorise les champs nommés « model_... ».
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    # --- Backend Django ---
    django_api_url: str
    django_api_timeout: float = 10.0

    # --- JWT ---
    # Doit être STRICTEMENT identique à la clé qui signe les tokens côté
    # Django (SIMPLE_JWT n'ayant pas de SIGNING_KEY, c'est SECRET_KEY).
    # FastAPI ne délivre aucun token : il vérifie seulement ceux de Django.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"

    # --- Modèle de langage ---
    # Le modèle tourne en local ; la clé Hugging Face n'est utile que pour
    # télécharger un modèle privé ou éviter les limites de téléchargement.
    huggingface_api_key: str | None = None
    model_name: str = "Qwen/Qwen2.5-1.5B-Instruct"
    # Temps maximal d'attente d'un créneau d'inférence avant de basculer
    # sur l'extraction par règles (le modèle traite une requête à la fois).
    model_queue_timeout: float = 20.0
    # Réponse rédigée librement par le modèle aux messages hors commande.
    # Désactivée par défaut : mesuré sur Qwen2.5-1.5B, elle coûtait ~10 s
    # et répondait à des questions hors sujet (« La capitale du Japon est
    # Tokyo ») malgré les consignes. Les questions sur la plateforme ont des
    # réponses déterministes (app/assistant/aide.py).
    conversation_libre: bool = False

    # --- Tâches planifiées (n8n) ---
    # Même secret que TACHES_TOKEN côté Django : il autorise le workflow n8n
    # à demander la rédaction de la synthèse hebdomadaire sans compte
    # utilisateur. Absent : la route correspondante répond 403.
    taches_token: str | None = None

    # --- HTTP ---
    cors_origins: list[str] = ["http://localhost:4200"]
    # Nombre de messages autorisés par utilisateur et par minute : l'inférence
    # coûte cher en CPU, un seul client ne doit pas pouvoir saturer le service.
    rate_limit_par_minute: int = 20

    # --- Fuseau horaire de l'établissement (identique au TIME_ZONE de Django) ---
    time_zone: str = "Africa/Dakar"

    # --- Sessions de conversation ---
    session_ttl_minutes: int = 30
    session_max: int = 5000

    @field_validator("huggingface_api_key", "taches_token")
    @classmethod
    def _cle_vide_ignoree(cls, valeur: str | None) -> str | None:
        # « HUGGINGFACE_API_KEY= » (vide, comme dans .env.example) donnait un
        # en-tête « Authorization: Bearer » invalide et empêchait le
        # téléchargement du modèle : une clé vide équivaut à aucune clé.
        return valeur.strip() or None if valeur is not None else None

    @field_validator("django_api_url")
    @classmethod
    def _sans_slash_final(cls, valeur: str) -> str:
        # Évite les URL du type ".../api//equipements/".
        return valeur.rstrip("/")


settings = Settings()
