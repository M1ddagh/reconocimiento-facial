import os
from pydantic import BaseModel


class Settings(BaseModel):
    PROJECT_NAME: str = "Sistema de Reconocimiento Facial para Búsqueda a Gran Escala"
    API_V1_STR: str = "/api"

    # Entorno y Seguridad Interna
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").lower()
    INTERNAL_API_SECRET: str = os.getenv(
        "INTERNAL_API_SECRET",
        "rf_secret_compartido_dev_mvp" if os.getenv("ENVIRONMENT", "development").lower() != "production" else "",
    )

    def validate_internal_secret(self) -> None:
        """
        Valida que INTERNAL_API_SECRET esté configurado y que en producción
        no se utilice ningún secreto por defecto de desarrollo.
        """
        if not self.INTERNAL_API_SECRET or not self.INTERNAL_API_SECRET.strip():
            raise RuntimeError(
                "INTERNAL_API_SECRET no está configurado. Es obligatorio definir esta variable en el entorno."
            )
        dev_defaults = {
            "rf_secret_compartido_dev_mvp",
            "cambiar_este_secreto_interno_compartido_12345",
            "change_this_secret_in_production_key_123",
        }
        if self.ENVIRONMENT == "production":
            if self.INTERNAL_API_SECRET in dev_defaults:
                raise RuntimeError(
                    "En entorno de producción (ENVIRONMENT=production), está prohibido utilizar el "
                    f"secreto interno por defecto ('{self.INTERNAL_API_SECRET}'). "
                    "Configure un INTERNAL_API_SECRET propio y seguro."
                )
            if len(self.INTERNAL_API_SECRET) < 16:
                raise RuntimeError(
                    "En entorno de producción, INTERNAL_API_SECRET debe tener al menos 16 caracteres."
                )

    # Base de Datos PostgreSQL (con fallback a SQLite local si no está disponible)
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./reconocimiento.db",
    )

    # Redis Broker & Queues
    ENABLE_REDIS: bool = os.getenv("ENABLE_REDIS", "true").lower() in ("true", "1", "yes")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    ALERTS_CHANNEL: str = os.getenv("ALERTS_CHANNEL", "alerts_channel")
    FACE_EVENTS_QUEUE: str = os.getenv("FACE_EVENTS_QUEUE", "face_events_queue")

    # MinIO / S3 Storage
    S3_ENDPOINT: str = os.getenv("S3_ENDPOINT", "localhost:9000")
    S3_ACCESS_KEY: str = os.getenv("S3_ACCESS_KEY", "minioadmin")
    S3_SECRET_KEY: str = os.getenv("S3_SECRET_KEY", "minioadminpassword")
    S3_BUCKET: str = os.getenv("S3_BUCKET", "evidencias-rostros")
    S3_SECURE: bool = os.getenv("S3_SECURE", "false").lower() == "true"

    # FAISS Index
    FAISS_INDEX_PATH: str = os.getenv("FAISS_INDEX_PATH", "search_service/faces.index")
    DEFAULT_SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.65"))


settings = Settings()
