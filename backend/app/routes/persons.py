import base64
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session
import numpy as np
import cv2

from backend.app.database import get_db
from backend.app.models import TargetPerson
from backend.app.schemas import TargetPersonCreate, TargetPersonResponse
from backend.app.storage import storage_client
from backend.app.config import settings
from search_service.vector_indexer import FaceSearchEngine

router = APIRouter(prefix="/persons", tags=["Personas Buscadas / Identidades"])
logger = logging.getLogger("backend.routes.persons")

# Motor FAISS compartido
_engine_instance: Optional[FaceSearchEngine] = None
_face_app = None


def get_search_engine() -> FaceSearchEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = FaceSearchEngine(
            index_path=settings.FAISS_INDEX_PATH,
            similarity_threshold=settings.DEFAULT_SIMILARITY_THRESHOLD,
        )
    return _engine_instance


def get_face_analyzer():
    """Inicializa y cachea el modelo FaceAnalysis de InsightFace como singleton."""
    global _face_app
    if _face_app is None:
        try:
            from insightface.app import FaceAnalysis
            app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=-1, det_size=(640, 640))
            _face_app = app
            logger.info("[✓] Analizador InsightFace (buffalo_l) listo en Backend.")
        except Exception as e:
            logger.error(f"[!] Error inicializando InsightFace: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error al inicializar el modelo biométrico InsightFace: {e}",
            )
    return _face_app


@router.get("", response_model=List[TargetPersonResponse])
def list_target_persons(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(TargetPerson).offset(skip).limit(limit).all()


@router.post("", response_model=TargetPersonResponse, status_code=status.HTTP_201_CREATED)
async def create_target_person(
    full_name: str = Form(...),
    document_id: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """
    Registra una persona buscada en la base de datos y extrae su embedding para indexarlo en FAISS.
    La fotografía es obligatoria y debe contener un rostro claramente reconocible.
    """
    if document_id:
        existing = db.query(TargetPerson).filter(TargetPerson.document_id == document_id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Ya existe una persona registrada con ese número de documento.")

    if not photo or not photo.filename:
        raise HTTPException(
            status_code=400,
            detail="La fotografía es obligatoria para registrar a una persona en la base biométrica.",
        )

    content = await photo.read()
    if not content:
        raise HTTPException(status_code=400, detail="El archivo de fotografía subido está vacío.")

    # Decodificar imagen para extracción de embedding
    np_arr = np.frombuffer(content, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if img is None:
        raise HTTPException(
            status_code=400,
            detail="No se pudo decodificar la imagen. Verifique que sea un formato válido JPG o PNG.",
        )

    # Inferencia facial con InsightFace
    analyzer = get_face_analyzer()
    try:
        faces = analyzer.get(img)
    except Exception as e:
        logger.error(f"Fallo durante la extracción de embedding: {e}")
        raise HTTPException(status_code=500, detail=f"Error durante el análisis facial: {e}")

    if not faces:
        raise HTTPException(
            status_code=400,
            detail="No se detectó ningún rostro en la fotografía. Suba una foto frontal, nítida y bien iluminada donde el rostro sea claramente visible.",
        )

    # Rostro con mejor detección
    best_face = max(faces, key=lambda f: f.det_score)
    embedding = best_face.embedding
    logger.info(f"Embedding extraído para '{full_name}'. Score de detección: {best_face.det_score:.3f}")

    # Guardar foto en almacenamiento
    photo_url = storage_client.save_image_bytes(content, filename=f"person_{photo.filename}")

    # Determinar el siguiente faiss_id disponible
    engine = get_search_engine()
    max_id_db = db.query(TargetPerson.faiss_id).order_by(TargetPerson.faiss_id.desc()).first()
    next_faiss_id = (max_id_db[0] + 1) if max_id_db else 1001

    person = TargetPerson(
        full_name=full_name,
        document_id=document_id,
        notes=notes,
        photo_url=photo_url,
        faiss_id=next_faiss_id,
        is_active=True,
    )
    db.add(person)
    db.commit()
    db.refresh(person)

    # Insertar en índice FAISS
    engine.add_identity(
        subject_id=next_faiss_id,
        embedding=embedding,
        name=full_name,
        extra_meta={"document_id": document_id, "notes": notes},
        auto_save=True,
    )

    # Notificar a Search Service para recargar si Redis está habilitado
    if settings.ENABLE_REDIS:
        try:
            import redis
            r = redis.Redis.from_url(settings.REDIS_URL)
            r.publish("index_updates", "reload")
        except Exception:
            pass

    return person


@router.post("/reindex")
def reindex_all_target_persons(db: Session = Depends(get_db)):
    """
    Reconstruye el índice FAISS a partir de todas las personas registradas en la base de datos
    y sus fotografías almacenadas.
    """
    engine = get_search_engine()
    analyzer = get_face_analyzer()

    persons = db.query(TargetPerson).filter(TargetPerson.is_active == True).all()
    engine.reset()

    indexed_count = 0
    errors = []

    for p in persons:
        if not p.photo_url:
            continue
        img_bytes = storage_client.get_image_bytes(p.photo_url)
        if not img_bytes:
            errors.append(f"No se encontró archivo para {p.full_name} ({p.photo_url})")
            continue

        np_arr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if img is None:
            errors.append(f"Error decodificando imagen de {p.full_name}")
            continue

        faces = analyzer.get(img)
        if not faces:
            errors.append(f"No se detectó rostro en foto guardada de {p.full_name}")
            continue

        best = max(faces, key=lambda f: f.det_score)
        engine.add_identity(
            subject_id=p.faiss_id,
            embedding=best.embedding,
            name=p.full_name,
            extra_meta={"document_id": p.document_id, "notes": p.notes},
            auto_save=False,
        )
        indexed_count += 1

    engine.save()

    return {
        "status": "success",
        "total_active_persons": len(persons),
        "indexed_count": indexed_count,
        "errors": errors,
    }


@router.get("/{person_id}", response_model=TargetPersonResponse)
def get_target_person(person_id: int, db: Session = Depends(get_db)):
    person = db.query(TargetPerson).filter(TargetPerson.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Persona no encontrada.")
    return person


@router.delete("/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target_person(person_id: int, db: Session = Depends(get_db)):
    person = db.query(TargetPerson).filter(TargetPerson.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Persona no encontrada.")

    # Remover del índice FAISS si aplica
    engine = get_search_engine()
    try:
        engine.index.remove_ids(np.array([person.faiss_id], dtype=np.int64))
        if person.faiss_id in engine.metadata:
            del engine.metadata[person.faiss_id]
        engine.save()
    except Exception as e:
        logger.warning(f"No fue posible remover ID de FAISS: {e}")

    db.delete(person)
    db.commit()
    return None
