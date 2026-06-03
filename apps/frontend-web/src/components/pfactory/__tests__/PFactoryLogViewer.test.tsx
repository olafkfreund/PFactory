/**
 * @vitest-environment jsdom
 *
 * Tests for <PFactoryLogViewer> — Task 10 (#11) commit 4.
 *
 * Uses a FakeWebSocket helper injected via the ``wsFactory`` prop so
 * tests can manually drive open/message/close lifecycle without a
 * real socket server.
 */

import { describe, it, expect, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { act, render, screen, waitFor } from '@testing-library/react';

import {
  PFactoryLogViewer,
  buildLogStreamUrl,
} from '../PFactoryLogViewer';

// ── FakeWebSocket ─────────────────────────────────────────────────────

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState: number = FakeWebSocket.CONNECTING;
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }

  triggerOpen() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.(new Event('open'));
  }

  triggerMessage(data: unknown) {
    const text = typeof data === 'string' ? data : JSON.stringify(data);
    this.onmessage?.(new MessageEvent('message', { data: text }));
  }

  triggerClose(code = 1000, reason = '') {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.(new CloseEvent('close', { code, reason }));
  }

  triggerError() {
    this.onerror?.(new Event('error'));
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }
}

// vitest's jsdom may not define WebSocket; install it for the lifetime
// of these tests so readyState comparisons in the component work.
if (typeof globalThis.WebSocket === 'undefined') {
  // @ts-expect-error — jsdom shim
  globalThis.WebSocket = FakeWebSocket;
}

// ── buildLogStreamUrl ────────────────────────────────────────────────

describe('buildLogStreamUrl', () => {
  it('uses VITE_WS_BASE_URL when provided', () => {
    expect(buildLogStreamUrl('042-x', 'ws://api.example.com:3102')).toBe(
      'ws://api.example.com:3102/api/pfactory/tasks/042-x/logs/stream',
    );
  });

  it('strips trailing slash from env base', () => {
    expect(buildLogStreamUrl('042-x', 'ws://api.example.com:3102/')).toBe(
      'ws://api.example.com:3102/api/pfactory/tasks/042-x/logs/stream',
    );
  });

  it('promotes http → ws via window.location fallback', () => {
    expect(buildLogStreamUrl(
      '042-x', undefined,
      { protocol: 'http:', host: 'localhost:3100' },
    )).toBe('ws://localhost:3100/api/pfactory/tasks/042-x/logs/stream');
  });

  it('promotes https → wss', () => {
    expect(buildLogStreamUrl(
      '042-x', undefined,
      { protocol: 'https:', host: 'pfactory.example.com' },
    )).toBe('wss://pfactory.example.com/api/pfactory/tasks/042-x/logs/stream');
  });
});

// ── Connection lifecycle ──────────────────────────────────────────────

describe('<PFactoryLogViewer> lifecycle', () => {
  it('shows connecting state immediately', () => {
    const created: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws as unknown as WebSocket;
    };
    render(<PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />);
    expect(screen.getByTestId('pfactory-log-viewer')).toHaveAttribute(
      'data-ws-state', 'connecting',
    );
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(created.length).toBe(1);
  });

  it('transitions to open on socket open', async () => {
    const created: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws as unknown as WebSocket;
    };
    render(<PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />);
    act(() => { created[0].triggerOpen(); });
    await waitFor(() =>
      expect(screen.getByTestId('pfactory-log-viewer')).toHaveAttribute(
        'data-ws-state', 'open',
      ),
    );
    expect(screen.getByTestId('ws-status-open')).toBeInTheDocument();
  });

  it('renders log sections after receiving a payload', async () => {
    const created: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws as unknown as WebSocket;
    };
    render(<PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />);
    act(() => {
      created[0].triggerOpen();
      created[0].triggerMessage({
        spec_id: '042-x',
        captured_at: '2026-05-28T00:00:00+00:00',
        files: {
          planner: ['p1', 'p2', 'p3'],
          gen_functional: ['g1'],
          evaluator: ['e1', 'e2'],
        },
      });
    });
    await waitFor(() => screen.getByTestId('log-section-planner'));
    expect(screen.getByTestId('log-content-planner').textContent).toBe('p1\np2\np3');
    expect(screen.getByTestId('log-content-gen_functional').textContent).toBe('g1');
    expect(screen.getByTestId('log-content-evaluator').textContent).toBe('e1\ne2');
  });

  it('shows empty-files placeholder when payload has no files', async () => {
    const created: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws as unknown as WebSocket;
    };
    render(<PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />);
    act(() => {
      created[0].triggerOpen();
      created[0].triggerMessage({
        spec_id: '042-x',
        captured_at: '2026-05-28T00:00:00+00:00',
        files: {},
      });
    });
    await waitFor(() => screen.getByTestId('log-files-empty'));
    expect(screen.getByTestId('log-files-empty').textContent).toMatch(/No log files yet/i);
  });

  it('shows custom error when close code is 4404', async () => {
    const created: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws as unknown as WebSocket;
    };
    render(<PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />);
    act(() => { created[0].triggerClose(4404, 'task not found: 042-x'); });
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText(/Task not found: 042-x/i)).toBeInTheDocument();
  });

  it('shows custom error when close code is 4400', async () => {
    const created: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws as unknown as WebSocket;
    };
    render(<PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />);
    act(() => { created[0].triggerClose(4400, 'invalid spec_id'); });
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText(/Invalid spec_id/i)).toBeInTheDocument();
  });

  it('shows closed status when socket closes cleanly', async () => {
    const created: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws as unknown as WebSocket;
    };
    render(<PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />);
    act(() => {
      created[0].triggerOpen();
      created[0].triggerClose(1000, '');
    });
    await waitFor(() => screen.getByTestId('ws-status-closed'));
    expect(screen.getByText(/Connection closed/i)).toBeInTheDocument();
  });

  it('shows error state on socket error', async () => {
    const created: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws as unknown as WebSocket;
    };
    render(<PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />);
    act(() => { created[0].triggerError(); });
    await waitFor(() =>
      expect(screen.getByTestId('pfactory-log-viewer')).toHaveAttribute(
        'data-ws-state', 'error',
      ),
    );
    expect(screen.getByText(/WebSocket error/i)).toBeInTheDocument();
  });

  it('gracefully handles malformed JSON payload', async () => {
    const created: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws as unknown as WebSocket;
    };
    render(<PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />);
    act(() => {
      created[0].triggerOpen();
      created[0].triggerMessage('not json{');
    });
    await waitFor(() =>
      expect(screen.getByText(/Failed to parse/i)).toBeInTheDocument(),
    );
  });

  it('throws-during-construction is caught and surfaced as error', () => {
    const wsFactory = () => {
      throw new Error('window.WebSocket missing');
    };
    render(<PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />);
    // No need to wait — error is set synchronously in the effect
    expect(screen.getByText(/window.WebSocket missing/i)).toBeInTheDocument();
  });

  it('closes the socket on unmount', async () => {
    const created: FakeWebSocket[] = [];
    const wsFactory = (url: string) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws as unknown as WebSocket;
    };
    const { unmount } = render(
      <PFactoryLogViewer specId="042-x" wsFactory={wsFactory} />,
    );
    act(() => { created[0].triggerOpen(); });
    const closeSpy = vi.spyOn(created[0], 'close');
    unmount();
    expect(closeSpy).toHaveBeenCalled();
  });
});
