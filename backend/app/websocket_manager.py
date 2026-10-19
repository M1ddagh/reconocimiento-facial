import json
import logging
from typing import List
from fastapi import WebSocket

logger = logging.getLogger("backend.websocket")


class ConnectionManager:
    """
    Administrador de conexiones WebSocket para el Dashboard en tiempo real.
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"[+] Nuevo cliente WebSocket conectado. Total activos: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"[-] Cliente WebSocket desconectado. Restantes: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Envía un mensaje a todos los dashboards conectados."""
        if not self.active_connections:
            return

        payload = json.dumps(message)
        disconnected = []

        for connection in self.active_connections:
            try:
                await connection.send_text(payload)
            except Exception as e:
                logger.warning(f"Fallo al enviar a cliente WebSocket: {e}")
                disconnected.append(connection)

        for dead_conn in disconnected:
            self.disconnect(dead_conn)


ws_manager = ConnectionManager()
