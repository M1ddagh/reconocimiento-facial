#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/schemas.py
--------------------
Modelos de datos y esquemas de serialización para eventos biométricos faciales.
"""

from typing import List, Optional
import uuid
import time
from pydantic import BaseModel, Field


class FaceDetectionEvent(BaseModel):
    """
    Evento producido por el AI Worker tras detectar, rastrear y extraer
    las características de un rostro en un fotograma de video.
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    camera_id: str = Field(..., description="Identificador único de la cámara o fuente de video")
    track_id: int = Field(..., description="ID persistente asignado por ByteTrack")
    frame_id: int = Field(..., description="Número secuencial del fotograma procesado")
    timestamp: float = Field(default_factory=time.time, description="Marca de tiempo Unix en segundos")
    bbox: List[int] = Field(..., description="Coordenadas [x1, y1, x2, y2] del rostro")
    det_score: float = Field(..., description="Puntaje de confianza de detección de InsightFace")
    embedding: List[float] = Field(..., description="Vector de características ArcFace 512D normalizado L2")
    crop_image_base64: Optional[str] = Field(None, description="Recorte JPEG del rostro en base64 para persistencia en MinIO")
    gender: Optional[int] = Field(None, description="Predicción de género (0: Mujer, 1: Hombre)")
    age: Optional[int] = Field(None, description="Estimación de edad")

    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "c62b9a78-2dfa-4be3-b78b-d7d8e6a21391",
                "camera_id": "camera_acceso_norte",
                "track_id": 4,
                "frame_id": 120,
                "timestamp": 1727460000.123,
                "bbox": [150, 80, 290, 260],
                "det_score": 0.942,
                "embedding": [0.034, -0.012, 0.089],  # 512 elementos
                "crop_image_base64": "/9j/4AAQSkZJRg...",
                "gender": 1,
                "age": 32,
            }
        }
