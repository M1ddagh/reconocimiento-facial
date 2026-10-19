from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Camera
from backend.app.schemas import CameraCreate, CameraUpdate, CameraResponse, CameraTelemetry
from backend.app.telemetry import (
    sanitize_rtsp_url,
    fetch_worker_status,
    fetch_worker_statuses,
    resolve_camera_status,
    purge_worker_status,
)

router = APIRouter(prefix="/cameras", tags=["Cámaras"])


def find_camera(db: Session, camera_identifier: str) -> Optional[Camera]:
    """
    Busca una cámara por su identificador único (camera_id: str)
    o por su clave primaria numérica (id: int).
    """
    cam = db.query(Camera).filter(Camera.camera_id == camera_identifier).first()
    if cam:
        return cam
    if camera_identifier.isdigit():
        return db.query(Camera).filter(Camera.id == int(camera_identifier)).first()
    return None


def build_camera_response(
    camera: Camera,
    redis_data: Optional[Any],
    redis_available: bool,
) -> CameraResponse:
    """Construye un CameraResponse a partir del modelo SQL y los datos de telemetría."""
    canonical_status, telemetry_data = resolve_camera_status(
        is_active=camera.is_active,
        redis_data=redis_data,
        redis_available=redis_available,
    )
    telemetry_obj = CameraTelemetry(**telemetry_data) if isinstance(telemetry_data, dict) else None

    return CameraResponse(
        id=camera.id,
        camera_id=camera.camera_id,
        name=camera.name,
        location=camera.location,
        rtsp_url=sanitize_rtsp_url(camera.rtsp_url),
        is_active=camera.is_active,
        created_at=camera.created_at,
        operational_status=canonical_status,
        telemetry=telemetry_obj,
    )


def enrich_camera_response(camera: Camera) -> CameraResponse:
    """Enriquece una única cámara consultando Redis individualmente."""
    redis_data, redis_available = fetch_worker_status(camera.camera_id)
    return build_camera_response(camera, redis_data, redis_available)


@router.get("", response_model=List[CameraResponse])
def list_cameras(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    Lista cámaras registradas optimizando la consulta a Redis mediante una sola
    operación en lote (MGET) para evitar el problema N+1.
    """
    cameras = db.query(Camera).offset(skip).limit(limit).all()
    if not cameras:
        return []

    camera_ids = [c.camera_id for c in cameras]
    statuses_map, redis_available = fetch_worker_statuses(camera_ids)

    return [
        build_camera_response(cam, statuses_map.get(cam.camera_id), redis_available)
        for cam in cameras
    ]


@router.post("", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
def create_camera(payload: CameraCreate, db: Session = Depends(get_db)):
    existing = db.query(Camera).filter(Camera.camera_id == payload.camera_id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ya existe una cámara registrada con el ID '{payload.camera_id}'.",
        )

    camera = Camera(
        camera_id=payload.camera_id,
        name=payload.name,
        location=payload.location,
        rtsp_url=payload.rtsp_url,
        is_active=payload.is_active,
    )
    db.add(camera)
    db.commit()
    db.refresh(camera)
    return enrich_camera_response(camera)


@router.get("/{camera_id}", response_model=CameraResponse)
def get_camera(camera_id: str, db: Session = Depends(get_db)):
    camera = find_camera(db, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Cámara no encontrada.")
    return enrich_camera_response(camera)


@router.patch("/{camera_id}", response_model=CameraResponse)
def update_camera(camera_id: str, payload: CameraUpdate, db: Session = Depends(get_db)):
    camera = find_camera(db, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Cámara no encontrada.")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(camera, field, value)

    db.commit()
    db.refresh(camera)
    return enrich_camera_response(camera)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: str, db: Session = Depends(get_db)):
    camera = find_camera(db, camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Cámara no encontrada.")

    actual_camera_id = camera.camera_id
    db.delete(camera)
    db.commit()
    purge_worker_status(actual_camera_id)
    return None
