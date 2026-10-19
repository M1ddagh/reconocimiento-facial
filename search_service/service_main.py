#!/usr/bin/env python3
from __future__ import annotations

"""
search_service/service_main.py
------------------------------
Servicio de Motor Vectorial para Reconocimiento Facial a Gran Escala.
Consume eventos biométricos de la cola Redis, consulta el índice FAISS
y emite alertas en tiempo real al canal de notificaciones 'alerts_channel'.
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
import uuid
from pathlib import Path

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

from search_service.vector_indexer import FaceSearchEngine

logger = logging.getLogger("search_service")


def setup_logger(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
    logging.basicConfig(level=level, format=format_str, datefmt="%H:%M:%S")


class SearchServiceWorker:
    """
    Consumidor de cola de eventos y despachador de alertas de coincidencias faciales.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        queue_name: str = "face_events_queue",
        alerts_channel: str = "alerts_channel",
        index_path: str = "search_service/faces.index",
        similarity_threshold: float = 0.65,
    ):
        self.redis_url = redis_url
        self.queue_name = queue_name
        self.alerts_channel = alerts_channel
        self.running = False

        # Inicializar motor de búsqueda FAISS
        self.engine = FaceSearchEngine(
            index_path=index_path,
            similarity_threshold=similarity_threshold,
        )
        self.last_index_mtime = (
            self.engine.index_path.stat().st_mtime
            if self.engine.index_path.exists()
            else 0
        )

        self.redis_client = None
        self._init_redis()

        # Métricas
        self.events_processed = 0
        self.matches_found = 0

    def _check_and_reload_index_if_needed(self) -> None:
        """Verifica si el archivo faces.index ha cambiado en disco y recarga en memoria."""
        try:
            if self.engine.index_path.exists():
                mtime = self.engine.index_path.stat().st_mtime
                if mtime > self.last_index_mtime:
                    logger.info("[!] Cambio detectado en 'faces.index'. Recargando índice en caliente...")
                    self.engine.reload()
                    self.last_index_mtime = mtime
        except Exception as e:
            logger.warning(f"Error comprobando estado del índice: {e}")

    def _init_redis(self) -> None:
        try:
            import redis

            self.redis_client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=3.0,
            )
            self.redis_client.ping()
            logger.info(f"[✓] Conexión establecida con Redis: {self.redis_url}")
        except Exception as err:
            logger.error(
                f"[!] No se pudo conectar a Redis en '{self.redis_url}': {err}. "
                "Asegúrate de que el servidor Redis esté activo."
            )
            self.redis_client = None

    def handle_event(self, raw_event_json: str) -> None:
        """Procesa un evento individual deserializado de Redis."""
        self.events_processed += 1
        try:
            event_data = json.loads(raw_event_json)
        except json.JSONDecodeError as err:
            logger.error(f"Error decodificando JSON de evento: {err}")
            return

        camera_id = event_data.get("camera_id", "desconocida")
        track_id = event_data.get("track_id")
        embedding = event_data.get("embedding")

        if not embedding:
            logger.warning(f"Evento {event_data.get('event_id')} no contiene vector embedding.")
            return

        # Verificar si hay nuevas identidades indexadas
        self._check_and_reload_index_if_needed()

        # Búsqueda de alta velocidad en FAISS
        search_res = self.engine.search(embedding)

        if search_res["is_match"]:
            self.matches_found += 1
            subject_id = search_res["matched_id"]
            subject_name = search_res["subject_name"] or f"Sujeto #{subject_id}"
            similarity = search_res["similarity"]

            logger.info(
                f"\n{'='*70}\n"
                f"🚨 ¡ALERTA DE COINCIDENCIA (MATCH)! 🚨\n"
                f" • Sujeto Identificado : {subject_name} (ID #{subject_id})\n"
                f" • Similitud Coseno    : {similarity:.4f} (Umbral: {search_res['threshold']})\n"
                f" • Cámara              : {camera_id} | Track #{track_id}\n"
                f" • Tiempo de Búsqueda  : {search_res['search_time_ms']:.3f} ms\n"
                f"{'='*70}"
            )

            # Construir payload de alerta
            alert_payload = {
                "alert_id": str(uuid.uuid4()),
                "timestamp": event_data.get("timestamp", time.time()),
                "camera_id": camera_id,
                "track_id": track_id,
                "event_id": event_data.get("event_id"),
                "matched_subject_id": subject_id,
                "subject_name": subject_name,
                "similarity": similarity,
                "status": "pending",
                "bbox": event_data.get("bbox"),
                "crop_image_base64": event_data.get("crop_image_base64"),
                "metadata": search_res.get("metadata"),
            }


            # Notificar vía Redis PubSub a FastAPI / WebSockets
            if self.redis_client:
                try:
                    self.redis_client.publish(self.alerts_channel, json.dumps(alert_payload))
                except Exception as ex:
                    logger.error(f"Error publicando alerta al canal Redis: {ex}")
        else:
            logger.debug(
                f"Event {event_data.get('event_id')}: Desconocido (Similitud máx: {search_res['similarity']:.3f})"
            )

    def start(self) -> None:
        """Inicia el bucle de consumo de eventos de la cola Redis (BLPOP)."""
        if not self.redis_client:
            logger.error("No hay cliente Redis disponible para iniciar el worker.")
            return

        self.running = True
        logger.info(
            f"[*] Search Service en espera de eventos en la cola '{self.queue_name}' "
            f"(Canal alertas: '{self.alerts_channel}')..."
        )

        while self.running:
            try:
                self._check_and_reload_index_if_needed()
                # BLPOP bloquea hasta que haya un elemento disponible o expire el timeout
                result = self.redis_client.blpop([self.queue_name], timeout=2)
                if result:
                    _, raw_data = result
                    self.handle_event(raw_data)
            except KeyboardInterrupt:
                break
            except Exception as ex:
                if self.running:
                    logger.error(f"Error consumiendo cola Redis: {ex}")
                    time.sleep(1)

        logger.info(
            f"[✓] Search Service detenido. Total eventos: {self.events_processed}, Matches: {self.matches_found}"
        )

    def stop(self) -> None:
        self.running = False


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search Service - Motor Vectorial FAISS y Consumidor de Eventos."
    )
    parser.add_argument(
        "--redis-url",
        type=str,
        default=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        help="URL del servidor Redis (por defecto: env REDIS_URL o 'redis://localhost:6379/0').",
    )
    parser.add_argument(
        "--queue-name",
        type=str,
        default=os.getenv("QUEUE_NAME", "face_events_queue"),
        help="Nombre de la cola de eventos en Redis.",
    )
    parser.add_argument(
        "--alerts-channel",
        type=str,
        default=os.getenv("ALERTS_CHANNEL", "alerts_channel"),
        help="Canal Pub/Sub de Redis para publicar alertas de coincidencia.",
    )
    parser.add_argument(
        "--index-path",
        type=str,
        default=os.getenv("INDEX_PATH", "search_service/faces.index"),
        help="Ruta al archivo de índice FAISS (.index).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=float(os.getenv("SIMILARITY_THRESHOLD", "0.65")),
        help="Umbral de similitud coseno para emitir coincidencia (por defecto: 0.65).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Habilitar salida detallada.",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()
    setup_logger(args.verbose)

    logger.info("=" * 65)
    logger.info("   INICIANDO SEARCH SERVICE (FAISS + REDIS CONSUMER)")
    logger.info("=" * 65)
    logger.info(f" • Índice FAISS    : {args.index_path}")
    logger.info(f" • Umbral Match    : {args.threshold}")
    logger.info(f" • Cola Ingesta    : {args.queue_name}")
    logger.info(f" • Canal Alertas   : {args.alerts_channel}")
    logger.info("=" * 65)

    worker = SearchServiceWorker(
        redis_url=args.redis_url,
        queue_name=args.queue_name,
        alerts_channel=args.alerts_channel,
        index_path=args.index_path,
        similarity_threshold=args.threshold,
    )

    def signal_handler(sig, frame):
        logger.info("\nSeñal de terminación recibida. Cerrando servicio...")
        worker.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    worker.start()


if __name__ == "__main__":
    main()
