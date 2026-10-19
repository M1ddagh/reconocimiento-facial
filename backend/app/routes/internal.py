#!/usr/bin/env python3
from __future__ import annotations

"""
backend/app/routes/internal.py
-------------------------------
Endpoints protegidos para consumo exclusivo de servicios internos y supervisores.
Entrega configuración sensible (URLs RTSP completas con credenciales).
"""

import hmac
import ipaddress
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import Camera
from backend.app.schemas import InternalCameraResponse

logger = logging.getLogger("backend.routes.internal")

router = APIRouter(prefix="/internal", tags=["Interno - Supervisor"])


def is_private_or_loopback_ip(ip_str: str) -> bool:
    """Verifica si una dirección IP pertenece a localhost o redes privadas (RFC 1918 / loopback)."""
    if ip_str in ("testclient", "localhost"):
        return True
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


def verify_internal_secret(
    request: Request,
    x_internal_secret: str = Header(..., alias="X-Internal-Secret"),
) -> str:
    """
    Dependencia de seguridad que valida el secreto interno compartido
    y restringe el acceso en producción a la red privada o loopback.
    """
    # 1. Validar presencia del secreto configurado en el servidor
    configured_secret = settings.INTERNAL_API_SECRET
    if not configured_secret or not configured_secret.strip():
        logger.error("[Seguridad Interna] INTERNAL_API_SECRET no está configurado en el servidor.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error de configuración: INTERNAL_API_SECRET no está configurado en el servidor.",
        )

    # 2. Comparación en tiempo constante contra ataques de temporización
    if not hmac.compare_digest(x_internal_secret, configured_secret):
        logger.warning(f"[Seguridad Interna] Intento de acceso no autorizado a ruta interna desde {request.client.host if request.client else 'desconocido'}.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: Secreto interno inválido.",
        )

    # 3. Restricción de red en producción (defensa en profundidad)
    if settings.ENVIRONMENT == "production":
        client_ip = request.client.host if request.client else ""
        if not is_private_or_loopback_ip(client_ip):
            logger.error(f"[Seguridad Interna] Acceso rechazado desde IP no privada en producción: {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acceso restringido: Las rutas internas solo son accesibles desde la red privada.",
            )

    return x_internal_secret


@router.get(
    "/cameras",
    response_model=List[InternalCameraResponse],
    dependencies=[Depends(verify_internal_secret)],
)
def list_cameras_internal(
    active_only: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """
    Endpoint protegido para el supervisor de AI Workers.
    Retorna la lista de cámaras con URLs RTSP originales (sin enmascarar credenciales).
    """
    query = db.query(Camera)
    if active_only is not None:
        query = query.filter(Camera.is_active == active_only)
    return query.all()
