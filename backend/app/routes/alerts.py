from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import AlertEvent, Camera, TargetPerson
from backend.app.schemas import AlertResponse, AlertStatusUpdate, SystemStats
from backend.app.routes.persons import get_search_engine

router = APIRouter(tags=["Alertas e Historial"])


@router.get("/alerts", response_model=List[AlertResponse])
def list_alerts(
    camera_id: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    query = db.query(AlertEvent)
    if camera_id:
        query = query.filter(AlertEvent.camera_id == camera_id)
    if status:
        query = query.filter(AlertEvent.status == status)

    return query.order_by(AlertEvent.timestamp.desc()).offset(skip).limit(limit).all()


@router.patch("/alerts/{alert_id}/status", response_model=AlertResponse)
def update_alert_status(alert_id: str, payload: AlertStatusUpdate, db: Session = Depends(get_db)):
    alert = db.query(AlertEvent).filter(AlertEvent.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alerta no encontrada.")

    valid_statuses = ["pending", "reviewed", "confirmed", "dismissed"]
    if payload.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Estado inválido. Debe ser uno de: {valid_statuses}")

    alert.status = payload.status
    db.commit()
    db.refresh(alert)
    return alert


@router.get("/stats", response_model=SystemStats)
def get_system_stats(db: Session = Depends(get_db)):
    total_cameras = db.query(Camera).count()
    active_cameras = db.query(Camera).filter(Camera.is_active == True).count()
    total_persons = db.query(TargetPerson).count()
    total_alerts = db.query(AlertEvent).count()
    pending_alerts = db.query(AlertEvent).filter(AlertEvent.status == "pending").count()

    engine = get_search_engine()
    faiss_count = engine.index.ntotal if engine and engine.index else 0

    return SystemStats(
        total_cameras=total_cameras,
        active_cameras=active_cameras,
        total_target_persons=total_persons,
        total_alerts=total_alerts,
        pending_alerts=pending_alerts,
        faiss_indexed_identities=faiss_count,
    )
