import time
import uuid
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, ForeignKey, DateTime
from sqlalchemy.sql import func
from backend.app.database import Base


class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=False)
    location = Column(String(300), nullable=True)
    rtsp_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(Float, default=time.time)


class TargetPerson(Base):
    __tablename__ = "target_persons"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(250), nullable=False, index=True)
    document_id = Column(String(100), unique=True, index=True, nullable=True)
    notes = Column(Text, nullable=True)
    photo_url = Column(String(500), nullable=True)
    faiss_id = Column(Integer, unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(Float, default=time.time)


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(String(64), unique=True, index=True, default=lambda: str(uuid.uuid4()))
    camera_id = Column(String(100), index=True, nullable=False)
    matched_subject_id = Column(Integer, index=True, nullable=False)
    subject_name = Column(String(250), nullable=True)
    similarity = Column(Float, nullable=False)
    bbox = Column(String(100), nullable=True)  # e.g., "120,80,250,220"
    crop_url = Column(String(500), nullable=True)
    crop_base64 = Column(Text, nullable=True)
    status = Column(String(50), default="pending", index=True)  # pending, reviewed, dismissed
    timestamp = Column(Float, default=time.time, index=True)
