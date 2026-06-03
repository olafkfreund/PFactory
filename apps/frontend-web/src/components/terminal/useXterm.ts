import { useEffect, useRef, useCallback } from 'react';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import { SerializeAddon } from '@xterm/addon-serialize';
import { terminalBufferManager } from '../../lib/terminal-buffer-manager';

interface UseXtermOptions {
  terminalId: string;
  onCommandEnter?: (command: string) => void;
  onResize?: (cols: number, rows: number) => void;
}

export function useXterm({ terminalId, onCommandEnter, onResize }: UseXtermOptions) {
  const terminalRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<XTerm | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const serializeAddonRef = useRef<SerializeAddon | null>(null);
  const commandBufferRef = useRef<string>('');
  const isDisposedRef = useRef<boolean>(false);

  // Initialize xterm.js UI
  useEffect(() => {
    if (!terminalRef.current || xtermRef.current) return;

    const xterm = new XTerm({
      cursorBlink: true,
      cursorStyle: 'block',
      fontSize: 18,
      fontFamily: 'var(--font-mono), "JetBrains Mono", Menlo, Monaco, "Courier New", monospace',
      lineHeight: 2,
      letterSpacing: 0,
      theme: {
        background: '#0B0B0F',
        foreground: '#E8E6E3',
        cursor: '#D6D876',
        cursorAccent: '#0B0B0F',
        selectionBackground: '#D6D87640',
        selectionForeground: '#E8E6E3',
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
      scrollback: 10000,
    });

    const fitAddon = new FitAddon();
    const webLinksAddon = new WebLinksAddon();
    const serializeAddon = new SerializeAddon();

    xterm.loadAddon(fitAddon);
    xterm.loadAddon(webLinksAddon);
    xterm.loadAddon(serializeAddon);

    xterm.open(terminalRef.current);

    // Custom keyboard event handler for terminal shortcuts
    //
    // IMPORTANT: This handler ensures ALL printable ASCII characters (32-126) pass through
    // correctly to the terminal. Only specific application-level shortcuts are intercepted.
    //
    // Return value behavior:
    // - true: Let xterm.js handle the key (normal terminal behavior)
    // - false: Prevent xterm.js handling, bubble up to app (for shortcuts)
    //
    // Intercepted keys (return false - app handles):
    // - Cmd/Ctrl+1-9: Project tab switching
    // - Cmd/Ctrl+Tab: Tab navigation
    // - Cmd/Ctrl+T: New terminal
    // - Cmd/Ctrl+W: Close terminal
    //
    // Keys that pass through to xterm (return true - terminal handles):
    // - All printable characters: space (32) through tilde (126)
    //   Letters: a-z, A-Z
    //   Numbers: 0-9 (without modifiers)
    //   Symbols: !"#$%&'()*+,-./:;<=>?@[\]^_`{|}~
    // - Arrow keys, Enter, Backspace (without Cmd/Ctrl), Delete, etc.
    // - Terminal control sequences: Ctrl+C, Ctrl+D, Ctrl+Z, etc.
    //
    // Special handling (custom behavior):
    // - SHIFT+Enter: Multi-line input (sends ESC+newline)
    // - Cmd/Ctrl+Backspace: Delete line (sends Ctrl+U)
    xterm.attachCustomKeyEventHandler((event) => {
      // Only process keydown events to avoid redundant processing
      // keyup events should be ignored as they don't affect input
      if (event.type !== 'keydown') {
        return true;
      }

      const isMod = event.metaKey || event.ctrlKey;

      // Handle SHIFT+Enter for multi-line input (send newline character)
      // This matches VS Code/Cursor behavior for multi-line input in Claude Code
      if (event.key === 'Enter' && event.shiftKey && !isMod) {
        // Send ESC + newline - same as OPTION+Enter which works for multi-line
        xterm.input('\x1b\n');
        return false; // Prevent default xterm handling
      }

      // Handle CMD+Backspace (Mac) or Ctrl+Backspace (Windows/Linux) to delete line
      // Sends Ctrl+U which is the terminal standard for "kill line backward"
      if (event.key === 'Backspace' && isMod) {
        xterm.input('\x15'); // Ctrl+U
        return false;
      }

      // Let Cmd/Ctrl + number keys pass through for project tab switching
      if (isMod && event.key >= '1' && event.key <= '9') {
        return false; // Don't handle in xterm, let it bubble up
      }

      // Let Cmd/Ctrl + Tab pass through for tab navigation
      if (isMod && event.key === 'Tab') {
        return false;
      }

      // Let Cmd/Ctrl + T pass through for new terminal shortcut
      // Let Cmd/Ctrl + W pass through for close terminal shortcut
      if (isMod && (event.key === 't' || event.key === 'T' || event.key === 'w' || event.key === 'W')) {
        return false;
      }

      // Handle all other keys in xterm
      // This includes ALL printable characters (ASCII 32-126) and terminal control sequences
      return true;
    });

    setTimeout(() => {
      fitAddon.fit();
    }, 50);

    xtermRef.current = xterm;
    fitAddonRef.current = fitAddon;
    serializeAddonRef.current = serializeAddon;

    // Replay buffered output if this is a remount or restored session
    // This now includes ANSI codes for proper formatting/colors/prompt
    const bufferedOutput = terminalBufferManager.get(terminalId);
    if (bufferedOutput && bufferedOutput.length > 0) {
      xterm.write(bufferedOutput);
      // Clear buffer after replay to avoid duplicate output
      terminalBufferManager.clear(terminalId);
    }

    // Handle terminal input - ALL data is forwarded to the backend
    // This includes printable characters (ASCII 32-126), control sequences, etc.
    xterm.onData((data) => {
      // Forward ALL input data to the terminal backend without filtering
      window.API.sendTerminalInput(terminalId, data);

      // Track printable characters for command auto-naming feature
      // This does NOT affect what gets sent to the terminal - it's just for UI
      if (data === '\r' || data === '\n') {
        // Enter pressed - trigger command callback
        const command = commandBufferRef.current;
        commandBufferRef.current = '';
        if (onCommandEnter) {
          onCommandEnter(command);
        }
      } else if (data === '\x7f' || data === '\b') {
        // Backspace or DEL - remove last character from buffer
        commandBufferRef.current = commandBufferRef.current.slice(0, -1);
      } else if (data === '\x03') {
        // Ctrl+C - clear command buffer
        commandBufferRef.current = '';
      } else if (data.charCodeAt(0) >= 32 && data.charCodeAt(0) < 127) {
        // Printable ASCII characters (space=32 through tilde=126)
        // Add to command buffer for auto-naming feature
        commandBufferRef.current += data;
      }
      // Note: Multi-byte UTF-8 characters and other control codes are sent to
      // the terminal but not tracked in the command buffer
    });

    // Handle resize
    xterm.onResize(({ cols, rows }) => {
      if (onResize) {
        onResize(cols, rows);
      }
    });

    return () => {
      // Cleanup handled by parent component
    };
  }, [terminalId, onCommandEnter, onResize]);

  // Handle resize on container resize and window resize
  useEffect(() => {
    const handleResize = () => {
      if (fitAddonRef.current && xtermRef.current) {
        fitAddonRef.current.fit();
      }
    };

    // ResizeObserver for container dimension changes
    const container = terminalRef.current?.parentElement;
    let resizeObserver: ResizeObserver | undefined;
    if (container) {
      resizeObserver = new ResizeObserver(handleResize);
      resizeObserver.observe(container);
    }

    // Window resize listener as fallback (e.g. when container becomes visible after display:none)
    window.addEventListener('resize', handleResize);

    return () => {
      resizeObserver?.disconnect();
      window.removeEventListener('resize', handleResize);
    };
  }, []);

  const fit = useCallback(() => {
    if (fitAddonRef.current && xtermRef.current) {
      fitAddonRef.current.fit();
    }
  }, []);

  const write = useCallback((data: string) => {
    if (xtermRef.current) {
      xtermRef.current.write(data);
    }
  }, []);

  const writeln = useCallback((data: string) => {
    if (xtermRef.current) {
      xtermRef.current.writeln(data);
    }
  }, []);

  const focus = useCallback(() => {
    if (xtermRef.current) {
      xtermRef.current.focus();
    }
  }, [terminalId]);

  /**
   * Serialize the terminal buffer before disposal.
   * This preserves ANSI escape codes for colors, formatting, and the prompt.
   */
  const serializeBuffer = useCallback(() => {
    if (xtermRef.current && serializeAddonRef.current) {
      try {
        const serialized = serializeAddonRef.current.serialize();
        if (serialized && serialized.length > 0) {
          terminalBufferManager.set(terminalId, serialized);
        }
      } catch (error) {
        console.error('[useXterm] Failed to serialize terminal buffer:', error);
      }
    }
  }, [terminalId]);

  const dispose = useCallback(() => {
    // Guard against double dispose (can happen in React StrictMode or rapid unmount)
    if (isDisposedRef.current) return;
    isDisposedRef.current = true;

    // Serialize buffer before disposing to preserve ANSI formatting
    serializeBuffer();

    if (xtermRef.current) {
      xtermRef.current.dispose();
      xtermRef.current = null;
    }
    if (serializeAddonRef.current) {
      serializeAddonRef.current.dispose();
      serializeAddonRef.current = null;
    }
    fitAddonRef.current = null;
  }, [serializeBuffer]);

  return {
    terminalRef,
    xtermRef,
    fitAddonRef,
    fit,
    write,
    writeln,
    focus,
    dispose,
    cols: xtermRef.current?.cols || 80,
    rows: xtermRef.current?.rows || 24,
  };
}
