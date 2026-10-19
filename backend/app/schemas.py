from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


# --- Modelos de Cámaras ---
class CameraTelemetry(BaseModel):
    pid: Optional[int] = None
    fps: float = 0.0
    frames_processed: int = 0
    total_frames_read: int = 0
    last_frame_timestamp: float = 0.0
    reconnect_attempts: int = 0
    events_published: int = 0
    events_failed: int = 0
    last_error: Optional[str] = None
    started_at: Optional[float] = None
    updated_at: Optional[float] = None


class CameraBase(BaseModel):
    camera_id: str
    name: str
    location: Optional[str] = None
    rtsp_url: Optional[str] = None
    is_active: bool = True


class CameraCreate(CameraBase):
    pass


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    rtsp_url: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name_not_null(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            raise ValueError("El campo 'name' no puede ser nulo.")
        if not v.strip():
            raise ValueError("El campo 'name' no puede estar en blanco.")
        return v


class CameraResponse(CameraBase):
    id: int
    created_at: float
    operational_status: str = "offline"
    telemetry: Optional[CameraTelemetry] = None

    class Config:
        from_attributes = True


class InternalCameraResponse(BaseModel):
    """Esquema para consumo exclusivo del supervisor con rtsp_url sin enmascarar."""
    id: int
    camera_id: str
    name: str
    location: Optional[str] = None
    rtsp_url: Optional[str] = None
    is_active: bool
    created_at: float

    class Config:
        from_attributes = True


# --- Modelos de Personas Objetivo / Buscadas ---
class TargetPersonBase(BaseModel):
    full_name: str
    document_id: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool = True


class TargetPersonCreate(TargetPersonBase):
    faiss_id: Optional[int] = None
    photo_base64: Optional[str] = None


class TargetPersonResponse(TargetPersonBase):
    id: int
    faiss_id: int
    photo_url: Optional[str] = None
    created_at: float

    class Config:
        from_attributes = True


# --- Modelos de Alertas ---
class AlertResponse(BaseModel):
    id: int
    alert_id: str
    camera_id: str
    matched_subject_id: int
    subject_name: Optional[str] = None
    similarity: float
    bbox: Optional[str] = None
    crop_url: Optional[str] = None
    crop_base64: Optional[str] = None
    status: str
    timestamp: float

    class Config:
        from_attributes = True


class AlertStatusUpdate(BaseModel):
    status: str = Field(..., description="'pending', 'reviewed', o 'dismissed'")


# --- Métricas del Sistema ---
class SystemStats(BaseModel):
    total_cameras: int
    active_cameras: int
    total_target_persons: int
    total_alerts: int
    pending_alerts: int
    faiss_indexed_identities: int
