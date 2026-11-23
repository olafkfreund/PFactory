/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { useEffect, useState } from 'react';
import { TerminalGrid } from './TerminalGrid';
import { useTerminalStore } from '../stores/terminal-store';

const xterms = vi.hoisted(() => new Map<string, { disposed: boolean }>());
vi.mock('./SortableTerminal', () => ({
  SortableTerminal: ({ id }: { id: string }) => {
    const [instance] = useState(() => ({ disposed: false }));
    useEffect(() => {
      xterms.set(id, instance);
      return () => { instance.disposed = true; };
    }, [id, instance]);
    return <div data-testid={`xterm-${id}`} />;
  },
}));
vi.mock('./FileExplorerPanel', () => ({ FileExplorerPanel: () => null }));

beforeEach(() => {
  xterms.clear();
  useTerminalStore.setState({
    terminals: [
      { id: 'alpha', title: 'Alpha', status: 'running', cwd: '/src/alpha', projectPath: '/src/alpha', createdAt: new Date(), isClaudeMode: false },
      { id: 'beta', title: 'Beta', status: 'running', cwd: '/src/beta', projectPath: '/src/beta', createdAt: new Date(), isClaudeMode: false },
    ],
    activeTerminalId: 'alpha',
  });
  (window as unknown as { API: unknown }).API = new Proxy({}, {
    get: () => () => Promise.resolve({ success: true, data: [] }),
  });
});

describe('TerminalGrid xterm lifetime', () => {
  it('keeps each instance alive while switching projects and hiding the grid globally', () => {
    const view = render(<TerminalGrid projectPath="/src/alpha" isActive />);
    const alpha = xterms.get('alpha');
    const beta = xterms.get('beta');
    expect(alpha).toBeDefined();
    expect(beta).toBeDefined();
    expect(screen.getByTestId('xterm-beta').parentElement).toHaveClass('hidden');
    view.rerender(<TerminalGrid projectPath="/src/beta" isActive />);
    expect(xterms.get('alpha')).toBe(alpha);
    expect(xterms.get('beta')).toBe(beta);
    expect(alpha?.disposed).toBe(false);
    expect(screen.getByTestId('xterm-alpha').parentElement).toHaveClass('hidden');
    view.rerender(<TerminalGrid isActive={false} />);
    expect(xterms.get('alpha')).toBe(alpha);
    expect(xterms.get('beta')).toBe(beta);
    expect(alpha?.disposed).toBe(false);
    expect(beta?.disposed).toBe(false);
  });
});
