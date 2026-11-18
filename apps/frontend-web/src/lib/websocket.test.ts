import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { wsManager } from './websocket';

vi.mock('./auth', () => ({ getAuthenticatedWsUrl: (endpoint: string) => endpoint }));

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => { this.readyState = 3; });

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  message(data: string) {
    this.onmessage?.({ data });
  }

  serverClose(code: number) {
    this.readyState = 3;
    this.onclose?.({ code, reason: '' });
  }
}

const endpoint = '/ws/test';
const lastSocket = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('WebSocket', FakeWebSocket);
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  FakeWebSocket.instances = [];
});

afterEach(() => {
  wsManager.disconnectAll();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('WebSocketManager subscriptions', () => {
  it.each([1006, 4001])('keeps a subscription after close code %i', (code) => {
    const handler = vi.fn();
    wsManager.subscribe(endpoint, handler);
    const first = lastSocket();
    first.open();
    first.serverClose(code);

    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    lastSocket().open();
    lastSocket().message('{"value":1}');
    expect(handler).toHaveBeenCalledExactlyOnceWith({ value: 1 });
  });

  it('unsubscribes after reconnect and closes the current socket', () => {
    const handler = vi.fn();
    const unsubscribe = wsManager.subscribe(endpoint, handler);
    lastSocket().serverClose(1006);
    vi.advanceTimersByTime(1000);
    const current = lastSocket();
    current.open();

    unsubscribe();
    expect(current.close).toHaveBeenCalledWith(1000, 'Client disconnect');
    current.message('ignored');
    expect(handler).not.toHaveBeenCalled();
  });

  it('delivers each message once after three reconnects', () => {
    const handler = vi.fn();
    wsManager.subscribe(endpoint, handler);
    for (let attempt = 0; attempt < 3; attempt++) {
      lastSocket().serverClose(1006);
      vi.advanceTimersByTime(1000);
      lastSocket().open();
      lastSocket().message(String(attempt));
    }
    expect(handler.mock.calls).toEqual([[0], [1], [2]]);
  });

  it('clears handlers on explicit disconnect', () => {
    const handler = vi.fn();
    wsManager.subscribe(endpoint, handler);
    const oldSocket = lastSocket();
    wsManager.disconnect(endpoint);
    wsManager.connect(endpoint);
    lastSocket().open();
    lastSocket().message('ignored');
    oldSocket.message('also ignored');
    expect(handler).not.toHaveBeenCalled();
  });

  it('keeps a new subscription when an old unsubscribe runs', () => {
    const oldUnsubscribe = wsManager.subscribe(endpoint, vi.fn());
    wsManager.disconnect(endpoint);
    const handler = vi.fn();
    wsManager.subscribe(endpoint, handler);

    oldUnsubscribe();
    lastSocket().open();
    lastSocket().message('current');
    expect(handler).toHaveBeenCalledExactlyOnceWith('current');
    expect(lastSocket().close).not.toHaveBeenCalled();
  });

  it('does not replace a newer socket when an old reconnect timer fires', () => {
    const handler = vi.fn();
    wsManager.subscribe(endpoint, handler);
    const oldSocket = lastSocket();
    oldSocket.serverClose(1006);
    wsManager.connect(endpoint);
    const current = lastSocket();
    current.open();

    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    oldSocket.serverClose(1006);
    oldSocket.message('stale');
    current.message('current');
    expect(handler).toHaveBeenCalledExactlyOnceWith('current');
  });

  it('logs a throwing handler once and continues to the next handler', () => {
    const throwing = vi.fn(() => { throw new Error('handler failed'); });
    const following = vi.fn();
    wsManager.subscribe(endpoint, throwing);
    wsManager.subscribe(endpoint, following);
    lastSocket().open();
    lastSocket().message('{"value":1}');

    expect(throwing).toHaveBeenCalledExactlyOnceWith({ value: 1 });
    expect(following).toHaveBeenCalledExactlyOnceWith({ value: 1 });
    expect(console.error).toHaveBeenCalledOnce();
  });

  it('pings every 25s and starts a new heartbeat after reconnect', () => {
    wsManager.subscribe(endpoint, vi.fn());
    const first = lastSocket();
    first.open();
    vi.advanceTimersByTime(25000);
    expect(first.send).toHaveBeenCalledWith('ping');

    first.serverClose(1006);
    expect(vi.getTimerCount()).toBe(1); // only the reconnect timer
    vi.advanceTimersByTime(1000);
    lastSocket().open();
    vi.advanceTimersByTime(25000);
    expect(lastSocket().send).toHaveBeenCalledWith('ping');
  });

  it('a stale socket close clears its own heartbeat', () => {
    wsManager.subscribe(endpoint, vi.fn());
    const stale = lastSocket();
    stale.open();
    stale.readyState = 3; // dropped without onclose yet
    const current = wsManager.connect(endpoint) as unknown as FakeWebSocket;
    current.open();
    expect(vi.getTimerCount()).toBe(2);
    stale.serverClose(1006);
    expect(vi.getTimerCount()).toBe(1); // stale heartbeat gone, no reconnect scheduled
  });
});
