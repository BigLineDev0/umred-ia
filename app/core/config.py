from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    django_api_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    huggingface_api_key: str

    class Config:
        env_file = ".env"


settings = Settings()