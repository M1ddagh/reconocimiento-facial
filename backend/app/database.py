import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from backend.app.config import settings

logger = logging.getLogger("backend.database")

# Configurar motor de base de datos
connect_args = {}
db_url = settings.DATABASE_URL
if db_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
elif db_url.startswith("postgresql://") and not (
    db_url.startswith("postgresql+psycopg://") or db_url.startswith("postgresql+psycopg2://")
):
    try:
        import psycopg
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    except ImportError:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

try:
    engine = create_engine(
        db_url,
        connect_args=connect_args,
        echo=False,
        future=True,
    )
except Exception as e:
    logger.warning(f"No fue posible conectar a {db_url}: {e}. Usando SQLite local...")
    engine = create_engine(
        "sqlite:///./reconocimiento.db",
        connect_args={"check_same_thread": False},
        future=True,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependencia FastAPI para inyectar la sesión de la base de datos."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
