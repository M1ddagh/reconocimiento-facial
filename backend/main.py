import asyncio
from contextlib import asynccontextmanager
import logging
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Asegurar UTF-8 en consolas Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config import settings
from backend.app.database import Base, engine
from backend.app.routes import cameras, persons, alerts, ws, internal
from backend.app.redis_listener import redis_alerts_listener

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backend.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 0. Validación temprana (fail-fast) de INTERNAL_API_SECRET
    settings.validate_internal_secret()
    logger.info(f"[✓] Validación de seguridad interna exitosa (Entorno: {settings.ENVIRONMENT}).")

    # 1. Crear tablas de base de datos
    logger.info("[*] Verificando y creando esquemas de base de datos...")
    Base.metadata.create_all(bind=engine)
    logger.info("[✓] Tablas de base de datos inicializadas.")

    # 2. Iniciar listener de Redis en segundo plano
    redis_task = asyncio.create_task(redis_alerts_listener())

    yield

    # Cancelar listener de Redis al apagar
    redis_task.cancel()
    try:
        await redis_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="API Gateway y servidor de WebSockets para el Sistema de Reconocimiento Facial Distribuido.",
    version="1.0.0",
    lifespan=lifespan,
)

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servidor dinámico de almacenamiento de evidencias y fotos (Local + MinIO)
uploads_dir = Path("backend/uploads")
uploads_dir.mkdir(parents=True, exist_ok=True)

@app.get("/api/storage/{filename}")
def get_stored_file(filename: str):
    from fastapi.responses import Response, FileResponse
    from fastapi import HTTPException
    from backend.app.storage import storage_client

    local_path = uploads_dir / filename
    if local_path.is_file():
        return FileResponse(str(local_path), media_type="image/jpeg")

    data = storage_client.get_image_bytes(filename)
    if data is not None:
        return Response(content=data, media_type="image/jpeg")

    raise HTTPException(status_code=404, detail="Archivo de imagen no encontrado.")

# Registrar Rutas REST y WebSocket
app.include_router(cameras.router, prefix=settings.API_V1_STR)
app.include_router(internal.router, prefix=settings.API_V1_STR)
app.include_router(persons.router, prefix=settings.API_V1_STR)
app.include_router(alerts.router, prefix=settings.API_V1_STR)
app.include_router(ws.router, prefix=settings.API_V1_STR)


@app.get("/")
def root():
    return {
        "status": "online",
        "service": settings.PROJECT_NAME,
        "docs_url": "/docs",
        "ws_endpoint": f"{settings.API_V1_STR}/ws/alerts",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
