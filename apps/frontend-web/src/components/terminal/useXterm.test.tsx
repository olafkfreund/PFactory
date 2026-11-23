/** @vitest-environment jsdom */
import { describe, expect, it, vi } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import { useXterm } from './useXterm';

const terms = vi.hoisted(() => [] as Array<{ options: { theme: unknown }; open: ReturnType<typeof vi.fn> }>);
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    options: { theme: unknown };
    open = vi.fn();
    loadAddon = vi.fn();
    attachCustomKeyEventHandler = vi.fn();
    onData = vi.fn();
    onResize = vi.fn();
    constructor(options: { theme: unknown }) {
      this.options = options;
      terms.push(this);
    }
  },
}));
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }));
vi.mock('@xterm/addon-web-links', () => ({ WebLinksAddon: class {} }));
vi.mock('@xterm/addon-serialize', () => ({ SerializeAddon: class {} }));

function Harness() {
  const { terminalRef } = useXterm({ terminalId: 'one' });
  return <div ref={terminalRef} />;
}

describe('kept terminal theme', () => {
  it('updates the existing xterm palette after a shell theme switch', async () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    terms.length = 0;
    document.documentElement.classList.remove('dark');
    render(<Harness />);
    const term = terms[0];
    expect(term).toBeDefined();
    const light = term.options.theme;
    act(() => { document.documentElement.classList.add('dark'); });
    await waitFor(() => expect(term.options.theme).not.toEqual(light));
    expect(terms).toHaveLength(1);
  });
});
