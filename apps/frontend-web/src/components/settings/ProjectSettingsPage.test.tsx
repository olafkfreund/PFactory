/** @vitest-environment jsdom */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { useEffect } from 'react';
import { ProjectSettingsPage } from './AppSettings';
import { useProjectStore } from '../../stores/project-store';
import type { UseProjectSettingsReturn } from '../project-settings/hooks/useProjectSettings';

const save = vi.hoisted(() => vi.fn());
vi.mock('./ProjectSettingsContent', () => ({
  ProjectSettingsContent: ({ onHookReady }: { onHookReady: (hook: UseProjectSettingsReturn) => void }) => {
    useEffect(() => { onHookReady({ handleSave: save } as unknown as UseProjectSettingsReturn); }, [onHookReady]);
    return null;
  },
}));

describe('Project Settings save', () => {
  it('disables the header while a write is pending and ignores a second click', async () => {
    let finish: () => void = () => undefined;
    save.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    useProjectStore.setState({ projects: [{ id: 'alpha', name: 'Alpha', path: '/alpha' }] as ReturnType<typeof useProjectStore.getState>['projects'] });
    render(<ProjectSettingsPage projectId="alpha" />);
    const button = await screen.findByRole('button', { name: 'Save Settings' });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(save).toHaveBeenCalledTimes(1);
    finish();
    await waitFor(() => expect(button).toBeEnabled());
  });
});
