#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/video_processor.py
----------------------------
Procesador de flujo de video con omisión de fotogramas (frame-skipping),
detección y extracción biométrica con InsightFace (ArcFace 512D),
seguimiento multi-objeto persistente con ByteTrack y publicación a cola Redis.
"""

import base64
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional, Callable

import cv2
import numpy as np
from insightface.app import FaceAnalysis

from ai_worker.tracker.byte_tracker import ByteTracker
from ai_worker.schemas import FaceDetectionEvent
from ai_worker.event_publisher import EventPublisher
from ai_worker.capture_stream import RTSPStreamCapture, sanitize_rtsp_url

logger = logging.getLogger("ai_worker.video_processor")


class VideoProcessor:
    """
    Pipeline de ingesta de video, detección facial, tracking con ByteTrack y emisión de eventos.
    """

    def __init__(
        self,
        source: str | int,
        camera_id: str = "camera_01",
        skip_frames: int = 3,
        det_size: tuple[int, int] = (640, 640),
        use_cpu: bool = False,
        publisher: Optional[EventPublisher] = None,
        emit_interval_seconds: float = 3.0,
        show_window: bool = False,
        output_path: Optional[str] = None,
        stream_capture: Optional[RTSPStreamCapture] = None,
        enable_latency_tracking: bool = False,
        benchmark_warmup_seconds: float = 0.0,
        benchmark_duration_seconds: float = 0.0,
        benchmark_report_file: Optional[str] = None,
    ):
        self.source = source
        self.camera_id = camera_id
        self.skip_frames = max(1, skip_frames)
        self.det_size = det_size
        self.use_cpu = use_cpu
        self.publisher = publisher
        self.emit_interval_seconds = emit_interval_seconds
        self.show_window = show_window
        self.output_path = output_path
        self.stream_capture = stream_capture or RTSPStreamCapture(source=self.source)
        self.enable_latency_tracking = enable_latency_tracking
        self.benchmark_warmup_seconds = benchmark_warmup_seconds
        self.benchmark_duration_seconds = benchmark_duration_seconds
        self.benchmark_report_file = benchmark_report_file
        self._stop_event = threading.Event()
        self._metrics_lock = threading.Lock()

        # Medición de latencia cuadro-a-evento e IPC de benchmark
        self._frame_capture_timestamps: dict[int, float] = {}
        self._latency_samples: list[float] = []
        self._benchmark_window_started: bool = False
        self._benchmark_window_start_time: Optional[float] = None
        self._benchmark_frames_processed: int = 0

        # Inicializar analizador de rostros
        self.face_app = self._init_face_analysis()

        # Inicializar ByteTracker
        self.tracker = ByteTracker(track_thresh=0.45, match_thresh=0.7, track_buffer=30)

        # Historial de emisiones por track_id: {track_id: {"last_time": float, "best_score": float}}
        self.track_emit_history: dict[int, dict] = {}

        # Métricas de rendimiento
        self.processed_frames = 0
        self.total_frames_read = 0
        self.last_frame_timestamp = 0.0
        self.fps = 0.0

    def _init_face_analysis(self) -> FaceAnalysis:
        providers = ["CPUExecutionProvider"] if self.use_cpu else ["CUDAExecutionProvider", "CPUExecutionProvider"]
        ctx_id = -1 if self.use_cpu else 0
        logger.info(f"[*] Inicializando InsightFace (buffalo_l, ctx_id={ctx_id}, det_size={self.det_size})...")
        app = FaceAnalysis(name="buffalo_l", providers=providers)
        app.prepare(ctx_id=ctx_id, det_size=self.det_size)
        logger.info("[✓] InsightFace inicializado exitosamente.")
        return app

    @staticmethod
    def _normalize_l2(vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        if norm == 0:
            norm = 1e-12
        return vector / norm

    @staticmethod
    def _crop_face(frame: np.ndarray, bbox: list[int], margin_ratio: float = 0.15) -> Optional[np.ndarray]:
        """Extrae el recorte del rostro con un margen adicional prudencial."""
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        bw = x2 - x1
        bh = y2 - y1

        mx = int(bw * margin_ratio)
        my = int(bh * margin_ratio)

        x1_m = max(0, x1 - mx)
        y1_m = max(0, y1 - my)
        x2_m = min(w, x2 + mx)
        y2_m = min(h, y2 + my)

        if x2_m <= x1_m or y2_m <= y1_m:
            return None
        return frame[y1_m:y2_m, x1_m:x2_m].copy()

    @staticmethod
    def _encode_crop_base64(crop_img: np.ndarray) -> Optional[str]:
        """Codifica el recorte a formato JPEG base64 para persistencia de evidencias."""
        success, encoded_img = cv2.imencode(".jpg", crop_img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            return None
        return base64.b64encode(encoded_img.tobytes()).decode("utf-8")

    def _should_emit_event(self, track_id: int, score: float, current_time: float) -> bool:
        """Determina si debe emitirse un nuevo evento para evitar inundar la cola."""
        if track_id not in self.track_emit_history:
            self.track_emit_history[track_id] = {
                "last_time": current_time,
                "best_score": score,
            }
            return True

        hist = self.track_emit_history[track_id]
        time_elapsed = current_time - hist["last_time"]

        # Si transcurrió el intervalo mínimo configurado o se obtuvo una detección significativamente mejor
        if time_elapsed >= self.emit_interval_seconds or (score > hist["best_score"] + 0.15 and time_elapsed > 1.0):
            hist["last_time"] = current_time
            hist["best_score"] = max(hist["best_score"], score)
            return True

        return False

    def process_frame(
        self,
        frame: np.ndarray,
        frame_idx: int,
        timestamp: float,
        on_event_cb: Optional[Callable[[FaceDetectionEvent], None]] = None,
    ) -> tuple[np.ndarray, list]:
        """
        Procesa un único fotograma: detección, tracking y generación de eventos.
        Retorna el fotograma anotado y las pistas activas.
        """
        detections_for_tracker = []
        annotated_frame = frame.copy()

        # Detección e inferencia de rostros con InsightFace
        faces = self.face_app.get(frame)

        for face in faces:
            bbox = face.bbox.astype(int).tolist()
            score = float(face.det_score)
            embedding = face.embedding

            # Normalización L2 unitaria requerida para búsqueda por Similitud Coseno
            emb_normalized = self._normalize_l2(embedding).astype(np.float32)

            detections_for_tracker.append({
                "bbox": bbox,
                "score": score,
                "face_data": {
                    "embedding": emb_normalized,
                    "gender": getattr(face, "gender", None),
                    "age": getattr(face, "age", None),
                },
            })

        # Actualización del Tracker multi-objeto
        active_tracks = self.tracker.update(detections_for_tracker)

        # Análisis de pistas y emisión de eventos biométricos
        for track in active_tracks:
            track_id = track.track_id
            tlbr = track.tlbr.astype(int).tolist()
            score = track.score
            face_data = track.face_data

            # Bounding box delimitador acotado a la imagen
            h, w = frame.shape[:2]
            x1 = max(0, min(w - 1, tlbr[0]))
            y1 = max(0, min(h - 1, tlbr[1]))
            x2 = max(0, min(w, tlbr[2]))
            y2 = max(0, min(h, tlbr[3]))
            clamped_bbox = [x1, y1, x2, y2]

            # Evaluar política de emisión
            if "embedding" in face_data and self._should_emit_event(track_id, score, timestamp):
                crop_img = self._crop_face(frame, clamped_bbox)
                crop_b64 = self._encode_crop_base64(crop_img) if crop_img is not None else None

                event = FaceDetectionEvent(
                    camera_id=self.camera_id,
                    track_id=track_id,
                    frame_id=frame_idx,
                    timestamp=timestamp,
                    bbox=clamped_bbox,
                    det_score=score,
                    embedding=face_data["embedding"].tolist(),
                    crop_image_base64=crop_b64,
                    gender=int(face_data["gender"]) if face_data.get("gender") is not None else None,
                    age=int(face_data["age"]) if face_data.get("age") is not None else None,
                )

                if self.publisher:
                    self.publisher.publish(
                        event,
                        on_published=self._on_event_published if self.enable_latency_tracking else None,
                    )
                if on_event_cb:
                    on_event_cb(event)

            # Dibujar anotaciones visuales
            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 255, 120), 2)
            label = f"ID: {track_id} | {score:.2f}"
            (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(annotated_frame, (x1, max(0, y1 - 20)), (x1 + lw, y1), (0, 255, 120), -1)
            cv2.putText(
                annotated_frame,
                label,
                (x1, max(14, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 0, 0),
                1,
                cv2.LINE_AA,
            )

        return annotated_frame, active_tracks

    def get_metrics(self) -> dict:
        """
        Retorna instantánea sincronizada de métricas operativas y de progreso para StatusReporter.
        Coordina la lectura coherente de fotogramas, fps y timestamps mediante _metrics_lock.
        """
        with self._metrics_lock:
            return {
                "status": self.stream_capture.status,
                "fps": self.fps,
                "last_frame_timestamp": self.last_frame_timestamp or self.stream_capture.last_frame_timestamp,
                "frames_processed": self.processed_frames,
                "total_frames_read": self.total_frames_read,
                "reconnect_attempts": self.stream_capture.reconnect_attempts,
                "events_published": self.publisher.total_published if self.publisher else 0,
                "events_failed": self.publisher.events_failed if self.publisher else 0,
                "last_error": self.stream_capture.last_error,
            }

    def is_stopped(self, stop_event: Optional[threading.Event] = None) -> bool:
        """Indica si se ha solicitado detener cooperativamente el procesamiento."""
        if self._stop_event.is_set():
            return True
        if stop_event is not None and stop_event.is_set():
            return True
        return self.stream_capture.status in ("stopped", "error")

    def stop(self, timeout: float = 2.0) -> None:
        """
        Detiene cooperativamente el pipeline de captura y procesamiento.
        Solicita el cierre real y seguro del capturador con un límite de espera.
        """
        self._stop_event.set()
        if hasattr(self, "stream_capture") and self.stream_capture is not None:
            self.stream_capture.close(timeout=timeout)

    def _on_event_published(self, event: FaceDetectionEvent, t_published: float) -> None:
        """
        Callback invocado por EventPublisher tras confirmación de rpush en Redis.
        Calcula la latencia cuadro-a-evento correlacionando event.frame_id con su marca de captura.
        """
        t_capture = self._frame_capture_timestamps.pop(event.frame_id, None)
        if t_capture is not None:
            latency_ms = (t_published - t_capture) * 1000.0
            # Si no hay benchmark activo o si la ventana sostenida ya inició
            if self.benchmark_duration_seconds <= 0 or self._benchmark_window_started:
                self._latency_samples.append(latency_ms)

    def _start_benchmark_window(self) -> None:
        """
        Inicia síncronamente la ventana de medición sostenida del benchmark, purgando muestras del warmup.
        """
        self._latency_samples.clear()
        self._benchmark_frames_processed = 0
        self._benchmark_window_started = True
        self._benchmark_window_start_time = time.perf_counter()
        logger.info(
            f"[BENCHMARK] Warmup completado ({self.benchmark_warmup_seconds}s). "
            f"Iniciando ventana de medición sostenida ({self.benchmark_duration_seconds}s)..."
        )

    def get_latency_stats(self) -> dict[str, float]:
        """
        Calcula métricas agregadas de latencia cuadro-a-evento en milisegundos.
        """
        if not self._latency_samples:
            return {
                "count": 0,
                "mean_ms": 0.0,
                "p50_ms": 0.0,
                "p95_ms": 0.0,
                "min_ms": 0.0,
                "max_ms": 0.0,
            }
        samples = sorted(self._latency_samples)
        n = len(samples)
        mean_val = sum(samples) / n
        min_val = samples[0]
        max_val = samples[-1]
        p50_val = samples[int(0.50 * (n - 1))]
        p95_val = samples[int(0.95 * (n - 1))]
        return {
            "count": n,
            "mean_ms": round(mean_val, 2),
            "p50_ms": round(p50_val, 2),
            "p95_ms": round(p95_val, 2),
            "min_ms": round(min_val, 2),
            "max_ms": round(max_val, 2),
        }

    def export_benchmark_report(self, file_path: str) -> dict:
        """
        Serializa las métricas del benchmark en un archivo JSON para consumo por IPC.
        """
        lat_stats = self.get_latency_stats()
        duration = self.benchmark_duration_seconds if self.benchmark_duration_seconds > 0 else 1.0
        fps = round(self._benchmark_frames_processed / duration, 2) if self._benchmark_window_started else self.fps
        report_data = {
            "camera_id": self.camera_id,
            "warmup_seconds": self.benchmark_warmup_seconds,
            "duration_seconds": self.benchmark_duration_seconds,
            "frames_processed": self._benchmark_frames_processed if self._benchmark_window_started else self.processed_frames,
            "fps": fps,
            "latency": lat_stats,
            "events_published": self.publisher.total_published if self.publisher else 0,
            "events_failed": self.publisher.events_failed if self.publisher else 0,
        }
        out_path = Path(file_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)
        tmp_path.replace(out_path)
        logger.info(f"[BENCHMARK] Reporte exportado a: {out_path}")
        return report_data

    def run(
        self,
        max_frames: Optional[int] = None,
        on_event_cb: Optional[Callable[[FaceDetectionEvent], None]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> dict:
        """
        Ejecuta el bucle principal de captura e ingesta de video utilizando RTSPStreamCapture.
        """
        if not self.stream_capture.isOpened():
            opened = self.stream_capture.open()
            if not opened and not self.stream_capture.is_live:
                raise RuntimeError(
                    f"No fue posible abrir la fuente de video: '{self.stream_capture.sanitized_source}'"
                )

        stream_fps = self.stream_capture.get_fps()
        logger.info(
            f"[+] Ingesta iniciada: {self.stream_capture.sanitized_source} "
            f"(En vivo: {self.stream_capture.is_live}, Skip: {self.skip_frames}, FPS stream: {stream_fps:.1f})"
        )

        writer = None
        start_time = time.time()
        last_fps_time = start_time
        fps_frame_count = 0
        benchmark_mode = self.benchmark_duration_seconds > 0
        benchmark_start_time = time.perf_counter() if benchmark_mode else 0.0

        try:
            while not self.is_stopped(stop_event):
                ret, frame, frame_ts = self.stream_capture.read()
                if self.is_stopped(stop_event):
                    break

                if not ret or frame is None:
                    if not self.stream_capture.is_live:
                        break
                    # En modo stream en vivo, continuar para dar oportunidad a la reconexión
                    continue

                with self._metrics_lock:
                    self.total_frames_read = self.stream_capture.total_frames_read
                    self.last_frame_timestamp = frame_ts
                curr_time = frame_ts

                # Inicialización diferida del VideoWriter si se especificó output_path
                if self.output_path and writer is None:
                    h, w = frame.shape[:2]
                    out_file = Path(self.output_path)
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(str(out_file), fourcc, stream_fps / self.skip_frames, (w, h))

                # Omisión de fotogramas (Frame Skipping) para optimización de recursos
                if (self.total_frames_read - 1) % self.skip_frames != 0:
                    continue

                # Registro de captura para fotogramas que ingresan a inferencia
                if self.enable_latency_tracking:
                    t_capture_now = time.perf_counter()
                    self._frame_capture_timestamps[self.total_frames_read] = t_capture_now
                    # Limpieza acotada: descartar registros con antigüedad > 150 cuadros
                    cutoff_frame = self.total_frames_read - 150
                    for stale_fid in [k for k in self._frame_capture_timestamps if k < cutoff_frame]:
                        self._frame_capture_timestamps.pop(stale_fid, None)

                fps_frame_count += 1

                # Cálculo de FPS de inferencia en ventana deslizante
                if curr_time - last_fps_time >= 1.0:
                    calc_fps = fps_frame_count / (curr_time - last_fps_time)
                    fps_frame_count = 0
                    last_fps_time = curr_time
                    with self._metrics_lock:
                        self.fps = calc_fps
                        self.processed_frames += 1
                else:
                    with self._metrics_lock:
                        self.processed_frames += 1

                # Procesamiento
                annotated_frame, active_tracks = self.process_frame(
                    frame=frame,
                    frame_idx=self.total_frames_read,
                    timestamp=curr_time,
                    on_event_cb=on_event_cb,
                )

                # Control síncrono de fases de benchmark
                if benchmark_mode:
                    now_perf = time.perf_counter()
                    elapsed = now_perf - benchmark_start_time
                    if not self._benchmark_window_started and elapsed >= self.benchmark_warmup_seconds:
                        self._start_benchmark_window()
                    elif self._benchmark_window_started:
                        self._benchmark_frames_processed += 1
                        window_elapsed = now_perf - self._benchmark_window_start_time
                        if window_elapsed >= self.benchmark_duration_seconds:
                            logger.info(
                                f"[BENCHMARK] Ventana de medición sostenida completada ({window_elapsed:.1f}s). Finalizando..."
                            )
                            if self.benchmark_report_file:
                                self.export_benchmark_report(self.benchmark_report_file)
                            break

                # Información superpuesta (Overlay)
                reconnect_str = f" | Reconnects: {self.stream_capture.reconnect_attempts}" if self.stream_capture.reconnect_attempts > 0 else ""
                stats_text = (
                    f"Camera: {self.camera_id} | Status: {self.stream_capture.status}{reconnect_str} | "
                    f"Ingest FPS: {self.fps:.1f} | Tracks: {len(active_tracks)} | "
                    f"Published: {self.publisher.total_published if self.publisher else 0}"
                )
                cv2.rectangle(annotated_frame, (10, 10), (10 + len(stats_text) * 9, 35), (0, 0, 0), -1)
                cv2.putText(
                    annotated_frame,
                    stats_text,
                    (15, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 255),
                    1,
                    cv2.LINE_AA,
                )

                if writer:
                    writer.write(annotated_frame)

                if self.show_window:
                    cv2.imshow(f"AI Worker - {self.camera_id}", annotated_frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == 27 or key == ord("q"):
                        logger.info("Interrupción solicitada por el usuario (tecla Q/ESC).")
                        self.stop(timeout=2.0)
                        break

                if max_frames and self.processed_frames >= max_frames:
                    logger.info(f"Límite de {max_frames} fotogramas procesados alcanzado.")
                    break

        finally:
            self.stop(timeout=2.0)
            if writer:
                writer.release()
            if self.show_window:
                cv2.destroyAllWindows()
            if self.publisher:
                self.publisher.close()
            if benchmark_mode and self.benchmark_report_file and not Path(self.benchmark_report_file).exists():
                try:
                    self.export_benchmark_report(self.benchmark_report_file)
                except Exception as r_err:
                    logger.error(f"Error al escribir reporte de benchmark en shutdown: {r_err}")

        total_elapsed = time.time() - start_time
        avg_fps = self.processed_frames / total_elapsed if total_elapsed > 0 else 0.0

        summary = {
            "camera_id": self.camera_id,
            "status": self.stream_capture.status,
            "total_frames_read": self.total_frames_read,
            "processed_frames": self.processed_frames,
            "elapsed_seconds": total_elapsed,
            "avg_fps": avg_fps,
            "events_published": self.publisher.total_published if self.publisher else 0,
            "events_failed": self.publisher.events_failed if self.publisher else 0,
            "reconnect_attempts": self.stream_capture.reconnect_attempts,
            "last_error": self.stream_capture.last_error,
            "last_frame_timestamp": self.last_frame_timestamp,
        }
        if self.enable_latency_tracking:
            summary["latency_stats"] = self.get_latency_stats()

        logger.info(f"[✓] Procesamiento finalizado: {summary}")
        return summary
