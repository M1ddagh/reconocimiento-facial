#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/worker_main.py
------------------------
Punto de entrada principal para el servicio AI Worker.
Encargado de la ingesta de video / transmisiones en tiempo real, detección y extracción biométrica,
seguimiento con ByteTrack y publicación de eventos biométricos hacia Redis.
"""

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

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

from ai_worker.event_publisher import EventPublisher
from ai_worker.video_processor import VideoProcessor
from ai_worker.capture_stream import sanitize_rtsp_url
from ai_worker.status_reporter import StatusReporter


def setup_logger(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
    logging.basicConfig(level=level, format=format_str, datefmt="%H:%M:%S")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Worker - Ingesta de Video, ByteTrack y Extracción Biométrica Facial."
    )
    parser.add_argument(
        "--source",
        type=str,
        default=os.getenv("VIDEO_SOURCE", "0"),
        help="Fuente de video: índice de cámara web ('0', '1'), ruta de archivo .mp4 o URL RTSP.",
    )
    parser.add_argument(
        "--camera-id",
        type=str,
        default=os.getenv("CAMERA_ID", "camera_01"),
        help="Identificador de la cámara asignada (por defecto: 'camera_01').",
    )
    parser.add_argument(
        "--skip-frames",
        type=int,
        default=int(os.getenv("SKIP_FRAMES", "3")),
        help="Omisión de fotogramas para balancear la carga de inferencia (por defecto: 3).",
    )
    parser.add_argument(
        "--det-size",
        type=int,
        nargs=2,
        default=[640, 640],
        metavar=("WIDTH", "HEIGHT"),
        help="Resolución de entrada para el detector RetinaFace (por defecto: 640 640).",
    )
    parser.add_argument(
        "--use-cpu",
        action="store_true",
        default=os.getenv("USE_CPU", "true").lower() in ("true", "1", "yes"),
        help="Forzar ejecución en CPU con OnnxRuntime.",
    )
    parser.add_argument(
        "--redis-url",
        type=str,
        default=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        help="URL de conexión a Redis (por defecto: 'redis://localhost:6379/0').",
    )
    parser.add_argument(
        "--queue-name",
        type=str,
        default=os.getenv("QUEUE_NAME", "face_events_queue"),
        help="Nombre de la lista/cola en Redis donde se publicarán los eventos.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ejecutar en modo de prueba sin enviar eventos a Redis.",
    )
    parser.add_argument(
        "--emit-interval",
        type=float,
        default=3.0,
        help="Intervalo mínimo en segundos entre emisiones de eventos para un mismo sujeto (por defecto: 3.0s).",
    )
    parser.add_argument(
        "--show",
        "--show-preview",
        dest="show",
        action="store_true",
        help="Mostrar ventana de visualización en vivo con OpenCV.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Ruta de archivo MP4 donde guardar el video resultante anotado.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Límite opcional de fotogramas a procesar (útil para pruebas o benchmarks).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Habilitar salida detallada de depuración.",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()
    setup_logger(args.verbose)
    logger = logging.getLogger("ai_worker")

    sanitized_src = sanitize_rtsp_url(args.source)
    logger.info("=" * 65)
    logger.info("   INICIANDO AI WORKER - PIPELINE DE EXTRACCIÓN Y BYTE TRACK")
    logger.info("=" * 65)
    logger.info(f" • Cámara ID       : {args.camera_id}")
    logger.info(f" • Fuente          : {sanitized_src}")
    logger.info(f" • Frame Skip      : {args.skip_frames} (1 de cada {args.skip_frames} frames)")
    logger.info(f" • Modo ejecución  : {'CPU' if args.use_cpu else 'GPU/CUDA (fallback CPU)'}")
    logger.info(f" • Cola Redis      : {args.queue_name} ({'Dry-Run' if args.dry_run else args.redis_url})")
    logger.info("=" * 65)

    # 1. Iniciar inmediatamente el StatusReporter reportando 'starting'
    reporter = StatusReporter(
        camera_id=args.camera_id,
        redis_url=args.redis_url if not args.dry_run else None,
        update_interval=3.0,
        ttl_seconds=10,
    )
    reporter.start()

    # 2. Configurar manejador de señales limpio (cero I/O ni llamadas a objetos en el signal handler)
    stop_requested = threading.Event()

    def handle_signal(sig, frame):
        stop_requested.set()

    try:
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
    except Exception as ex:
        logger.debug(f"No se pudieron registrar manejadores de señales: {ex}")

    final_status = "stopped"
    final_error = None
    exit_code = 0
    processor: Optional[VideoProcessor] = None

    try:
        # 3. Configurar publicador de eventos hacia Redis
        publisher = EventPublisher(
            redis_url=args.redis_url,
            queue_name=args.queue_name,
            dry_run=args.dry_run,
        )

        # 4. Detectar configuración de benchmark desde variables de entorno
        benchmark_report_file = os.getenv("BENCHMARK_REPORT_FILE")
        benchmark_warmup = float(os.getenv("BENCHMARK_WARMUP_SECONDS", "10.0"))
        benchmark_duration = float(os.getenv("BENCHMARK_DURATION_SECONDS", "30.0"))
        enable_latency_tracking = bool(benchmark_report_file) or os.getenv("ENABLE_LATENCY_TRACKING", "false").lower() in ("true", "1", "yes")

        if benchmark_report_file:
            logger.info(
                f" • Modo Benchmark : ACTIVO (Warmup: {benchmark_warmup}s, Duración: {benchmark_duration}s, "
                f"Reporte: {benchmark_report_file})"
            )

        # 5. Configurar procesador de video con ByteTrack e InsightFace
        processor = VideoProcessor(
            source=args.source,
            camera_id=args.camera_id,
            skip_frames=args.skip_frames,
            det_size=tuple(args.det_size),
            use_cpu=args.use_cpu,
            publisher=publisher,
            emit_interval_seconds=args.emit_interval,
            show_window=args.show,
            output_path=args.output,
            enable_latency_tracking=enable_latency_tracking,
            benchmark_warmup_seconds=benchmark_warmup if benchmark_report_file else 0.0,
            benchmark_duration_seconds=benchmark_duration if benchmark_report_file else 0.0,
            benchmark_report_file=benchmark_report_file,
        )

        # Conectar proveedor de métricas al reporter
        reporter.set_metrics_provider(processor.get_metrics)

        # Si se solicitó detención durante la inicialización
        if stop_requested.is_set():
            logger.info("[!] Parada solicitada antes de iniciar la ingesta.")
            processor.stop(timeout=2.0)
            summary = {"status": "stopped"}
        else:
            summary = processor.run(max_frames=args.max_frames, stop_event=stop_requested)
            logger.info(f"[✓] Proceso completado exitosamente: {summary}")

        if stop_requested.is_set():
            logger.info("\n[!] Parada ordenada solicitada por señal externa. Finalizando de forma limpia...")
            final_status = "stopped"
            exit_code = 0
        elif summary.get("status") == "error":
            final_status = "error"
            final_error = summary.get("last_error")
            exit_code = 2
        else:
            final_status = "stopped"
            exit_code = 0

    except KeyboardInterrupt:
        logger.info("\n[!] Proceso detenido por el usuario (KeyboardInterrupt).")
        if processor:
            processor.stop(timeout=2.0)
        final_status = "stopped"
        exit_code = 0
    except RuntimeError as ex:
        logger.error(f"[ERROR INICIO] Fallo de apertura o configuración: {ex}")
        final_status = "error"
        final_error = str(ex)
        exit_code = 1
    except Exception as ex:
        logger.exception(f"[ERROR CRÍTICO] Fallo inesperado en el AI Worker: {ex}")
        final_status = "error"
        final_error = str(ex)
        exit_code = 1
    finally:
        # 5. Parada limpia síncrona en el hilo principal
        reporter.stop(final_status=final_status, error=final_error)
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
