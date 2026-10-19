#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/supervisor.py
------------------------
Daemon supervisor autónomo para orquestar y vigilar el ciclo de vida de los subprocesos AI Worker.

Responsabilidades clave:
1. Validación temprana (fail-fast) de INTERNAL_API_SECRET.
2. Coordinación de liderazgo distribuido (Leader Election con cerrojo atómico en Redis).
3. Bucle de reconciliación periódica contra GET /api/internal/cameras con timeout acotado (3.0s).
4. Lanzamiento de subprocesos mediante variables de entorno (sin exponer credenciales en sys.argv).
5. Omisión estricta de cámaras sin rtsp_url configurada para evitar arrancar webcams locales por defecto.
6. Reinicio con backoff exponencial para cualquier terminación (códigos 0 y distintos de 0).
7. Watchdog de salud en Redis con inmunidad a caídas de Redis y respeto al estado 'reconnecting'.
8. Parada segura (Graceful Shutdown) de subprocesos y del propio supervisor.
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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

import httpx
import redis

logger = logging.getLogger("ai_worker.supervisor")

# Scripts Lua atómicos para el cerrojo de liderazgo
LUA_RENEW_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""

LUA_RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def validate_internal_secret(secret: str, environment: str = "development") -> None:
    """Valida que INTERNAL_API_SECRET esté configurado y sea seguro en producción."""
    if not secret or not secret.strip():
        raise RuntimeError(
            "INTERNAL_API_SECRET no está configurado en el supervisor. "
            "Es obligatorio definir esta variable en el entorno."
        )
    dev_defaults = {
        "rf_secret_compartido_dev_mvp",
        "cambiar_este_secreto_interno_compartido_12345",
        "change_this_secret_in_production_key_123",
    }
    if environment.lower() == "production":
        if secret in dev_defaults:
            raise RuntimeError(
                "En entorno de producción (ENVIRONMENT=production), está prohibido utilizar el "
                f"secreto interno por defecto ('{secret}'). Configure un INTERNAL_API_SECRET propio y seguro."
            )
        if len(secret) < 16:
            raise RuntimeError(
                "En entorno de producción, INTERNAL_API_SECRET debe tener al menos 16 caracteres."
            )


@dataclass
class ManagedWorker:
    """Representa el estado en memoria de un subproceso de worker supervisado."""
    camera_id: str
    rtsp_url: str
    process: Optional[subprocess.Popen] = None
    started_at: float = 0.0
    restart_count: int = 0
    last_exit_code: Optional[int] = None
    backoff_until: float = 0.0
    is_reconnecting: bool = False
    stalled_since: Optional[float] = None


class WorkerSupervisor:
    """
    Supervisor autónomo de procesos AI Worker.
    """

    def __init__(
        self,
        backend_url: str = "http://localhost:8000",
        internal_secret: Optional[str] = None,
        redis_url: str = "redis://localhost:6379/0",
        queue_name: str = "face_events_queue",
        poll_interval: float = 5.0,
        use_cpu: bool = True,
        shutdown_timeout: float = 3.0,
        environment: str = "development",
        enable_leader_lock: bool = True,
    ):
        self.backend_url = backend_url.rstrip("/")
        self.environment = environment
        self.internal_secret = internal_secret or os.getenv("INTERNAL_API_SECRET", "")
        self.redis_url = redis_url
        self.queue_name = queue_name
        self.poll_interval = max(1.0, float(poll_interval))
        self.use_cpu = use_cpu
        self.shutdown_timeout = shutdown_timeout
        self.enable_leader_lock = enable_leader_lock

        # Validación fail-fast de seguridad
        validate_internal_secret(self.internal_secret, self.environment)

        self.instance_id = str(uuid.uuid4())
        self.leader_lock_key = "supervisor:leader:lock"
        self.leader_lease_seconds = 15
        self.is_leader = False

        self.workers: Dict[str, ManagedWorker] = {}
        self.stop_event = threading.Event()

        # Cliente Redis con timeouts breves
        self.redis_client: Optional[redis.Redis] = None
        self._init_redis()

    def _init_redis(self) -> None:
        try:
            self.redis_client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
        except Exception as ex:
            logger.warning(f"[Supervisor] No se pudo instanciar cliente Redis: {ex}")
            self.redis_client = None

    # ------------------------------------------------------------------
    # Gestión del Cerrojo de Liderazgo (Leader Election)
    # ------------------------------------------------------------------
    def try_acquire_leadership(self) -> bool:
        """Intenta adquirir o renovar el cerrojo de liderazgo en Redis."""
        if not self.enable_leader_lock or self.redis_client is None:
            self.is_leader = True
            return True

        try:
            # 1. Si ya somos líderes, renovar con script Lua atómico
            if self.is_leader:
                renewed = self.redis_client.eval(
                    LUA_RENEW_LOCK,
                    1,
                    self.leader_lock_key,
                    self.instance_id,
                    self.leader_lease_seconds,
                )
                if renewed:
                    return True
                logger.warning("[Supervisor] Se perdió el cerrojo de liderazgo (clave no renovada).")
                self.is_leader = False
                return False

            # 2. Intento de adquisición inicial (SET NX EX)
            acquired = self.redis_client.set(
                self.leader_lock_key,
                self.instance_id,
                nx=True,
                ex=self.leader_lease_seconds,
            )
            if acquired:
                self.is_leader = True
                logger.info(f"[Supervisor] Liderazgo adquirido con éxito (Instancia: {self.instance_id[:8]}).")
                return True
            else:
                self.is_leader = False
                return False
        except (redis.ConnectionError, redis.TimeoutError) as ex:
            logger.warning(f"[Supervisor] Redis inaccesible durante verificación de liderazgo: {ex}")
            # Si Redis cae y ya éramos líderes, conceder gracia temporal sin alterar procesos
            return self.is_leader
        except Exception as ex:
            logger.error(f"[Supervisor] Error verificando liderazgo: {ex}")
            return False

    def release_leadership(self) -> None:
        """Libera de forma segura el cerrojo de liderazgo en Redis si nos pertenece."""
        if not self.enable_leader_lock or self.redis_client is None or not self.is_leader:
            return
        try:
            self.redis_client.eval(
                LUA_RELEASE_LOCK,
                1,
                self.leader_lock_key,
                self.instance_id,
            )
            logger.info("[Supervisor] Cerrojo de liderazgo liberado.")
        except Exception as ex:
            logger.warning(f"[Supervisor] No se pudo liberar cerrojo de liderazgo: {ex}")
        finally:
            self.is_leader = False

    # ------------------------------------------------------------------
    # Consulta al Backend
    # ------------------------------------------------------------------
    def fetch_desired_cameras(self) -> Optional[List[Dict[str, Any]]]:
        """
        Consulta GET /api/internal/cameras con cabecera X-Internal-Secret y timeout acotado.
        Retorna la lista de cámaras o None si hubo error de conexión/5xx.
        Lanza RuntimeError si el backend rechaza la autenticación (403/422).
        """
        endpoint = f"{self.backend_url}/api/internal/cameras"
        headers = {"X-Internal-Secret": self.internal_secret}
        try:
            response = httpx.get(endpoint, headers=headers, timeout=3.0)
            if response.status_code == 200:
                return response.json()
            elif response.status_code in (403, 422):
                logger.critical(
                    f"[Supervisor] Error de autenticación interna con backend ({response.status_code}): "
                    f"{response.text}. Verifique INTERNAL_API_SECRET."
                )
                raise RuntimeError(
                    f"Fallo de autenticación con endpoint interno: HTTP {response.status_code}"
                )
            else:
                logger.warning(
                    f"[Supervisor] Backend respondió con código HTTP inesperado: {response.status_code}"
                )
                return None
        except httpx.RequestError as ex:
            logger.warning(f"[Supervisor] Backend inaccesible o timeout tras 3.0s: {ex}")
            return None

    # ------------------------------------------------------------------
    # Gestión de Subprocesos y Reconciliación
    # ------------------------------------------------------------------
    def spawn_worker(self, camera_id: str, rtsp_url: str) -> bool:
        """
        Lanza un subproceso worker_main inyectando VIDEO_SOURCE y CAMERA_ID en su entorno (env),
        manteniendo la línea de comando (sys.argv) libre de credenciales y contraseñas.
        """
        # Regla 1: No iniciar workers sin fuente configurada
        if not rtsp_url or not rtsp_url.strip():
            logger.warning(
                f"[Supervisor] Cámara '{camera_id}' activa pero sin rtsp_url válida. "
                "Se omite el arranque para evitar abrir dispositivos locales por defecto."
            )
            self._report_camera_error(camera_id, "Configuración incompleta: rtsp_url no definida")
            return False

        # Inyección segura en el entorno del hijo
        worker_env = os.environ.copy()
        worker_env["VIDEO_SOURCE"] = rtsp_url.strip()
        worker_env["CAMERA_ID"] = camera_id
        worker_env["REDIS_URL"] = self.redis_url
        worker_env["QUEUE_NAME"] = self.queue_name
        worker_env["USE_CPU"] = str(self.use_cpu).lower()

        # Línea de comandos limpia sin credenciales
        cmd = [
            sys.executable,
            "-m",
            "ai_worker.worker_main",
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                env=worker_env,
                cwd=str(ROOT_DIR),
            )
            now = time.time()
            current = self.workers.get(camera_id)
            restart_count = current.restart_count if current else 0

            self.workers[camera_id] = ManagedWorker(
                camera_id=camera_id,
                rtsp_url=rtsp_url,
                process=proc,
                started_at=now,
                restart_count=restart_count,
            )
            logger.info(
                f"[Supervisor] Worker lanzado exitosamente para cámara '{camera_id}' "
                f"(PID: {proc.pid}, Reinicio #{restart_count})."
            )
            return True
        except Exception as ex:
            logger.error(f"[Supervisor] Error al iniciar subproceso para '{camera_id}': {ex}")
            return False

    def stop_worker(self, camera_id: str, timeout: Optional[float] = None) -> None:
        """Detiene un subproceso worker con terminación ordenada (terminate -> kill) y purga Redis."""
        t_out = timeout if timeout is not None else self.shutdown_timeout
        worker = self.workers.get(camera_id)
        if not worker or not worker.process:
            self._purge_redis_status(camera_id)
            self.workers.pop(camera_id, None)
            return

        proc = worker.process
        if proc.poll() is None:
            logger.info(f"[Supervisor] Solicitando terminación ordenada de worker '{camera_id}' (PID: {proc.pid})...")
            try:
                proc.terminate()
                proc.wait(timeout=t_out)
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"[Supervisor] Worker '{camera_id}' no terminó en {t_out}s tras terminate(). "
                    "Aplicando kill() forzoso..."
                )
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except Exception as ex:
                    logger.error(f"[Supervisor] Error aplicando kill a '{camera_id}': {ex}")
            except Exception as ex:
                logger.error(f"[Supervisor] Excepción deteniendo worker '{camera_id}': {ex}")

        self._purge_redis_status(camera_id)
        self.workers.pop(camera_id, None)

    def _purge_redis_status(self, camera_id: str) -> None:
        """Purga la clave de estado en Redis tras detener un worker."""
        if self.redis_client:
            try:
                self.redis_client.delete(f"worker:status:{camera_id}")
            except Exception:
                pass

    def _report_camera_error(self, camera_id: str, error_msg: str) -> None:
        """Publica estado error en Redis para alertar al dashboard de configuración inválida."""
        if self.redis_client:
            try:
                import json
                payload = json.dumps({
                    "status": "error",
                    "pid": None,
                    "last_error": error_msg,
                    "updated_at": time.time(),
                })
                self.redis_client.set(f"worker:status:{camera_id}", payload, ex=30)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Reconciliación Desired vs Running
    # ------------------------------------------------------------------
    def reconcile(self) -> None:
        """Ejecuta un ciclo de reconciliación comparando cámaras deseadas contra subprocesos."""
        # 1. Comprobar cerrojo de liderazgo
        if self.enable_leader_lock and not self.try_acquire_leadership():
            logger.debug("[Supervisor] En modo standby (no líder). Se omite ciclo de reconciliación.")
            return

        # 2. Consultar cámaras deseadas del backend
        desired_cameras = self.fetch_desired_cameras()
        now = time.time()

        # Si el backend no respondió, preservar workers en ejecución y continuar
        if desired_cameras is None:
            logger.debug("[Supervisor] Reconciliación pospuesta: backend no disponible en este ciclo.")
            self._check_process_health(now)
            return

        # 3. Mapear estado deseado
        active_desired: Dict[str, Dict[str, Any]] = {}
        for cam in desired_cameras:
            cid = cam.get("camera_id")
            if not cid:
                continue
            if cam.get("is_active", False):
                active_desired[cid] = cam

        # 4. Detener workers de cámaras inactivas o eliminadas
        managed_ids = list(self.workers.keys())
        for cid in managed_ids:
            if cid not in active_desired:
                logger.info(f"[Supervisor] Cámara '{cid}' ya no está activa en la base de datos. Deteniendo worker...")
                self.stop_worker(cid)

        # 5. Iniciar o actualizar cámaras activas
        for cid, cam_data in active_desired.items():
            rtsp_url = cam_data.get("rtsp_url")
            existing = self.workers.get(cid)

            # Si ya corre, verificar si cambió su URL
            if existing and existing.process and existing.process.poll() is None:
                if existing.rtsp_url != rtsp_url:
                    logger.info(f"[Supervisor] URL RTSP de '{cid}' fue actualizada. Reiniciando subproceso...")
                    self.stop_worker(cid)
                    self.spawn_worker(cid, rtsp_url)
                continue

            # Si no tiene proceso o ya terminó, evaluar si está en periodo de backoff
            if existing:
                if now < existing.backoff_until:
                    logger.debug(
                        f"[Supervisor] Cámara '{cid}' en enfriamiento de backoff por "
                        f"{existing.backoff_until - now:.1f}s adicionales."
                    )
                    continue

            # Iniciar nuevo subproceso
            self.spawn_worker(cid, rtsp_url)

        # 6. Supervisar la salud de los subprocesos y watchdog de Redis
        self._check_process_health(now)

    def _check_process_health(self, now: float) -> None:
        """
        Inspecciona el estado de los subprocesos:
        - Si un subproceso terminó (código 0 o != 0), programa su reinicio con backoff.
        - Restablece el contador de reinicios si se mantuvo estable > 60s.
        - Ejecuta watchdog de Redis con tolerancia a fallos.
        """
        for cid, worker in list(self.workers.items()):
            proc = worker.process
            if proc is None:
                continue

            exit_code = proc.poll()
            if exit_code is not None:
                # El proceso terminó (sea 0 por fin de video / clean close o != 0 por error)
                worker.last_exit_code = exit_code
                worker.process = None
                worker.restart_count += 1

                # Calcular backoff exponencial (base 2.0s, multiplicador x2, max 30s)
                backoff_delay = min(30.0, 2.0 * (2 ** max(0, worker.restart_count - 1)))
                worker.backoff_until = now + backoff_delay

                logger.warning(
                    f"[Supervisor] Worker '{cid}' terminó (código: {exit_code}). "
                    f"Reinicio #{worker.restart_count} programado en {backoff_delay:.1f}s."
                )
            else:
                # El proceso está vivo; si superó 60s sin caer, restablecer contador de backoff
                if now - worker.started_at > 60.0 and worker.restart_count > 0:
                    worker.restart_count = 0
                    logger.debug(f"[Supervisor] Worker '{cid}' estable por > 60s. Contador de backoff restablecido a 0.")

        # Watchdog en Redis
        self._run_redis_watchdog(now)

    def _run_redis_watchdog(self, now: float) -> None:
        """
        Watchdog desacoplado en Redis:
        - Si Redis está caído (redis_available=False): no toca workers vivos.
        - Si el worker reporta 'reconnecting': no lo interrumpe.
        - Si un worker vivo lleva > 30s congelado sin latido y sin reconexión: lo reinicia.
        """
        if self.redis_client is None:
            return

        for cid, worker in list(self.workers.items()):
            if worker.process is None or worker.process.poll() is not None:
                continue

            try:
                raw_data = self.redis_client.get(f"worker:status:{cid}")
                redis_available = True
            except (redis.ConnectionError, redis.TimeoutError):
                redis_available = False
                raw_data = None
            except Exception:
                redis_available = False
                raw_data = None

            # Regla 2: Si Redis no está disponible, omitir decisión destructiva
            if not redis_available:
                logger.debug(f"[Watchdog] Redis no disponible para '{cid}'; se preserva proceso vivo.")
                continue

            if raw_data:
                try:
                    import json
                    payload = json.loads(raw_data)
                    status = payload.get("status", "")
                    if status == "reconnecting":
                        # Respeto estricto al estado de reconexión RTSP de la Etapa 1
                        worker.is_reconnecting = True
                        worker.stalled_since = None
                        continue
                    elif status == "stalled":
                        if worker.stalled_since is None:
                            worker.stalled_since = now
                        elif now - worker.stalled_since > 30.0:
                            logger.warning(
                                f"[Watchdog] Worker '{cid}' reporta 'stalled' por > 30s continuo. "
                                "Reiniciando proceso congelado..."
                            )
                            self.stop_worker(cid)
                            self.spawn_worker(cid, worker.rtsp_url)
                        continue
                    else:
                        worker.is_reconnecting = False
                        worker.stalled_since = None
                except Exception:
                    pass
            else:
                # Clave expiró en Redis pero el proceso sigue vivo según el SO
                # Si recién inició (< 15s), permitir tiempo de arranque de modelos
                if now - worker.started_at < 15.0:
                    continue

                if worker.is_reconnecting:
                    # Estaba en reconexión, permitir tolerancia
                    continue

                if worker.stalled_since is None:
                    worker.stalled_since = now
                elif now - worker.stalled_since > 30.0:
                    logger.warning(
                        f"[Watchdog] Worker '{cid}' no reporta latidos en Redis por > 30s. "
                        "Reiniciando subproceso potencialmente bloqueado..."
                    )
                    self.stop_worker(cid)
                    self.spawn_worker(cid, worker.rtsp_url)

    # ------------------------------------------------------------------
    # Ciclo de Vida y Señales
    # ------------------------------------------------------------------
    def run(self) -> None:
        """Bucle principal del supervisor."""
        logger.info("=" * 65)
        logger.info("   INICIANDO AI WORKER SUPERVISOR")
        logger.info("=" * 65)
        logger.info(f" • Backend URL     : {self.backend_url}")
        logger.info(f" • Entorno         : {self.environment}")
        logger.info(f" • Redis URL       : {self.redis_url}")
        logger.info(f" • Intervalo Poll  : {self.poll_interval}s")
        logger.info(f" • Modo ejecución  : {'CPU' if self.use_cpu else 'GPU/CUDA'}")
        logger.info(f" • Instancia UUID  : {self.instance_id[:8]}")
        logger.info("=" * 65)

        while not self.stop_event.is_set():
            try:
                self.reconcile()
            except RuntimeError as ex:
                logger.critical(f"[Supervisor] Error crítico de configuración: {ex}. Deteniendo supervisor.")
                break
            except Exception as ex:
                logger.exception(f"[Supervisor] Error inesperado en reconciliación: {ex}")

            # Espera no bloqueante: responde de inmediato a stop_event
            self.stop_event.wait(timeout=self.poll_interval)

        self.shutdown()

    def stop(self) -> None:
        """Solicita la parada del supervisor (seguro para invocar desde hilos o handlers)."""
        self.stop_event.set()

    def shutdown(self) -> None:
        """Protocolo de cierre ordenado: detiene todos los workers y libera cerrojo."""
        logger.info("[Supervisor] Deteniendo todos los subprocesos gestionados...")
        managed_ids = list(self.workers.keys())
        for cid in managed_ids:
            self.stop_worker(cid)

        self.release_leadership()
        logger.info("[✓] Supervisor detenido completamente.")


def main():
    parser = argparse.ArgumentParser(description="AI Worker Supervisor")
    parser.add_argument("--backend-url", default=os.getenv("BACKEND_URL", "http://localhost:8000"))
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    parser.add_argument("--poll-interval", type=float, default=float(os.getenv("SUPERVISOR_POLL_INTERVAL", "5.0")))
    parser.add_argument("--use-cpu", action="store_true", default=os.getenv("USE_CPU", "true").lower() in ("true", "1", "yes"))
    parser.add_argument("--no-leader-lock", action="store_true", help="Deshabilitar cerrojo distribuido de Redis.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%H:%M:%S",
    )

    env = os.getenv("ENVIRONMENT", "development")
    supervisor = WorkerSupervisor(
        backend_url=args.backend_url,
        redis_url=args.redis_url,
        poll_interval=args.poll_interval,
        use_cpu=args.use_cpu,
        environment=env,
        enable_leader_lock=not args.no_leader_lock,
    )

    # Handlers de señales libres de I/O
    def handle_signal(sig, frame):
        logger.info(f"Señal {sig} recibida. Solicitando parada ordenada del supervisor...")
        supervisor.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    supervisor.run()


if __name__ == "__main__":
    main()
