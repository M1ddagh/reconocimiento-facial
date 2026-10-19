import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.websocket_manager import ws_manager

router = APIRouter(tags=["WebSockets"])
logger = logging.getLogger("backend.ws")


@router.websocket("/ws/alerts")
async def websocket_alerts_endpoint(websocket: WebSocket):
    """
    Canal WebSocket para transmisión push en tiempo real de alertas biométricas.
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            # Mantener conexión abierta y escuchar pings/mensajes del cliente
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type": "pong"}')
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as ex:
        logger.warning(f"Error en websocket /ws/alerts: {ex}")
        ws_manager.disconnect(websocket)
