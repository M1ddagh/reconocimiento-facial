import base64
import io
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from backend.app.config import settings

logger = logging.getLogger("backend.storage")


class StorageClient:
    """
    Cliente de almacenamiento de objetos (MinIO / S3) con fallback local.
    """

    def __init__(self):
        self.minio_client = None
        self.bucket = settings.S3_BUCKET
        self.local_dir = Path("backend/uploads")
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self._init_minio()

    def _init_minio(self):
        try:
            import urllib3
            from minio import Minio

            # Cliente con timeout rápido para no bloquear si MinIO no está levantado
            http_client = urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=1.0, read=2.0),
                retries=urllib3.Retry(total=1, connect=1, read=1),
            )
            self.minio_client = Minio(
                settings.S3_ENDPOINT.replace("http://", "").replace("https://", ""),
                access_key=settings.S3_ACCESS_KEY,
                secret_key=settings.S3_SECRET_KEY,
                secure=settings.S3_SECURE,
                http_client=http_client,
            )

            # Verificar / crear bucket si no existe
            if not self.minio_client.bucket_exists(self.bucket):
                self.minio_client.make_bucket(self.bucket)
                logger.info(f"[✓] Bucket '{self.bucket}' creado en MinIO.")
            else:
                logger.info(f"[✓] Conectado a bucket MinIO: '{self.bucket}'")
        except Exception as ex:
            logger.info(
                f"[*] MinIO no detectado en '{settings.S3_ENDPOINT}'. "
                f"Usando almacenamiento local en '{self.local_dir.resolve()}'."
            )
            self.minio_client = None

    def save_image_bytes(self, image_bytes: bytes, filename: Optional[str] = None) -> str:
        """
        Guarda la imagen (crop/evidencia) y retorna su URL o identificador relativo.
        """
        if not filename:
            filename = f"crop_{uuid.uuid4().hex[:12]}.jpg"

        # Espejar siempre en almacenamiento local para acceso inmediato
        local_path = self.local_dir / filename
        try:
            with open(local_path, "wb") as f:
                f.write(image_bytes)
        except Exception as e:
            logger.warning(f"Error escribiendo copia local de {filename}: {e}")

        if self.minio_client:
            try:
                stream = io.BytesIO(image_bytes)
                self.minio_client.put_object(
                    self.bucket,
                    filename,
                    stream,
                    length=len(image_bytes),
                    content_type="image/jpeg",
                )
            except Exception as e:
                logger.error(f"Error guardando en MinIO: {e}.")

        return f"/api/storage/{filename}"

    def get_image_bytes(self, identifier: str) -> Optional[bytes]:
        """Recupera los bytes de una imagen desde disco local o MinIO."""
        filename = identifier.split("/")[-1]
        local_path = self.local_dir / filename
        if local_path.is_file():
            try:
                return local_path.read_bytes()
            except Exception:
                pass

        if self.minio_client:
            try:
                response = self.minio_client.get_object(self.bucket, filename)
                data = response.read()
                response.close()
                response.release_conn()
                # Guardar en caché local
                try:
                    with open(local_path, "wb") as f:
                        f.write(data)
                except Exception:
                    pass
                return data
            except Exception as e:
                logger.error(f"Error obteniendo '{filename}' de MinIO: {e}")

        return None

    def save_base64_crop(self, b64_str: str) -> Optional[str]:
        """Decodifica un base64 JPEG y lo almacena."""
        try:
            raw_bytes = base64.b64decode(b64_str)
            return self.save_image_bytes(raw_bytes)
        except Exception as e:
            logger.error(f"Error decodificando imagen base64: {e}")
            return None


storage_client = StorageClient()
