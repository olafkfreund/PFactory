/**
 * PFactory log viewer — Task 10 (#11) commit 4.
 *
 * Connects to ws(s)://.../api/pfactory/tasks/{spec_id}/logs/stream and
 * renders the JSON payload the backend (Task 9 commit 3) sends on
 * connect:
 *
 *   {
 *     "spec_id": "<id>",
 *     "captured_at": "<iso>",
 *     "files": {
 *       "planner": ["line1", "line2", ...],
 *       "gen_functional": [...],
 *       "evaluator": [...],
 *       ...
 *     }
 *   }
 *
 * The component injects a ``wsFactory`` prop so tests can swap in a
 * FakeWebSocket. URL construction uses VITE_WS_BASE_URL when set,
 * else derives ws(s):// from window.location.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Loader2, Plug, Unplug } from 'lucide-react';

// ── WS URL helper ────────────────────────────────────────────────────

/**
 * Build the absolute WebSocket URL for the log stream.
 *
 * Resolution order:
 *   1. ``VITE_WS_BASE_URL`` env (e.g., "ws://api.example.com:3102") +
 *      "/api/pfactory/tasks/{spec_id}/logs/stream".
 *   2. ``window.location`` — promote http(s) → ws(s); same host + port.
 *
 * Exposed for testing.
 */
export function buildLogStreamUrl(
  specId: string,
  envBase?: string,
  location?: { protocol: string; host: string },
): string {
  const path = `/api/pfactory/tasks/${specId}/logs/stream`;
  if (envBase) {
    // Allow caller to omit trailing slash; we always join with /api/...
    const cleanBase = envBase.replace(/\/+$/, '');
    return `${cleanBase}${path}`;
  }
  const loc = location ?? (typeof window !== 'undefined' ? window.location : null);
  if (!loc) return path;
  const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${loc.host}${path}`;
}

// ── Connection state ─────────────────────────────────────────────────

export type WsState = 'connecting' | 'open' | 'closed' | 'error';

interface Props {
  specId: string;
  /** Test seam: replace ``new WebSocket(url)``. */
  wsFactory?: (url: string) => WebSocket;
  /** Test seam: pin an env-style base. Otherwise reads VITE_WS_BASE_URL. */
  wsBaseUrl?: string;
}

// ── Log payload shape (mirrors backend tail_log_payload) ────────────

interface LogStreamPayload {
  spec_id: string;
  captured_at: string;
  files: Record<string, string[]>;
}

// ── Component ────────────────────────────────────────────────────────

export function PFactoryLogViewer({ specId, wsFactory, wsBaseUrl }: Props) {
  const [state, setState] = useState<WsState>('connecting');
  const [payload, setPayload] = useState<LogStreamPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  // We use a ref for the socket so cleanup can close it even after
  // unmount, without React warnings about state-in-cleanup.
  const socketRef = useRef<WebSocket | null>(null);

  const envBase = useMemo(() => {
    if (wsBaseUrl !== undefined) return wsBaseUrl;
    try {
      return (import.meta.env?.VITE_WS_BASE_URL as string | undefined) ?? undefined;
    } catch {
      return undefined;
    }
  }, [wsBaseUrl]);

  useEffect(() => {
    const url = buildLogStreamUrl(specId, envBase);
    const factory = wsFactory ?? ((u: string) => new WebSocket(u));
    let socket: WebSocket;
    try {
      socket = factory(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setState('error');
      return;
    }
    socketRef.current = socket;

    setState('connecting');
    setError(null);

    socket.onopen = () => setState('open');

    socket.onmessage = (ev: MessageEvent) => {
      try {
        const data = typeof ev.data === 'string' ? ev.data : '';
        const parsed = JSON.parse(data) as LogStreamPayload;
        setPayload(parsed);
        setError(null);
      } catch (err) {
        setError(`Failed to parse log payload: ${err instanceof Error ? err.message : err}`);
        // Treat as error state so the UI shows the alert clearly
        setState('error');
      }
    };

    socket.onerror = () => {
      setState('error');
      setError('WebSocket error');
    };

    socket.onclose = (ev: CloseEvent) => {
      setState('closed');
      // The backend uses custom codes 4400/4404 for known failures
      // (path-traversal / missing spec) — surface those distinctly.
      if (ev.code === 4400) setError('Invalid spec_id');
      else if (ev.code === 4404) setError(`Task not found: ${specId}`);
    };

    return () => {
      try {
        if (socket.readyState === WebSocket.OPEN
          || socket.readyState === WebSocket.CONNECTING) {
          socket.close();
        }
      } catch {
        // ignore
      }
      socketRef.current = null;
    };
  }, [specId, envBase, wsFactory]);

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div
      data-testid="pfactory-log-viewer"
      data-ws-state={state}
      className="flex flex-col gap-2"
    >
      <StatusLine state={state} error={error} />
      {payload ? (
        <LogPanels payload={payload} />
      ) : (
        <NoPayloadPlaceholder state={state} />
      )}
    </div>
  );
}

// ── Subcomponents ────────────────────────────────────────────────────

function StatusLine({ state, error }: { state: WsState; error: string | null }) {
  if (state === 'connecting') {
    return (
      <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        Connecting to log stream…
      </div>
    );
  }
  if (state === 'open') {
    return (
      <div data-testid="ws-status-open" className="flex items-center gap-2 text-sm text-success">
        <Plug className="h-3 w-3" aria-hidden />
        Live
      </div>
    );
  }
  if (state === 'closed' && !error) {
    return (
      <div data-testid="ws-status-closed" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Unplug className="h-3 w-3" aria-hidden />
        Connection closed
      </div>
    );
  }
  // error or closed-with-reason
  return (
    <div role="alert" className="flex items-center gap-2 text-sm text-destructive">
      <AlertTriangle className="h-3 w-3" aria-hidden />
      {error ?? 'WebSocket error'}
    </div>
  );
}

function NoPayloadPlaceholder({ state }: { state: WsState }) {
  if (state === 'connecting') return null;
  return (
    <p className="text-xs italic text-muted-foreground">
      No log data received yet.
    </p>
  );
}

function LogPanels({ payload }: { payload: LogStreamPayload }) {
  const fileNames = Object.keys(payload.files).sort();
  if (fileNames.length === 0) {
    return (
      <p
        data-testid="log-files-empty"
        className="text-xs italic text-muted-foreground"
      >
        No log files yet — agents haven't produced output for this task.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {fileNames.map((name) => (
        <section
          key={name}
          data-testid={`log-section-${name}`}
          className="rounded border border-border bg-muted"
        >
          <header className="border-b border-border px-3 py-1 font-mono text-xs text-foreground">
            {name}.log
          </header>
          <pre
            data-testid={`log-content-${name}`}
            className="max-h-80 overflow-auto whitespace-pre-wrap p-3 text-xs"
          >
            {payload.files[name].join('\n')}
          </pre>
        </section>
      ))}
    </div>
  );
}
