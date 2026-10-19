#!/usr/bin/env python3
from __future__ import annotations

"""
backend/app/telemetry.py
-------------------------
Módulo para la consulta resiliente del estado de los AI Workers en Redis,
enmascaramiento seguro de URLs RTSP, validación estricta de payloads
y reconciliación canónica de estados.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import redis
from backend.app.config import settings

logger = logging.getLogger("backend.telemetry")

_redis_client_instance: Optional[redis.Redis] = None

VALID_WORKER_STATUSES = {"starting", "running", "reconnecting", "stopped", "error"}


def sanitize_rtsp_url(url: Optional[str]) -> Optional[str]:
    """
    Enmascara contraseñas en URLs RTSP, HTTP y Redis (ej. 'rtsp://user:pass@host' -> 'rtsp://user:****@host').
    Si no contiene credenciales o no es string, lo devuelve inalterado.
    """
    if not url or not isinstance(url, str):
        return url
    return re.sub(r"://([^:@\s]*):([^@\s]+)@", r"://\1:****@", url)


def validate_and_sanitize_payload(raw_val: str, key: str) -> Optional[Dict[str, Any]]:
    """
    Parsea, valida y sanitiza el payload JSON proveniente de Redis.
    Evita exponer credenciales en logs y previene excepciones por datos corruptos o tipos anómalos.
    Retorna el diccionario estructurado o None si el payload es inválido.
    """
    if not isinstance(raw_val, str) or not raw_val.strip():
        logger.warning(f"[Telemetry] Payload vacío en clave Redis '{key}'.")
        return None

    try:
        data = json.loads(raw_val)
    except json.JSONDecodeError:
        logger.warning(f"[Telemetry] JSON corrupto en clave Redis '{key}' (longitud: {len(raw_val)} bytes).")
        return None

    if not isinstance(data, dict):
        logger.warning(f"[Telemetry] El payload en clave Redis '{key}' no es un objeto JSON.")
        return None

    raw_status = data.get("status")
    if not isinstance(raw_status, str) or not raw_status.strip():
        logger.warning(f"[Telemetry] Campo 'status' faltante o no string en clave Redis '{key}'.")
        return None

    status_clean = raw_status.strip().lower()
    if status_clean not in VALID_WORKER_STATUSES:
        logger.warning(f"[Telemetry] Estado no reconocido '{status_clean}' en clave Redis '{key}'.")
        return None

    try:
        pid = int(data["pid"]) if data.get("pid") is not None else None
        fps = max(0.0, float(data.get("fps", 0.0)))
        frames_processed = max(0, int(data.get("frames_processed", 0)))
        total_frames_read = max(0, int(data.get("total_frames_read", 0)))
        last_frame_ts = max(0.0, float(data.get("last_frame_timestamp", 0.0)))
        reconnect_attempts = max(0, int(data.get("reconnect_attempts", 0)))
        events_published = max(0, int(data.get("events_published", 0)))
        events_failed = max(0, int(data.get("events_failed", 0)))
        last_error = sanitize_rtsp_url(str(data["last_error"])) if data.get("last_error") is not None else None
        started_at = float(data["started_at"]) if data.get("started_at") is not None else None
        updated_at = float(data["updated_at"]) if data.get("updated_at") is not None else None

        return {
            "status": status_clean,
            "pid": pid,
            "fps": fps,
            "frames_processed": frames_processed,
            "total_frames_read": total_frames_read,
            "last_frame_timestamp": last_frame_ts,
            "reconnect_attempts": reconnect_attempts,
            "events_published": events_published,
            "events_failed": events_failed,
            "last_error": last_error,
            "started_at": started_at,
            "updated_at": updated_at,
        }
    except (ValueError, TypeError) as ex:
        logger.warning(f"[Telemetry] Error de tipos de datos en telemetría para clave '{key}': {ex}")
        return None


def get_redis_client() -> Optional[redis.Redis]:
    """Retorna un cliente Redis reutilizable configurado con timeouts acotados."""
    global _redis_client_instance
    if not settings.ENABLE_REDIS:
        return None
    if _redis_client_instance is None:
        try:
            _redis_client_instance = redis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
        except Exception as ex:
            logger.warning(f"[Telemetry] No se pudo instanciar cliente Redis: {ex}")
            return None
    return _redis_client_instance


def fetch_worker_statuses(camera_ids: List[str]) -> Tuple[Dict[str, Optional[Any]], bool]:
    """
    Consulta en lote (MGET) el estado de múltiples cámaras en Redis de forma atómica y eficiente.
    Retorna: (dict_mapa_camera_id_a_payload, redis_available: bool)
    """
    if not camera_ids:
        return {}, True

    client = get_redis_client()
    if client is None:
        return {cid: None for cid in camera_ids}, False

    keys = [f"worker:status:{cid}" for cid in camera_ids]
    try:
        raw_vals = client.mget(keys)
        results: Dict[str, Optional[Any]] = {}
        for cid, raw_val in zip(camera_ids, raw_vals):
            if raw_val is None:
                results[cid] = None
            else:
                key = f"worker:status:{cid}"
                parsed = validate_and_sanitize_payload(raw_val, key)
                results[cid] = parsed if parsed is not None else "INVALID_PAYLOAD"
        return results, True
    except (redis.ConnectionError, redis.TimeoutError) as ex:
        logger.warning(f"[Telemetry] Redis no disponible durante mget de {len(keys)} claves: {ex}")
        return {cid: None for cid in camera_ids}, False
    except Exception as ex:
        logger.error(f"[Telemetry] Error inesperado durante mget: {ex}")
        return {cid: None for cid in camera_ids}, False


def fetch_worker_status(camera_id: str) -> Tuple[Optional[Any], bool]:
    """
    Consulta en Redis la clave 'worker:status:{camera_id}'.
    Retorna: (payload_dict_o_indicador, redis_available: bool)
    - Si Redis está caído o inaccesible: (None, False)
    - Si la clave no existe o expiró: (None, True)
    - Si el payload es inválido: ("INVALID_PAYLOAD", True)
    - Si es válido: (dict_validado_y_sanitizado, True)
    """
    results, redis_available = fetch_worker_statuses([camera_id])
    return results.get(camera_id), redis_available


def purge_worker_status(camera_id: str) -> bool:
    """Elimina la clave de estado en Redis para una cámara borrada."""
    client = get_redis_client()
    if client is None:
        return False
    key = f"worker:status:{camera_id}"
    try:
        client.delete(key)
        return True
    except Exception as ex:
        logger.warning(f"[Telemetry] No se pudo purgar clave Redis '{key}': {ex}")
        return False


def resolve_camera_status(
    is_active: bool,
    redis_data: Optional[Any],
    redis_available: bool,
    stale_threshold_seconds: float = 15.0,
    current_time: Optional[float] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Resuelve el estado operativo canónico y adjunta la telemetría asociada.
    Taxonomía canónica:
    - stopped: Si is_active=False (cámara administrativamente apagada).
    - status_unavailable: Si Redis está caído o el payload es inválido.
    - offline: Si is_active=True pero no hay worker reportando (clave expirada o worker 'stopped').
    - starting: Si el worker reporta inicialización de modelos.
    - reconnecting: Si el worker reporta reconexión RTSP activa.
    - error: Si el worker reporta fallo fatal.
    - stalled: Si el worker reporta 'running' pero last_frame_timestamp > 15s.
    - running: Si el worker reporta 'running' con frames recientes.
    """
    # 1. Cámara administrativamente apagada
    if not is_active:
        return "stopped", None

    # 2. Infraestructura Redis no disponible o payload corrupto/inválido
    if not redis_available or redis_data == "INVALID_PAYLOAD":
        return "status_unavailable", None

    # 3. Sin latido o clave ausente
    if redis_data is None:
        return "offline", None

    # 4. Evaluación de payload de telemetría de worker activo
    if isinstance(redis_data, dict):
        reported_status = redis_data.get("status", "unknown")
        last_frame_ts = float(redis_data.get("last_frame_timestamp") or 0.0)
        now = current_time if current_time is not None else time.time()

        # Si el worker reportó 'stopped' mientras is_active=True, significa que el worker murió o se cerró
        if reported_status == "stopped":
            return "offline", redis_data

        if reported_status == "running":
            # Watchdog de estancamiento
            if last_frame_ts > 0.0 and (now - last_frame_ts > stale_threshold_seconds):
                return "stalled", redis_data
            return "running", redis_data

        if reported_status in ("starting", "reconnecting", "error"):
            return reported_status, redis_data

        return reported_status, redis_data

    return "status_unavailable", None
