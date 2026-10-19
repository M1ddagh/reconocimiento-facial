import asyncio
import json
import logging
import time
import uuid
from backend.app.config import settings
from backend.app.websocket_manager import ws_manager
from backend.app.database import SessionLocal
from backend.app.models import AlertEvent
from backend.app.storage import storage_client

logger = logging.getLogger("backend.redis_listener")


async def redis_alerts_listener():
    """
    Tarea en segundo plano que escucha el canal 'alerts_channel' en Redis,
    guarda la alerta en la base de datos y la retransmite inmediatamente a los WebSockets.
    """
    if not settings.ENABLE_REDIS:
        logger.info("[*] Listener de Redis deshabilitado (ENABLE_REDIS=False). Alertas en tiempo real desactivadas.")
        return

    try:
        import redis.asyncio as aioredis
    except ImportError:
        logger.warning("redis.asyncio no disponible. El listener de Redis se deshabilitará.")
        return

    logger.info(f"[*] Iniciando listener asíncrono de alertas en Redis: {settings.REDIS_URL} (Canal: {settings.ALERTS_CHANNEL})")

    retry_count = 0
    while True:
        try:
            r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe(settings.ALERTS_CHANNEL)
            logger.info(f"[✓] Conexión establecida con Redis pub/sub: {settings.ALERTS_CHANNEL}")
            retry_count = 0

            async for message in pubsub.listen():
                if message["type"] == "message":
                    raw_data = message["data"]
                    try:
                        alert_dict = json.loads(raw_data)

                        # Guardar crop en MinIO / Storage si viene en base64
                        crop_url = None
                        if alert_dict.get("crop_image_base64"):
                            crop_url = storage_client.save_base64_crop(alert_dict["crop_image_base64"])
                            alert_dict["crop_url"] = crop_url

                        # Persistir en la base de datos relacional
                        db = SessionLocal()
                        try:
                            alert_db = AlertEvent(
                                alert_id=alert_dict.get("alert_id") or str(uuid.uuid4()),
                                camera_id=alert_dict.get("camera_id") or "desconocida",
                                matched_subject_id=alert_dict.get("matched_subject_id") or 0,
                                subject_name=alert_dict.get("subject_name"),
                                similarity=float(alert_dict.get("similarity", 0.0)),
                                bbox=str(alert_dict.get("bbox") or ""),
                                crop_url=crop_url,
                                timestamp=float(alert_dict.get("timestamp") or time.time()),
                                status="pending",
                            )
                            db.add(alert_db)
                            db.commit()
                            db.refresh(alert_db)

                            # Retransmitir en tiempo real a los dashboards vía WebSockets
                            # Estructura idéntica al esquema AlertResponse de la API REST
                            ws_payload = {
                                "id": alert_db.id,
                                "alert_id": alert_db.alert_id,
                                "camera_id": alert_db.camera_id,
                                "matched_subject_id": alert_db.matched_subject_id,
                                "subject_name": alert_db.subject_name or f"Sujeto #{alert_db.matched_subject_id}",
                                "similarity": float(alert_db.similarity),
                                "bbox": alert_db.bbox,
                                "crop_url": alert_db.crop_url,
                                "status": "pending",
                                "timestamp": float(alert_db.timestamp),
                            }
                        finally:
                            db.close()

                        await ws_manager.broadcast({
                            "type": "NEW_ALERT",
                            "data": ws_payload,
                        })


                    except Exception as ex:
                        logger.error(f"Error procesando mensaje de alerta Redis: {ex}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            retry_count += 1
            delay = min(3 * retry_count, 15)
            if retry_count == 1:
                logger.warning(
                    f"No se pudo conectar a Redis en '{settings.REDIS_URL}': {e}.\n"
                    f"     -> TIP: Para alertas en tiempo real, levanta Redis (ej. 'docker compose up -d redis') "
                    f"o usa 'set ENABLE_REDIS=false' para omitir Redis en local.\n"
                    f"     Reintentando en segundo plano en {delay}s..."
                )
            else:
                logger.info(f"Reintentando conexión con Redis ({settings.REDIS_URL}) en {delay}s...")
            await asyncio.sleep(delay)
