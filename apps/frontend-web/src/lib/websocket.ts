/**
 * WebSocket manager for real-time features
 * Handles terminal, logs, and progress streaming
 */

import { getAuthenticatedWsUrl } from './auth';

type MessageHandler = (data: unknown) => void;
type ConnectionHandler = () => void;

interface WebSocketConnection {
  ws: WebSocket;
  handlers: Set<MessageHandler>;
  reconnectAttempts: number;
  reconnectTimeout?: ReturnType<typeof setTimeout>;
}

class WebSocketManager {
  private connections: Map<string, WebSocketConnection> = new Map();
  private onConnectHandlers: Map<string, Set<ConnectionHandler>> = new Map();
  private onDisconnectHandlers: Map<string, Set<ConnectionHandler>> = new Map();
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;

  /**
   * Connect to a WebSocket endpoint
   */
  connect(endpoint: string): WebSocket {
    const existing = this.connections.get(endpoint);
    if (existing && existing.ws.readyState === WebSocket.OPEN) {
      return existing.ws;
    }

    const url = getAuthenticatedWsUrl(endpoint);
    const ws = new WebSocket(url);

    const connection: WebSocketConnection = {
      ws,
      handlers: new Set(),
      reconnectAttempts: 0,
    };

    ws.onopen = () => {
      console.log(`[WebSocket] Connected: ${endpoint}`);
      connection.reconnectAttempts = 0;
      this.onConnectHandlers.get(endpoint)?.forEach((h) => h());
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        connection.handlers.forEach((handler) => handler(data));
      } catch {
        // Handle non-JSON messages (e.g., terminal raw output)
        connection.handlers.forEach((handler) => handler(event.data));
      }
    };

    ws.onclose = (event) => {
      console.log(`[WebSocket] Disconnected: ${endpoint}`, event.code, event.reason);
      this.onDisconnectHandlers.get(endpoint)?.forEach((h) => h());

      // Attempt reconnect for non-normal closures
      if (event.code !== 1000 && connection.reconnectAttempts < this.maxReconnectAttempts) {
        connection.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(2, connection.reconnectAttempts - 1);
        console.log(`[WebSocket] Reconnecting in ${delay}ms (attempt ${connection.reconnectAttempts})`);
        connection.reconnectTimeout = setTimeout(() => {
          this.connections.delete(endpoint);
          this.connect(endpoint);
        }, delay);
      }
    };

    ws.onerror = (error) => {
      console.error(`[WebSocket] Error: ${endpoint}`, error);
    };

    this.connections.set(endpoint, connection);
    return ws;
  }

  /**
   * Subscribe to messages on an endpoint
   */
  subscribe(endpoint: string, handler: MessageHandler): () => void {
    let connection = this.connections.get(endpoint);
    if (!connection) {
      this.connect(endpoint);
      connection = this.connections.get(endpoint)!;
    }
    connection.handlers.add(handler);

    return () => {
      connection.handlers.delete(handler);
      // Close connection if no more handlers
      if (connection.handlers.size === 0) {
        this.disconnect(endpoint);
      }
    };
  }

  /**
   * Send data through WebSocket
   */
  send(endpoint: string, data: unknown): boolean {
    const connection = this.connections.get(endpoint);
    if (!connection || connection.ws.readyState !== WebSocket.OPEN) {
      console.warn(`[WebSocket] Cannot send, not connected: ${endpoint}`);
      return false;
    }

    const message = typeof data === 'string' ? data : JSON.stringify(data);
    connection.ws.send(message);
    return true;
  }

  /**
   * Disconnect from endpoint
   */
  disconnect(endpoint: string): void {
    const connection = this.connections.get(endpoint);
    if (connection) {
      if (connection.reconnectTimeout) {
        clearTimeout(connection.reconnectTimeout);
      }
      connection.ws.close(1000, 'Client disconnect');
      this.connections.delete(endpoint);
    }
  }

  /**
   * Register connection handler
   */
  onConnect(endpoint: string, handler: ConnectionHandler): () => void {
    if (!this.onConnectHandlers.has(endpoint)) {
      this.onConnectHandlers.set(endpoint, new Set());
    }
    this.onConnectHandlers.get(endpoint)!.add(handler);
    return () => this.onConnectHandlers.get(endpoint)?.delete(handler);
  }

  /**
   * Register disconnect handler
   */
  onDisconnect(endpoint: string, handler: ConnectionHandler): () => void {
    if (!this.onDisconnectHandlers.has(endpoint)) {
      this.onDisconnectHandlers.set(endpoint, new Set());
    }
    this.onDisconnectHandlers.get(endpoint)!.add(handler);
    return () => this.onDisconnectHandlers.get(endpoint)?.delete(handler);
  }

  /**
   * Get connection state
   */
  isConnected(endpoint: string): boolean {
    const connection = this.connections.get(endpoint);
    return connection?.ws.readyState === WebSocket.OPEN;
  }

  /**
   * Disconnect all
   */
  disconnectAll(): void {
    for (const endpoint of this.connections.keys()) {
      this.disconnect(endpoint);
    }
  }
}

// Singleton instance
export const wsManager = new WebSocketManager();

// Convenience functions for specific WebSocket types
export const terminalWs = {
  connect: (terminalId: string) => wsManager.connect(`/ws/terminal/${terminalId}`),
  subscribe: (terminalId: string, handler: MessageHandler) =>
    wsManager.subscribe(`/ws/terminal/${terminalId}`, handler),
  send: (terminalId: string, data: string) =>
    wsManager.send(`/ws/terminal/${terminalId}`, data),
  disconnect: (terminalId: string) =>
    wsManager.disconnect(`/ws/terminal/${terminalId}`),
};

export const taskLogsWs = {
  subscribe: (taskId: string, handler: MessageHandler) =>
    wsManager.subscribe(`/ws/tasks/${taskId}/logs`, handler),
  disconnect: (taskId: string) =>
    wsManager.disconnect(`/ws/tasks/${taskId}/logs`),
};

export const taskProgressWs = {
  subscribe: (taskId: string, handler: MessageHandler) =>
    wsManager.subscribe(`/ws/tasks/${taskId}/progress`, handler),
  disconnect: (taskId: string) =>
    wsManager.disconnect(`/ws/tasks/${taskId}/progress`),
};
