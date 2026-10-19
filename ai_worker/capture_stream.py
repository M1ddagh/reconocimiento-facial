#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/capture_stream.py
---------------------------
Módulo de captura resiliente para flujos de video RTSP, webcams y archivos locales.
Implementa:
- Hilo lector desacoplado (ReaderThread) para evitar acumulación de búfer y latencia en vivo.
- Detección de desconexión y bucle de reconexión con backoff exponencial.
- Watchdog de inactividad de fotogramas (last_frame_timestamp).
- Sanitización de URLs con credenciales en logs y excepciones.
- Inyección de factoría de captura para pruebas unitarias sin hardware físico.
"""

import logging
import os
import queue
import re
import threading
import time
from typing import Any, Callable, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import cv2
import numpy as np

logger = logging.getLogger("ai_worker.capture_stream")


def sanitize_url(url: str | int) -> str:
    """
    Ofusca contraseñas en URLs (RTSP/HTTP/Redis) y textos de error que contengan URLs.
    Ejemplos:
    - 'rtsp://admin:secret123@192.168.1.10:554/stream' -> 'rtsp://admin:****@192.168.1.10:554/stream'
    - 'redis://:mypassword@localhost:6379/0' -> 'redis://:****@localhost:6379/0'
    - 'Fallo con rtsp://admin:pwd@1.2.3.4/live' -> 'Fallo con rtsp://admin:****@1.2.3.4/live'
    """
    if not isinstance(url, str):
        return str(url)
    if "://" not in url:
        return url
    return re.sub(r"://([^:@\s]*):([^@\s]+)@", r"://\1:****@", url)


sanitize_rtsp_url = sanitize_url


def default_cv2_capture_factory(source: str | int) -> Any:
    """Factoría por defecto que instancia cv2.VideoCapture configurando transporte TCP para RTSP."""
    cap_src = int(source) if isinstance(source, str) and source.isdigit() else source
    if isinstance(source, str) and source.lower().startswith(("rtsp://", "rtsps://")):
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    return cv2.VideoCapture(cap_src)


class RTSPStreamCapture:
    """
    Capturador de video tolerante a fallos de red con máquina de estados y reconexión.
    Estados: 'starting', 'running', 'reconnecting', 'stopped', 'error'.
    """

    def __init__(
        self,
        source: str | int,
        is_live: Optional[bool] = None,
        read_timeout: float = 8.0,
        initial_backoff: float = 1.0,
        max_backoff: float = 30.0,
        backoff_factor: float = 2.0,
        max_reconnect_attempts: Optional[int] = None,
        capture_factory: Optional[Callable[[str | int], Any]] = None,
    ):
        self.source = source
        self.sanitized_source = sanitize_rtsp_url(source)
        self.read_timeout = max(1.0, float(read_timeout))
        self.initial_backoff = max(0.1, float(initial_backoff))
        self.max_backoff = max(self.initial_backoff, float(max_backoff))
        self.backoff_factor = max(1.1, float(backoff_factor))
        self.max_reconnect_attempts = max_reconnect_attempts
        self._capture_factory = capture_factory or default_cv2_capture_factory

        # Determinar si la fuente es en vivo o archivo local
        if is_live is None:
            if isinstance(source, int):
                self.is_live = True
            elif isinstance(source, str):
                s_lower = source.lower()
                if source.isdigit() or s_lower.startswith(("rtsp://", "rtsps://", "http://", "https://")):
                    self.is_live = True
                else:
                    self.is_live = False
            else:
                self.is_live = False
        else:
            self.is_live = is_live

        # Estado operativo y telemetría
        self.status: str = "starting"
        self.current_backoff: float = self.initial_backoff
        self.reconnect_attempts: int = 0
        self.total_frames_read: int = 0
        self.last_frame_timestamp: float = 0.0
        self.last_error: Optional[str] = None

        # Recursos internos
        self._cap: Any = None
        self._frame_queue: queue.Queue[Tuple[bool, Optional[np.ndarray], float]] = queue.Queue(maxsize=1)
        self._reader_thread: Optional[threading.Thread] = None
        self._close_requested = threading.Event()
        self._stop_reader_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def _stop_requested(self) -> threading.Event:
        """Alias de compatibilidad que referencia al evento de cierre definitivo."""
        return self._close_requested

    def open(self) -> bool:
        """Abre la conexión inicial con la fuente."""
        with self._lock:
            self.status = "starting"
            self._close_requested.clear()
            self._stop_reader_event.clear()
            try:
                self._cap = self._capture_factory(self.source)
                if self._cap is not None and self._cap.isOpened():
                    self.status = "running"
                    self.current_backoff = self.initial_backoff
                    self.reconnect_attempts = 0
                    logger.info(f"[+] Fuente de video abierta exitosamente: {self.sanitized_source}")
                    if self.is_live:
                        self._start_reader_thread()
                    return True
                else:
                    self.last_error = f"No se pudo abrir la fuente: {self.sanitized_source}"
                    logger.warning(f"[!] {self.last_error}")
                    if self.is_live:
                        self.status = "reconnecting"
                    else:
                        self.status = "error"
                    return False
            except Exception as ex:
                self.last_error = str(ex)
                logger.error(f"[ERROR] Excepción al abrir fuente {self.sanitized_source}: {ex}")
                self.status = "reconnecting" if self.is_live else "error"
                return False

    def isOpened(self) -> bool:
        return self._cap is not None and getattr(self._cap, "isOpened", lambda: False)()

    def get(self, prop_id: int) -> float:
        if self._cap is not None and hasattr(self._cap, "get"):
            try:
                return float(self._cap.get(prop_id))
            except Exception:
                return 0.0
        return 0.0

    def get_fps(self) -> float:
        fps = self.get(cv2.CAP_PROP_FPS)
        return fps if fps > 0.0 else 30.0

    def _start_reader_thread(self) -> None:
        self._stop_reader_thread()
        self._stop_reader_event.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"RTSPReader-{self.sanitized_source}",
            daemon=True,
        )
        self._reader_thread.start()

    def _stop_reader_thread(self, timeout: float = 1.5) -> None:
        self._stop_reader_event.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=timeout)
        self._reader_thread = None

        # Vaciar cola residual
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

    def _reader_loop(self) -> None:
        """Hilo en segundo plano que lee continuamente y mantiene únicamente el fotograma más fresco."""
        while not self._stop_reader_event.is_set() and not self._close_requested.is_set():
            cap = self._cap
            if cap is None or not getattr(cap, "isOpened", lambda: False)():
                time.sleep(0.05)
                continue

            try:
                ret, frame = cap.read()
            except Exception as ex:
                logger.debug(f"[RTSP] Excepción en lectura de frame: {ex}")
                ret, frame = False, None

            now = time.time()
            if ret and frame is not None:
                self.last_frame_timestamp = now
                self.total_frames_read += 1

                # Mantener solo el frame más reciente (Queue maxsize=1)
                try:
                    self._frame_queue.put_nowait((True, frame, now))
                except queue.Full:
                    try:
                        self._frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._frame_queue.put_nowait((True, frame, now))
                    except queue.Full:
                        pass
            else:
                # Si se configuró reproducción en bucle continuo para pruebas/benchmarks
                if not self.is_live and os.getenv("LOOP_VIDEO_SOURCE", "0").lower() in ("1", "true"):
                    if cap is not None and hasattr(cap, "set"):
                        try:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            continue
                        except Exception:
                            pass

                # Señal de corte o fin de stream
                if not self.is_live:
                    self.status = "stopped"
                    try:
                        self._frame_queue.put_nowait((False, None, now))
                    except queue.Full:
                        pass
                    break
                else:
                    logger.warning(f"[RTSP] Señal perdida en stream '{self.sanitized_source}'. Notificando consumidor.")
                    try:
                        self._frame_queue.put_nowait((False, None, now))
                    except queue.Full:
                        pass
                    # Esperar brevemente antes de siguiente lectura para no saturar si cap está caído
                    time.sleep(0.1)

    def reconnect(self) -> bool:
        """
        Ejecuta un ciclo de reconexión con backoff exponencial.
        Retorna True si reconectó exitosamente, False si falló este intento.
        """
        with self._lock:
            if self._stop_requested.is_set() or self.status == "stopped":
                return False

            self.status = "reconnecting"
            self.reconnect_attempts += 1

            if self.max_reconnect_attempts and self.reconnect_attempts > self.max_reconnect_attempts:
                self.status = "error"
                self.last_error = f"Límite de reconexiones ({self.max_reconnect_attempts}) alcanzado."
                logger.error(f"[!] {self.last_error}")
                return False

            sleep_duration = self.current_backoff
            logger.warning(
                f"[RTSP] Intento de reconexión #{self.reconnect_attempts} para '{self.sanitized_source}' "
                f"en {sleep_duration:.1f}s (Backoff actual: {sleep_duration:.1f}s)..."
            )
            # Despertar inmediatamente si se solicita parada durante el backoff
            if self._stop_requested.wait(timeout=sleep_duration):
                return False
            if self._stop_requested.is_set() or self.status == "stopped":
                return False

            # Incrementar backoff para el siguiente intento
            self.current_backoff = min(self.current_backoff * self.backoff_factor, self.max_backoff)

            # Limpiar captura previa
            self._stop_reader_thread()
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None

            if self._stop_requested.is_set() or self.status == "stopped":
                return False

            # Intentar reabrir
            try:
                new_cap = self._capture_factory(self.source)
                if new_cap is not None and new_cap.isOpened():
                    self._cap = new_cap
                    self.status = "running"
                    self.current_backoff = self.initial_backoff
                    self.reconnect_attempts = 0
                    self.last_frame_timestamp = time.time()
                    self.last_error = None
                    logger.info(f"[✓] Reconexión exitosa con '{self.sanitized_source}'")
                    if self.is_live:
                        self._start_reader_thread()
                    return True
                else:
                    self.last_error = f"Fallo al reabrir captura en intento #{self.reconnect_attempts}"
                    return False
            except Exception as ex:
                self.last_error = str(ex)
                logger.error(f"[!] Error durante reconexión a '{self.sanitized_source}': {ex}")
                return False

    def read(self, timeout: Optional[float] = None) -> Tuple[bool, Optional[np.ndarray], float]:
        """
        Lee el siguiente fotograma disponible.
        Para archivos locales: lectura secuencial sincrónica.
        Para streams en vivo: lectura desde cola de hilo desacoplado con watchdog.
        Retorna: (ret, frame, timestamp)
        """
        now = time.time()
        if self._stop_requested.is_set() or self.status in ("stopped", "error"):
            return False, None, now

        # 1. Modo archivo estático local
        if not self.is_live:
            if self._cap is None or not self.isOpened():
                self.status = "stopped"
                return False, None, now
            try:
                ret, frame = self._cap.read()
            except Exception as ex:
                self.last_error = str(ex)
                ret, frame = False, None

            if ret and frame is not None:
                self.total_frames_read += 1
                self.last_frame_timestamp = now
                self.status = "running"
                return True, frame, now
            else:
                self.status = "stopped"
                return False, None, now

        # 2. Modo stream en vivo (RTSP / Webcam) con chequeo ágil de parada
        t_out = min(timeout, 1.0) if timeout is not None else 1.0

        # Si el hilo lector no está corriendo y no está detenido, intentar reconectar
        if self._reader_thread is None or not self._reader_thread.is_alive():
            if not self._stop_requested.is_set() and self.status not in ("stopped", "error"):
                reconnected = self.reconnect()
                if not reconnected:
                    return False, None, now
            else:
                return False, None, now

        try:
            ret, frame, frame_ts = self._frame_queue.get(timeout=t_out)
            if self._stop_requested.is_set() or self.status in ("stopped", "error"):
                return False, None, now
            if ret and frame is not None:
                self.status = "running"
                return True, frame, frame_ts
            else:
                if self._stop_requested.is_set() or self.status in ("stopped", "error"):
                    return False, None, now
                # Señal de caída recibida desde el lector
                self.reconnect()
                return False, None, now
        except queue.Empty:
            if self._stop_requested.is_set() or self.status in ("stopped", "error"):
                return False, None, now
            # Watchdog de timeout excedido sin fotogramas
            if self.last_frame_timestamp > 0 and (now - self.last_frame_timestamp >= self.read_timeout):
                logger.warning(
                    f"[RTSP] Watchdog timeout: {now - self.last_frame_timestamp:.1f}s sin frames "
                    f"en '{self.sanitized_source}'. Iniciando reconexión..."
                )
                self.reconnect()
            elif self.last_frame_timestamp == 0.0:
                # Aún no recibió ningún frame inicial
                self.reconnect()
            return False, None, now

    def close(self, timeout: float = 2.0) -> None:
        """Cierra ordenadamente el capturador y libera recursos con límite de tiempo seguro."""
        self.status = "stopped"
        self._stop_requested.set()
        # Adquisición acotada con timeout para evitar deadlocks si otro hilo retiene el lock
        acquired = self._lock.acquire(timeout=timeout)
        if not acquired:
            logger.warning(
                f"[!] No se pudo adquirir el cerrojo tras {timeout:.1f}s para cerrar '{self.sanitized_source}'. "
                f"Se evita manipular el capturador o el hilo lector concurrentemente; "
                f"el supervisor externo aplicará terminación forzosa (kill) como última defensa."
            )
            return

        try:
            self._stop_reader_thread(timeout=min(timeout, 1.5))
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            logger.info(f"[-] Capturador cerrado para: {self.sanitized_source}")
        finally:
            self._lock.release()
