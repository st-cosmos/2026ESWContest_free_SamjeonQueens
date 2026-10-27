import { useEffect, useRef } from 'react';

export function useWebSocket(onMessage: (data: any) => void) {
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | null = null;

    function connect() {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws`;
      ws = new WebSocket(wsUrl);

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          onMessageRef.current(data);
        } catch {
          // 무시
        }
      };

      ws.onclose = () => {
        timer = window.setTimeout(connect, 2000);
      };

      ws.onerror = () => {
        ws?.close();
      };
    }

    connect();

    return () => {
      if (timer) clearTimeout(timer);
      if (ws) ws.close();
    };
  }, []);
}
