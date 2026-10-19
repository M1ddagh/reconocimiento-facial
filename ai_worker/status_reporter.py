#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/status_reporter.py
----------------------------
Publicador periódico de telemetría y latidos (heartbeat) del AI Worker en Redis.
Garantiza:
- Clave efímera con TTL configurable (por defecto 10s renovado cada 3s).
- Prueba de progreso real (last_frame_timestamp, frames_processed, fps).
- Hilo desacoplado daemon con detención instantánea mediante threading.Event.
- Sanitización obligatoria de credenciales en URLs y mensajes de error.
- Tolerancia a caídas de Redis (no bloquea el worker si Redis no responde).
- Publicación de estado final explícito (stopped o error) en parada ordenada.
"""

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from ai_worker.capture_stream import sanitize_url

logger = logging.getLogger("ai_worker.status_reporter")


class StatusReporter:
    """
    Gestiona el ciclo de vida de la telemetría en Redis bajo la clave:
    worker:status:{camera_id}
    """

    def __init__(
        self,
        camera_id: str,
        redis_url: Optional[str] = "redis://localhost:6379/0",
        update_interval: float = 3.0,
        ttl_seconds: int = 10,
        metrics_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ):
        self.camera_id = camera_id
        self.redis_url = redis_url
        self.sanitized_redis_url = sanitize_url(redis_url) if redis_url else "None"
        self.update_interval = max(0.5, float(update_interval))
        self.ttl_seconds = max(2, int(ttl_seconds))
        self.metrics_provider = metrics_provider

        self.current_status: str = "starting"
        self.started_at: float = time.time()
        self.last_error: Optional[str] = None

        self._redis_client = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Clave en Redis
        self.redis_key = f"worker:status:{self.camera_id}"

    def set_metrics_provider(self, provider: Callable[[], Dict[str, Any]]) -> None:
        """Asigna el proveedor de métricas (habitualmente VideoProcessor.get_metrics)."""
        with self._lock:
            self.metrics_provider = provider

    def set_status(self, status: str, error: Optional[str] = None) -> None:
        """Actualiza manualmente el estado y error."""
        with self._lock:
            self.current_status = status
            if error is not None:
                self.last_error = error

    def _get_redis_client(self):
        """Inicializa o retorna la conexión de Redis."""
        if self._redis_client is None and self.redis_url:
            try:
                import redis

                self._redis_client = redis.Redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2.0,
                )
            except Exception as ex:
                logger.warning(f"[StatusReporter] No se pudo instanciar cliente Redis: {ex}")
                self._redis_client = None
        return self._redis_client

    def build_payload(self) -> Dict[str, Any]:
        """Construye el diccionario de telemetría asegurando sanitización de errores."""
        now = time.time()
        metrics = {}
        if self.metrics_provider:
            try:
                metrics = self.metrics_provider() or {}
            except Exception as ex:
                logger.debug(f"[StatusReporter] Error al consultar metrics_provider: {ex}")

        status = metrics.get("status") or self.current_status
        last_error = metrics.get("last_error") or self.last_error
        sanitized_error = sanitize_url(last_error) if last_error else None

        payload = {
            "camera_id": self.camera_id,
            "status": status,
            "pid": os.getpid(),
            "fps": float(metrics.get("fps", 0.0)),
            "last_frame_timestamp": float(metrics.get("last_frame_timestamp", 0.0)),
            "frames_processed": int(metrics.get("frames_processed", 0)),
            "total_frames_read": int(metrics.get("total_frames_read", 0)),
            "reconnect_attempts": int(metrics.get("reconnect_attempts", 0)),
            "events_published": int(metrics.get("events_published", 0)),
            "events_failed": int(metrics.get("events_failed", 0)),
            "last_error": sanitized_error,
            "started_at": self.started_at,
            "updated_at": now,
        }
        return payload

    def emit_heartbeat(self, status_override: Optional[str] = None, error_override: Optional[str] = None) -> bool:
        """
        Emite un latido a Redis de forma síncrona.
        Retorna True si fue exitoso, False si falló.
        """
        payload = self.build_payload()
        if status_override:
            payload["status"] = status_override
        if error_override:
            payload["last_error"] = sanitize_url(error_override)
        payload["updated_at"] = time.time()

        client = self._get_redis_client()
        if not client:
            return False

        try:
            payload_json = json.dumps(payload)
            client.setex(self.redis_key, self.ttl_seconds, payload_json)
            logger.debug(f"[Heartbeat] {self.redis_key} -> {payload['status']} (TTL {self.ttl_seconds}s)")
            return True
        except Exception as ex:
            logger.warning(f"[Heartbeat] Error al emitir latido a Redis ({self.sanitized_redis_url}): {ex}")
            # Invalidar cliente si falló la conexión para reintentar en el próximo ciclo
            self._redis_client = None
            return False

    def start(self) -> None:
        """Inicia el hilo emisor de heartbeat y publica el estado inicial."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            # Emitir primer latido de inmediato (ej. 'starting')
            self.emit_heartbeat()
            self._thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"StatusReporter-{self.camera_id}",
                daemon=True,
            )
            self._thread.start()
            logger.info(f"[+] StatusReporter iniciado para '{self.camera_id}' (intervalo {self.update_interval}s)")

    def _heartbeat_loop(self) -> None:
        """Bucle periódico en segundo plano."""
        while not self._stop_event.is_set():
            # Espera interrumpible
            interrupted = self._stop_event.wait(self.update_interval)
            if interrupted:
                break
            self.emit_heartbeat()

    def stop(self, final_status: str = "stopped", error: Optional[str] = None) -> None:
        """
        Detiene el hilo emisor y emite el estado final a Redis.
        Garantiza no bloquear ni colgar si Redis está caído.
        """
        with self._lock:
            self._stop_event.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            self._thread = None

            # Intentar emitir el latido final
            try:
                self.emit_heartbeat(status_override=final_status, error_override=error)
            except Exception as ex:
                logger.warning(f"[StatusReporter] No se pudo emitir latido final ({final_status}): {ex}")

            # Cerrar cliente
            if self._redis_client:
                try:
                    self._redis_client.close()
                except Exception:
                    pass
                self._redis_client = None

            logger.info(f"[-] StatusReporter detenido para '{self.camera_id}' (estado final: {final_status})")
