/** @vitest-environment jsdom */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import '../shared/i18n';
import App from '../App';
import { useAuthStore } from '../stores/auth-store';
import { useProjectStore } from '../stores/project-store';
import { usePlanStore } from '../stores/plan-store';
import { useSettingsStore } from '../stores/settings-store';
import type { Project } from '../shared/types';

vi.mock('../hooks/useIpc', () => ({ useIpcListeners: () => undefined }));
vi.mock('../lib/api-client', () => ({
  apiRequest: vi.fn().mockResolvedValue({ success: true, data: { count: 2 } }),
  get: vi.fn().mockResolvedValue({ success: false }),
}));
vi.mock('../components/TerminalGrid', () => ({ TerminalGrid: () => null }));
vi.mock('../components/ChatLauncher', () => ({ ChatLauncher: () => null }));
vi.mock('../components/ui/toaster', () => ({ Toaster: () => null }));

const DEMO_PROJECT: Project = {
  id: 'id-alpha',
  name: 'Alpha',
  path: '/src/alpha',
  autoBuildPath: '.pfactory',
  settings: {
    model: 'claude-sonnet-4-5-20250929',
    memoryBackend: 'file',
    notifications: {
      onTaskComplete: true,
      onTaskFailed: true,
      onReviewNeeded: true,
      sound: false,
      emailEnabled: false,
    },
    graphitiMcpEnabled: false,
  },
  createdAt: new Date('2026-01-01'),
  updatedAt: new Date('2026-01-01'),
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  });
  useAuthStore.setState({ isAuthenticated: true, checkAuth: () => Promise.resolve(true) });
  useProjectStore.setState({
    projects: [],
    selectedProjectId: null,
    activeProjectId: null,
    openProjectIds: [],
    tabOrder: [],
  });
  usePlanStore.setState({
    sessions: [],
    currentSession: null,
    loading: false,
    sessionLoading: false,
    error: null,
    fetchFn: undefined,
  });
  const apiTarget: Record<string, unknown> = {
    getTabState: () => Promise.resolve({ success: true, data: {} }),
    saveTabState: () => Promise.resolve({ success: true }),
    getProjects: () => Promise.resolve({ success: true, data: [DEMO_PROJECT] }),
    getTasks: () => Promise.resolve({ success: true, data: [] }),
    getSettings: () => Promise.resolve({ success: true, data: { onboardingCompleted: true, theme: 'dark' } }),
    getAppVersion: () => Promise.resolve('0.9.2'),
    checkClaudeCodeVersion: () => Promise.resolve({ success: true, data: { installed: '1.0.0' } }),
    getAuthStatus: () => Promise.resolve({ success: true, data: { hasToken: true } }),
    checkGitStatus: () => Promise.resolve({ success: true, data: { isGitRepo: true, hasCommits: true } }),
    getProjectEnv: () => Promise.resolve({ success: true, data: { githubEnabled: true, gitProvider: 'github' } }),
  };
  (window as unknown as { API: unknown }).API = new Proxy(apiTarget, {
    get: (t, k: string) => t[k] ?? (() => Promise.resolve({ success: false })),
  });
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        tasks: [],
        count: 0,
        sessions: [
          {
            session_id: 'plan-1',
            title: 'Approved Plan',
            status: 'approved',
            board_state: 'done',
            target_kind: 'software',
            plan_type: 'feature',
            children: 3,
            gates_passed: true,
            created_at: '2026-06-01T10:00:00Z',
          },
        ],
      }),
    })
  );
});

describe('PFactory adapted AppShell', () => {
  it('renders AppShell with portal="plan", PipelineStrip (Backlog, Review, Approved), first sidebar item "Plans" (B9), ProjectSwitcher, primary + Task action, and routes to Project Settings and Preferences', async () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/p/alpha']}>
        <App />
      </MemoryRouter>
    );

    const shell = await waitFor(() => {
      const el = container.querySelector('.f-shell');
      expect(el).toHaveAttribute('data-portal', 'plan');
      return el!;
    });
    expect(shell).toBeInTheDocument();

    // First sidebar item is labelled "Plans", not "Tests" (bug B9)
    const navButtons = within(container.querySelector('.f-side__nav')!).getAllByRole('button');
    expect(navButtons[0]).toHaveTextContent('Plans');
    expect(screen.queryByRole('button', { name: 'Tests' })).toBeNull();

    // PipelineStrip rendered in the shell above the page with PFactory's three buckets
    const strip = await screen.findByRole('toolbar', { name: /Pipeline stages/i });
    expect(strip).toBeInTheDocument();
    expect(within(strip).getByText('Backlog')).toBeInTheDocument();
    expect(within(strip).getByText('Review')).toBeInTheDocument();
    expect(within(strip).getByText('Approved')).toBeInTheDocument();

    // Primary action "+ Task"
    expect(screen.getByRole('button', { name: '+ Task' })).toBeEnabled();

    // Project Settings pinned in sidebar navigates to /p/alpha/settings with General, Git, Memory, MCP, Plan sources tabs
    fireEvent.click(screen.getByRole('button', { name: 'Project Settings' }));
    expect(await screen.findByTestId('project-settings-page')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'General' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Git' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Memory' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'MCP' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Plan sources' })).toBeInTheDocument();
    // Strip is hidden on settings pages
    expect(screen.queryByRole('toolbar', { name: /Pipeline stages/i })).toBeNull();

    // User menu Preferences navigates to /preferences
    fireEvent.click(screen.getByRole('button', { name: 'Preferences' }));
    expect(await screen.findByTestId('preferences-page')).toBeInTheDocument();
    expect(screen.queryByRole('toolbar', { name: /Pipeline stages/i })).toBeNull();
  });

  it('filters only failed Review plans through the shell strip, clears both filters, and keeps the strip across views', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          tasks: [],
          sessions: [
            {
              session_id: 's-clean',
              title: 'Clean Review Plan',
              status: 'processed',
              board_state: 'human_review',
              target_kind: 'software',
              plan_type: 'feature',
              children: 2,
              gates_passed: true,
              created_at: '2026-06-01T10:00:00Z',
            },
            {
              session_id: 's-failed',
              title: 'Blocked Review Plan',
              status: 'processed',
              board_state: 'human_review',
              target_kind: 'software',
              plan_type: 'bugfix',
              children: 1,
              gates_passed: false,
              created_at: '2026-06-01T10:05:00Z',
            },
          ],
        }),
      })
    );

    const { container } = render(
      <MemoryRouter initialEntries={['/p/alpha']}>
        <App />
      </MemoryRouter>
    );

    await screen.findByText('Unassigned · Clean Review Plan');
    expect(container.querySelectorAll('.f-column .f-card')).toHaveLength(2);

    fireEvent.click(await screen.findByRole('button', { name: /Filter Review failures/ }));
    expect(container.querySelectorAll('.f-column .f-card')).toHaveLength(1);
    expect(screen.queryByText('Unassigned · Clean Review Plan')).toBeNull();
    expect(screen.getByText('Unassigned · Blocked Review Plan')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Clear filter/ }));
    expect(container.querySelectorAll('.f-column .f-card')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Unassigned · Blocked Review Plan' }));
    expect(screen.getByTestId('planning-view')).toBeInTheDocument();
    expect(screen.getByTestId('plan-board-view')).not.toHaveClass('hidden');
    fireEvent.click(screen.getByRole('button', { name: /Filter Review failures/ }));
    expect(screen.getByTestId('plan-board-view')).not.toHaveClass('hidden');
    expect(container.querySelectorAll('.f-column .f-card')).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: 'Files' }));
    expect(screen.getByRole('toolbar', { name: /Pipeline stages/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Filter Review failures/ }));
    expect(screen.getByTestId('plan-board-view')).not.toHaveClass('hidden');
    expect(container.querySelectorAll('.f-column .f-card')).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: 'Plans' }));
    fireEvent.click(screen.getByRole('button', { name: /Filter Review failures/ }));
    expect(screen.getByTestId('plan-board-view')).not.toHaveClass('hidden');
  });

  it('deep-links to /p/:slug/settings/plan-sources with Registry, Templates and Providers', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (String(url).includes('/meta/registry')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              entries: [
                {
                  id: 'aws-adapter',
                  title: 'AWS Adapter',
                  kind: 'adapter',
                  version: '1.0.0',
                  enabled: true,
                  capabilities: ['compute'],
                },
              ],
            }),
          });
        }
        if (String(url).includes('/meta/templates')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              templates: [{ name: 'standard', title: 'Standard Plan', tags: ['core'], policy: {} }],
            }),
          });
        }
        if (String(url).includes('/meta/providers')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ providers: ['claude'] }),
          });
        }
        if (String(url).includes('/meta/adapters')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ infra_adapters: ['aws'], knowledge_connectors: ['confluence'] }),
          });
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({ tasks: [], count: 0, sessions: [] }),
        });
      })
    );

    render(
      <MemoryRouter initialEntries={['/p/alpha/settings/plan-sources']}>
        <App />
      </MemoryRouter>
    );

    expect(await screen.findByTestId('project-settings-page')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Plan sources' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByTestId('registry-panel')).toBeInTheDocument();
    expect(screen.getByText('AWS Adapter')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Templates' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Providers' })).toBeInTheDocument();
  });

  it('reverts an unsaved appearance preview on route change', async () => {
    render(
      <MemoryRouter initialEntries={['/preferences']}>
        <App />
      </MemoryRouter>
    );
    await screen.findByTestId('preferences-page');
    fireEvent.click(screen.getByRole('button', { name: /Appearance/i }));
    fireEvent.click(within(screen.getByTestId('preferences-page')).getByRole('button', { name: /light/i }));
    expect(useSettingsStore.getState().settings.theme).toBe('light');
    fireEvent.click(screen.getByRole('button', { name: 'Plans' }));
    await waitFor(() => expect(useSettingsStore.getState().settings.theme).toBe('dark'));
  });
});
