import { useEffect, useRef, useState } from 'react';
import type { AlertEvent } from '../types';

interface UseWebSocketOptions {
  onNewAlert?: (alert: AlertEvent) => void;
}

export const useWebSocket = (url: string, options: UseWebSocketOptions = {}) => {
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<any>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    let unmounted = false;

    const connect = () => {
      try {
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
          if (!unmounted) {
            setIsConnected(true);
            console.log('[WebSocket] Conectado al servidor de alertas.');
          }
        };

        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data);
            if (message.type === 'NEW_ALERT' && message.data) {
              if (optionsRef.current.onNewAlert) {
                optionsRef.current.onNewAlert(message.data);
              }
            }
          } catch (e) {
            console.error('[WebSocket] Error parseando mensaje:', e);
          }
        };

        ws.onclose = () => {
          if (!unmounted) {
            setIsConnected(false);
            console.warn('[WebSocket] Conexión cerrada. Reconectando en 3s...');
            reconnectTimeoutRef.current = setTimeout(connect, 3000);
          }
        };

        ws.onerror = (err) => {
          console.error('[WebSocket] Error de red:', err);
          ws.close();
        };
      } catch (err) {
        if (!unmounted) {
          reconnectTimeoutRef.current = setTimeout(connect, 3000);
        }
      }
    };

    connect();

    return () => {
      unmounted = true;
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [url]);

  return { isConnected };
};
