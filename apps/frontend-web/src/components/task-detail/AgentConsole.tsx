/**
 * AgentConsole — Live Agent Console tab for TaskDetailModal.
 *
 * Epic #44 R2.  Wires the browser xterm.js viewport to the rmux
 * pane-bridge WebSocket and renders the read-only / attach UX.
 *
 * Protocol contract with the backend (apps/web-server/server/rmux/bridge.py):
 *
 *   - Connect to ``ws://.../api/tasks/{task_id}/agent-console/ws``
 *   - First server frame is JSON: {"type":"connected","connection_id":"<uuid>"}
 *   - Subsequent server frames are binary pane bytes (ANSI intact)
 *   - In attach mode, we POST /attach with the connection_id then forward
 *     xterm `onData` keystrokes as binary frames back over the same WS
 *   - 409 from POST /attach means "another viewer holds attach" — UI
 *     surfaces a modal explaining
 *   - On unmount or WS close, the server releases attach automatically
 *     so we don't have to POST /detach in the cleanup path (we do it
 *     anyway as a courtesy, ignoring errors)
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';

import { getAuthenticatedWsUrl } from '../../lib/auth';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../ui/alert-dialog';

type WsStatus = 'connecting' | 'open' | 'closed';
type AttachState =
  | { kind: 'read-only' }
  | { kind: 'attaching' }
  | { kind: 'attached' }
  | { kind: 'race-lost' };

interface AgentConsoleProps {
  /** Composite task ID — ``projectId:specId`` as the backend expects. */
  taskId: string;
}

function getApiBaseUrl(): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const env = (import.meta as any)?.env ?? {};
  return (env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '');
}

export function AgentConsole({ taskId }: AgentConsoleProps) {
  const { t } = useTranslation(['tasks']);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const xtermRef = useRef<XTerm | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  // Stored in a ref because we read/write it from a non-React WS
  // event handler and React state updates wouldn't propagate fast
  // enough to influence the next onData callback.
  const connectionIdRef = useRef<string | null>(null);
  const attachStateRef = useRef<AttachState>({ kind: 'read-only' });

  const [wsStatus, setWsStatus] = useState<WsStatus>('connecting');
  const [attachState, setAttachState] = useState<AttachState>({ kind: 'read-only' });
  const [showAttachConfirm, setShowAttachConfirm] = useState(false);
  const [showRaceLost, setShowRaceLost] = useState(false);

  /** Keep ref in sync with state for the onData closure. */
  useEffect(() => {
    attachStateRef.current = attachState;
  }, [attachState]);

  /**
   * Mount: spin up xterm, open the WS, wire the data plane.
   * Unmount: tear it all down in reverse order.
   */
  useEffect(() => {
    if (!containerRef.current) return;

    const xterm = new XTerm({
      cursorBlink: true,
      cursorStyle: 'block',
      fontSize: 13,
      fontFamily: 'var(--font-mono), Menlo, Monaco, "Courier New", monospace',
      scrollback: 10000,
      theme: {
        background: '#0B0B0F',
        foreground: '#E8E6E3',
        cursor: '#D6D876',
        selectionBackground: '#D6D87640',
      },
      allowProposedApi: true,
    });
    const fit = new FitAddon();
    xterm.loadAddon(fit);
    xterm.loadAddon(new WebLinksAddon());
    xterm.open(containerRef.current);
    try {
      fit.fit();
    } catch {
      // ResizeObserver hasn't fired yet — first fit() can fail under
      // jsdom or during unmount race; harmless.
    }
    xtermRef.current = xterm;
    fitAddonRef.current = fit;

    // Forward xterm keystrokes — but ONLY when this connection holds
    // attach mode.  Reading attachStateRef directly because the
    // onData closure was bound on mount and the React state inside it
    // would otherwise be stale.
    const onDataDisposable = xterm.onData((data) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (attachStateRef.current.kind !== 'attached') return;
      ws.send(data);
    });

    // Open the WS.  ``getAuthenticatedWsUrl`` is the same helper the
    // logs/terminal/progress WS clients use — it pulls the bearer token
    // out of the auth store and appends ``?token=...`` so the FastAPI
    // ``verify_websocket_token`` middleware accepts the upgrade.  Without
    // it the server closes the WS with code 4001 before our onopen
    // fires and the UI stays at "Connecting…" forever.
    const url = getAuthenticatedWsUrl(`/api/tasks/${encodeURIComponent(taskId)}/agent-console/ws`);
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    ws.onopen = () => setWsStatus('open');
    ws.onclose = () => setWsStatus('closed');
    ws.onerror = () => setWsStatus('closed');

    ws.onmessage = (ev) => {
      // First frame is the JSON `connected` envelope.  Everything
      // else is raw binary pane bytes that go straight to xterm.
      if (typeof ev.data === 'string') {
        try {
          const parsed = JSON.parse(ev.data);
          if (parsed?.type === 'connected' && typeof parsed.connection_id === 'string') {
            connectionIdRef.current = parsed.connection_id;
            return;
          }
        } catch {
          // Treat unparseable text as plain output (defensive — the
          // backend should never send untyped strings).
          xterm.write(ev.data);
          return;
        }
        xterm.write(ev.data);
        return;
      }
      // Binary frame.  ev.data is ArrayBuffer because we set
      // binaryType above.  xterm.write accepts Uint8Array.
      const bytes = new Uint8Array(ev.data as ArrayBuffer);
      xterm.write(bytes);
    };

    // Resize: refit xterm whenever the container changes size.
    const ro = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        /* swallow */
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      onDataDisposable.dispose();
      try {
        ws.close();
      } catch {
        /* swallow */
      }
      wsRef.current = null;
      try {
        xterm.dispose();
      } catch {
        /* swallow */
      }
      xtermRef.current = null;
      fitAddonRef.current = null;
    };
  }, [taskId]);

  // -----------------------------------------------------------------
  // Attach / detach handlers
  // -----------------------------------------------------------------

  const requestAttach = useCallback(async () => {
    const cid = connectionIdRef.current;
    if (!cid) {
      // Shouldn't happen — the button is disabled until we have the cid
      return;
    }
    setAttachState({ kind: 'attaching' });

    try {
      const res = await fetch(
        `${getApiBaseUrl()}/tasks/${encodeURIComponent(taskId)}/agent-console/attach`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ connection_id: cid }),
        },
      );
      if (res.status === 200) {
        setAttachState({ kind: 'attached' });
        return;
      }
      if (res.status === 409) {
        setAttachState({ kind: 'race-lost' });
        setShowRaceLost(true);
        return;
      }
      setAttachState({ kind: 'read-only' });
    } catch {
      setAttachState({ kind: 'read-only' });
    }
  }, [taskId]);

  const requestDetach = useCallback(async () => {
    const cid = connectionIdRef.current;
    if (!cid) return;
    try {
      await fetch(
        `${getApiBaseUrl()}/tasks/${encodeURIComponent(taskId)}/agent-console/detach`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ connection_id: cid }),
        },
      );
    } catch {
      /* swallow — detach is best-effort, server releases on WS close anyway */
    }
    setAttachState({ kind: 'read-only' });
  }, [taskId]);

  // -----------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------

  const isAttached = attachState.kind === 'attached';
  const canAttach =
    wsStatus === 'open' &&
    connectionIdRef.current !== null &&
    attachState.kind === 'read-only';

  return (
    <div className="flex flex-col h-full" data-testid="agent-console">
      {/* Header: status + attach button */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border">
        <div className="flex items-center gap-2">
          <Badge variant={isAttached ? 'destructive' : 'secondary'} className="text-xs">
            {wsStatus === 'connecting'
              ? t('tasks:agentConsole.connecting')
              : wsStatus === 'closed'
                ? t('tasks:agentConsole.connectionLost')
                : isAttached
                  ? t('tasks:agentConsole.attachedBadge')
                  : t('tasks:agentConsole.connected')}
          </Badge>
          {!isAttached && (
            <span className="text-xs text-muted-foreground">
              {t('tasks:agentConsole.headerHint')}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {isAttached ? (
            <Button
              variant="outline"
              size="sm"
              onClick={requestDetach}
              data-testid="agent-console-detach"
            >
              {t('tasks:agentConsole.detachButton')}
            </Button>
          ) : (
            <Button
              variant="default"
              size="sm"
              disabled={!canAttach}
              onClick={() => setShowAttachConfirm(true)}
              data-testid="agent-console-attach"
            >
              {t('tasks:agentConsole.attachButton')}
            </Button>
          )}
        </div>
      </div>

      {/* xterm canvas */}
      <div className="flex-1 min-h-0 bg-[#0B0B0F] p-2" ref={containerRef} />

      {/* Attach confirmation */}
      <AlertDialog open={showAttachConfirm} onOpenChange={setShowAttachConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('tasks:agentConsole.attachConfirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('tasks:agentConsole.attachConfirmBody')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>
              {t('tasks:agentConsole.attachConfirmCancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setShowAttachConfirm(false);
                void requestAttach();
              }}
              data-testid="agent-console-attach-confirm"
            >
              {t('tasks:agentConsole.attachConfirmConfirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 409 — another viewer holds attach */}
      <AlertDialog open={showRaceLost} onOpenChange={setShowRaceLost}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('tasks:agentConsole.raceLostTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('tasks:agentConsole.raceLostBody')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction
              onClick={() => {
                setShowRaceLost(false);
                setAttachState({ kind: 'read-only' });
              }}
            >
              {t('tasks:agentConsole.raceLostDismiss')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
