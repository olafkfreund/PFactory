import { useEffect, useRef, useCallback } from 'react';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import { v4 as uuid } from 'uuid';

interface EmbeddedTerminalProps {
  /** Command to run after the terminal shell is ready */
  initialCommand?: string;
  /** Callback when the terminal process exits */
  onExit?: (exitCode: number) => void;
  /** Callback when a URL is detected in the terminal output */
  onUrlDetected?: (url: string) => void;
  /** Callback when an OAuth token (sk-ant-oat01-*) is detected in output */
  onTokenDetected?: (token: string) => void;
  /** Height of the terminal container (default: 350px) */
  height?: number;
}

/**
 * Strip ANSI escape sequences and control characters from terminal output,
 * keeping only printable text. This lets us reconstruct URLs and tokens
 * that span multiple wrapped lines in the terminal.
 */
function stripAnsi(text: string): string {
  return text
    // Remove all ANSI escape sequences (CSI, OSC, etc.)
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '')
    .replace(/\x1b\][^\x07]*\x07/g, '')
    .replace(/\x1b[()][A-Z0-9]/g, '')
    .replace(/\x1b[>=<]/g, '')
    // Remove carriage returns (terminal line wrapping)
    .replace(/\r/g, '')
    // Remove newlines to join wrapped lines
    .replace(/\n/g, '');
}

/**
 * A minimal self-contained terminal component for use in dialogs/onboarding.
 * Creates its own PTY session without needing the terminal store or a project.
 *
 * Features:
 * - Auto-sends initialCommand once the shell is ready (waits for first output)
 * - Clipboard paste via Ctrl+V / Cmd+V
 * - Clickable URLs via WebLinksAddon
 * - Extracts full URLs and OAuth tokens from output (even across line wraps)
 * - Auto-focuses on mount so keyboard input works immediately
 */
export function EmbeddedTerminal({ initialCommand, onExit, onUrlDetected, onTokenDetected, height = 350 }: EmbeddedTerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<XTerm | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const terminalIdRef = useRef<string>(uuid());
  const isCreatedRef = useRef(false);
  const initialCommandSentRef = useRef(false);
  const detectedUrlsRef = useRef<Set<string>>(new Set());
  const detectedTokenRef = useRef(false);
  const onUrlDetectedRef = useRef(onUrlDetected);
  const onTokenDetectedRef = useRef(onTokenDetected);
  onUrlDetectedRef.current = onUrlDetected;
  onTokenDetectedRef.current = onTokenDetected;

  // Accumulate raw output to reconstruct URLs/tokens that wrap across lines
  const outputBufferRef = useRef('');
  const extractTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Create PTY and xterm
  useEffect(() => {
    if (!containerRef.current || xtermRef.current) return;

    const terminalId = terminalIdRef.current;

    // Create xterm instance
    const xterm = new XTerm({
      cursorBlink: true,
      cursorStyle: 'block',
      fontSize: 11,
      fontFamily: '"Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", monospace',
      lineHeight: 1.2,
      theme: {
        background: '#0B0B0F',
        foreground: '#E8E6E3',
        cursor: '#D6D876',
        cursorAccent: '#0B0B0F',
        selectionBackground: '#D6D87640',
        black: '#1A1A1F',
        red: '#FF6B6B',
        green: '#87D687',
        yellow: '#D6D876',
        blue: '#6BB3FF',
        magenta: '#C792EA',
        cyan: '#89DDFF',
        white: '#E8E6E3',
        brightBlack: '#4A4A50',
        brightRed: '#FF8A8A',
        brightGreen: '#A5E6A5',
        brightYellow: '#E8E87A',
        brightBlue: '#8AC4FF',
        brightMagenta: '#DEB3FF',
        brightCyan: '#A6E8FF',
        brightWhite: '#FFFFFF',
      },
      allowProposedApi: true,
      scrollback: 5000,
    });

    const fitAddon = new FitAddon();
    xterm.loadAddon(fitAddon);

    // WebLinksAddon makes URLs clickable — opens in a new browser tab
    const webLinksAddon = new WebLinksAddon((_event, uri) => {
      window.open(uri, '_blank', 'noopener,noreferrer');
    });
    xterm.loadAddon(webLinksAddon);

    xterm.open(containerRef.current);

    setTimeout(() => {
      fitAddon.fit();
      xterm.focus();
    }, 50);

    xtermRef.current = xterm;
    fitAddonRef.current = fitAddon;

    // Handle Ctrl+V/Cmd+V for clipboard paste, Ctrl+C/Cmd+C for copy
    xterm.attachCustomKeyEventHandler((event) => {
      const isMod = event.ctrlKey || event.metaKey;

      // Paste: Ctrl+V / Cmd+V
      if (isMod && event.key === 'v' && event.type === 'keydown') {
        navigator.clipboard.readText().then((text) => {
          if (text) {
            window.API.sendTerminalInput(terminalId, text);
          }
        }).catch(() => {
          // Clipboard read failed (permissions)
        });
        return false; // Prevent xterm default handling
      }

      // Copy: Ctrl+C / Cmd+C when text is selected
      if (isMod && event.key === 'c' && event.type === 'keydown') {
        if (xterm.hasSelection()) {
          navigator.clipboard.writeText(xterm.getSelection());
          return false; // Prevent sending SIGINT
        }
        // No selection — let Ctrl+C pass through as SIGINT
      }

      return true;
    });

    // Forward keyboard input to the PTY
    xterm.onData((data) => {
      window.API.sendTerminalInput(terminalId, data);
    });

    /**
     * Extract URLs and OAuth tokens from the accumulated output buffer.
     * Called after a debounce to ensure we have complete data
     * even when it spans multiple output chunks (line wrapping).
     */
    const extractFromBuffer = () => {
      const clean = stripAnsi(outputBufferRef.current);

      // Extract URLs
      const urlRegex = /https?:\/\/[^\s"'<>)\]]+/g;
      const urlMatches = clean.match(urlRegex);
      if (urlMatches && onUrlDetectedRef.current) {
        for (const url of urlMatches) {
          if (!detectedUrlsRef.current.has(url)) {
            detectedUrlsRef.current.add(url);
            onUrlDetectedRef.current(url);
          }
        }
      }

      // Extract OAuth tokens (sk-ant-oat01-...)
      // Token is base64url chars: letters, digits, hyphens, underscores
      if (!detectedTokenRef.current && onTokenDetectedRef.current) {
        const tokenRegex = /sk-ant-oat01-[A-Za-z0-9_-]{20,}/g;
        const tokenMatches = clean.match(tokenRegex);
        if (tokenMatches) {
          detectedTokenRef.current = true;
          // Take the longest match (the full token)
          const token = tokenMatches.reduce((a, b) => a.length >= b.length ? a : b);
          onTokenDetectedRef.current(token);
        }
      }
    };

    // Listen for output from the PTY
    const cleanupOutput = window.API.onTerminalOutput((id, data) => {
      if (id === terminalId && xtermRef.current) {
        xtermRef.current.write(data);

        // Accumulate output for URL/token reconstruction
        outputBufferRef.current += data;

        // Debounce extraction — wait for all chunks of wrapped text
        if (extractTimerRef.current) {
          clearTimeout(extractTimerRef.current);
        }
        extractTimerRef.current = setTimeout(extractFromBuffer, 500);

        // Send initial command once we receive the first output (shell prompt)
        if (initialCommand && !initialCommandSentRef.current && isCreatedRef.current) {
          initialCommandSentRef.current = true;
          setTimeout(() => {
            window.API.sendTerminalInput(terminalId, initialCommand + '\r');
          }, 150);
        }
      }
    });

    // Listen for exit
    const cleanupExit = window.API.onTerminalExit((id, exitCode) => {
      if (id === terminalId) {
        onExit?.(exitCode);
      }
    });

    // Create the PTY session on the backend
    window.API.createTerminal({
      id: terminalId,
      cols: xterm.cols || 80,
      rows: xterm.rows || 24,
    }).then((result) => {
      if (result.success) {
        isCreatedRef.current = true;
      }
    });

    // Handle container resize
    const resizeObserver = new ResizeObserver(() => {
      if (fitAddonRef.current && xtermRef.current) {
        fitAddonRef.current.fit();
        if (isCreatedRef.current) {
          window.API.resizeTerminal(
            terminalId,
            xtermRef.current.cols,
            xtermRef.current.rows
          );
        }
      }
    });
    if (containerRef.current.parentElement) {
      resizeObserver.observe(containerRef.current.parentElement);
    }

    return () => {
      cleanupOutput();
      cleanupExit();
      resizeObserver.disconnect();
      if (extractTimerRef.current) {
        clearTimeout(extractTimerRef.current);
      }
      window.API.destroyTerminal(terminalId);
      if (xtermRef.current) {
        xtermRef.current.dispose();
        xtermRef.current = null;
      }
      fitAddonRef.current = null;
    };
  }, []); // Only run once on mount

  // Click handler to re-focus xterm (important inside dialog focus traps)
  const handleFocus = useCallback(() => {
    if (fitAddonRef.current && xtermRef.current) {
      fitAddonRef.current.fit();
      xtermRef.current.focus();
    }
  }, []);

  return (
    <div
      className="rounded-lg overflow-hidden border border-border"
      style={{ height: `${height}px` }}
      onClick={handleFocus}
    >
      <div ref={containerRef} style={{ height: '100%', width: '100%' }} />
    </div>
  );
}
