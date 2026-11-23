/**
 * @vitest-environment jsdom
 *
 * Tests for <AddProjectModal> (#1116 B10) and api-adapter version lookup (#1116 B4).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { AddProjectModal } from '../AddProjectModal';
import { initWebAPI } from '../../lib/api-adapter';

describe('<AddProjectModal>', () => {
  const discoverProjects = vi.fn();

  beforeEach(() => {
    discoverProjects.mockReset();
    discoverProjects.mockResolvedValue({ success: true, data: [] });
    (window as unknown as { API: Record<string, unknown> }).API = {
      discoverProjects,
    };
  });

  it('opens in clone mode by default and resets to clone on reopen after switching to Local without auto-scanning', () => {
    const onOpenChange = vi.fn();
    const { rerender } = render(
      <AddProjectModal open={true} onOpenChange={onOpenChange} />,
    );

    // Default mode is clone; no local discovery ran on open
    expect(screen.getByLabelText(/Git repository URL/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Clone a Git repository into the portal workspace\./i),
    ).toBeInTheDocument();
    expect(discoverProjects).not.toHaveBeenCalled();

    // Switch to Local folder mode
    fireEvent.click(screen.getByRole('button', { name: /Local folder/i }));
    expect(
      screen.getByText(/Select a project from your projects folder or enter a custom path\./i),
    ).toBeInTheDocument();
    expect(discoverProjects).not.toHaveBeenCalled();

    // Close and reopen the modal: resets to clone mode with no discovery call
    rerender(<AddProjectModal open={false} onOpenChange={onOpenChange} />);
    rerender(<AddProjectModal open={true} onOpenChange={onOpenChange} />);

    expect(screen.getByLabelText(/Git repository URL/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Clone a Git repository into the portal workspace\./i),
    ).toBeInTheDocument();
    expect(discoverProjects).not.toHaveBeenCalled();
  });

  it('runs discovery when pressing Enter on the projects folder input in Local mode', async () => {
    const onOpenChange = vi.fn();
    render(<AddProjectModal open={true} onOpenChange={onOpenChange} />);

    fireEvent.click(screen.getByRole('button', { name: /Local folder/i }));
    const folderInput = screen.getByLabelText(/Projects Folder/i);
    fireEvent.keyDown(folderInput, { key: 'Enter', code: 'Enter' });

    expect(discoverProjects).toHaveBeenCalledWith('/home', 2);
  });
});

describe('api-adapter shared /api/health version lookup (#1116 B4)', () => {
  beforeEach(() => {
    initWebAPI();
  });

  it('returns "unknown" on failure and retries on the next call, then shares the result across getAppVersion and getDebugInfo', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');

    // 1. First call fails -> resolves to "unknown"
    fetchSpy.mockResolvedValueOnce({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      json: async () => ({}),
    } as Response);

    expect(await window.API.getAppVersion()).toBe('unknown');
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    // 2. Subsequent call retries /api/health and caches for both getAppVersion and getDebugInfo
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({ status: 'healthy', version: '3.1.4' }),
    } as Response);

    const [v1, debugInfo] = await Promise.all([
      window.API.getAppVersion(),
      window.API.getDebugInfo(),
    ]);

    expect(v1).toBe('3.1.4');
    expect(debugInfo.systemInfo.appVersion).toBe('3.1.4');
    expect(fetchSpy).toHaveBeenCalledTimes(2);

    fetchSpy.mockRestore();
  });
});
