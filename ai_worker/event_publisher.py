#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/event_publisher.py
----------------------------
Publicador de eventos biométricos hacia Redis (Colas / Lists o PubSub).
Garantiza:
- dry_run explícito únicamente si se solicita por parámetro.
- Registro honesto de métricas (total_published y events_failed).
- Reintento de reconexión ante fallos temporales de conexión con Redis.
"""

import json
import logging
import time
from typing import Optional, Callable
from ai_worker.schemas import FaceDetectionEvent
from ai_worker.capture_stream import sanitize_url

logger = logging.getLogger("ai_worker.event_publisher")


class EventPublisher:
    """
    Publica eventos de detección y tracking a una cola Redis (RPUSH / LPUSH).
    """

    def __init__(
        self,
        redis_url: Optional[str] = "redis://localhost:6379/0",
        queue_name: str = "face_events_queue",
        dry_run: bool = False,
    ):
        self.redis_url = redis_url
        self.queue_name = queue_name
        self.dry_run = dry_run
        self.redis_client = None
        self.total_published: int = 0
        self.events_failed: int = 0
        self.last_error: Optional[str] = None
        self.last_publish_timestamp: Optional[float] = None

        if not self.dry_run and self.redis_url:
            self._connect()

    def _connect(self) -> bool:
        """
        Intenta conectar con Redis y realizar ping para validar conectividad.

        Contrato:
        - Retorna True si la conexión y el ping fueron exitosos.
        - Retorna False si ocurrió cualquier excepción y mantiene self.redis_client = None.
        """
        sanitized_url = sanitize_url(self.redis_url)
        try:
            import redis

            self.redis_client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2.0,
            )
            self.redis_client.ping()
            self.last_error = None
            logger.info(f"[✓] Conexión establecida con Redis en: {sanitized_url} (Cola: {self.queue_name})")
            return True
        except Exception as err:
            self.redis_client = None
            self.last_error = str(err)
            logger.error(
                f"[!] No se pudo conectar a Redis en '{sanitized_url}': {err}. "
                "Los eventos no podrán ser publicados hasta que Redis esté disponible."
            )
            return False

    def publish(
        self,
        event: FaceDetectionEvent,
        on_published: Optional[Callable[[FaceDetectionEvent, float], None]] = None,
    ) -> bool:
        """
        Publica un evento FaceDetectionEvent en la cola Redis (vía RPUSH).
        Retorna True si fue publicado (o si dry_run=True explícito), False si falló.
        Si se provee on_published, se le pasa el evento y el timestamp time.perf_counter().
        """
        if self.dry_run:
            t_published = time.perf_counter()
            self.last_publish_timestamp = t_published
            self.total_published += 1
            if self.total_published % 10 == 1:
                logger.debug(
                    f"[Dry-run] Evento simulado publicado: camera={event.camera_id}, "
                    f"track_id={event.track_id}, frame={event.frame_id}, score={event.det_score:.3f}"
                )
            if on_published:
                try:
                    on_published(event, t_published)
                except Exception as cb_err:
                    logger.warning(f"Error en callback on_published (dry-run): {cb_err}")
            return True

        # Si el cliente no está conectado, intentar reconectar
        if self.redis_client is None:
            if not self._connect():
                self.events_failed += 1
                return False

        payload = event.model_dump_json()

        try:
            self.redis_client.rpush(self.queue_name, payload)
            t_published = time.perf_counter()
            self.last_publish_timestamp = t_published
            self.total_published += 1
            if on_published:
                try:
                    on_published(event, t_published)
                except Exception as cb_err:
                    logger.warning(f"Error en callback on_published: {cb_err}")
            return True
        except Exception as ex:
            self.events_failed += 1
            self.last_error = str(ex)
            logger.error(f"[!] Error al publicar evento en Redis ({self.queue_name}): {ex}")
            # Invalidar cliente para forzar reconexión en el siguiente intento
            self.redis_client = None
            return False

    def close(self) -> None:
        if self.redis_client:
            try:
                self.redis_client.close()
            except Exception:
                pass
            self.redis_client = None
