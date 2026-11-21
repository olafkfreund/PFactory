/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router';
import { useEffect } from 'react';
import type { Project } from '../shared/types';

const mounts = vi.hoisted(() => ({ count: 0, unmounts: 0 }));
vi.mock('../components/Sidebar', () => ({ Sidebar: ({ projectId }: { projectId: string | null }) => <div data-testid="sidebar-project">{projectId ?? 'none'}</div> }));
vi.mock('../components/ProjectTabBar', () => ({ ProjectTabBar: ({ activeProjectId }: { activeProjectId: string | null }) => <div data-testid="tab-project">{activeProjectId ?? 'none'}</div> }));
vi.mock('../components/TerminalGrid', () => ({
  TerminalGrid: ({ projectPath }: { projectPath: string }) => {
    useEffect(() => {
      mounts.count++;
      return () => { mounts.unmounts++; };
    }, []);
    return <div data-testid="terminal">{projectPath}</div>;
  },
}));
vi.mock('../components/planning/PlanningView', async () => {
  const { useTaskStore } = await import('../stores/task-store');
  return { PlanningView: () => <div data-testid="planning">{useTaskStore.getState().tasks.map((task) => task.title).join(',')}</div> };
});
vi.mock('../components/pfactory/PFactoryPortal', () => ({ PFactoryPortal: () => <div data-testid="queue" /> }));
vi.mock('../components/Worktrees', () => ({ Worktrees: () => null }));
vi.mock('../components/context/Context', () => ({ Context: () => null }));
vi.mock('../components/GitHubIssues', () => ({ GitHubIssues: () => null }));
vi.mock('../components/github-prs/GitHubPRs', () => ({ GitHubPRs: () => null }));
vi.mock('../components/changelog/Changelog', () => ({ Changelog: () => null }));
vi.mock('../components/Insights', () => ({ Insights: ({ projectId }: { projectId: string }) => <div data-testid="insights">{projectId}</div> }));
vi.mock('../components/AgentTools', () => ({ AgentTools: () => null }));
vi.mock('../components/SkillsPage', () => ({ SkillsPage: () => null }));
vi.mock('../components/ChatLauncher', () => ({ ChatLauncher: () => null }));
vi.mock('../components/settings/sections/CloudAssessmentPage', () => ({ CloudAssessmentPage: () => null }));
vi.mock('../components/settings/sections/VisualReportsPage', () => ({ VisualReportsPage: () => null }));
vi.mock('../components/WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="home" /> }));
vi.mock('../components/AddProjectModal', () => ({ AddProjectModal: () => null }));
vi.mock('../components/settings', () => ({ AppSettingsDialog: ({ projectId }: { projectId: string | null }) => <div data-testid="settings-project">{projectId ?? 'none'}</div> }));
vi.mock('../components/TaskCreationWizard', () => ({ TaskCreationWizard: ({ projectId }: { projectId: string }) => <div data-testid="wizard-project">{projectId}</div> }));
vi.mock('../components/CloudCheckDialog', () => ({ CloudCheckDialog: () => null }));
vi.mock('../components/task-detail', () => ({ TaskDetailModal: ({ projectPath }: { projectPath?: string }) => <div data-testid="task-modal-project">{projectPath ?? 'none'}</div> }));
vi.mock('../components/onboarding', () => ({ OnboardingWizard: () => null }));
vi.mock('../components/ProjectSwitchLoadingModal', () => ({ ProjectSwitchLoadingModal: () => null }));
vi.mock('../components/CommandPalette', () => ({ CommandPalette: () => null }));
vi.mock('../components/ui/toaster', () => ({ Toaster: () => null }));
vi.mock('../pages/EditorPage', () => ({ EditorPage: () => null }));
vi.mock('../pages/LoginPage', () => ({ LoginPage: () => <div data-testid="login" /> }));
vi.mock('../components/task-detail/AgentConsole', () => ({ AgentConsole: ({ taskId }: { taskId: string }) => <div data-testid="console">{taskId}</div> }));
vi.mock('../hooks/useIpc', () => ({ useIpcListeners: () => undefined }));
vi.mock('../lib/api-client', () => ({ apiRequest: vi.fn().mockResolvedValue({ success: false }) }));
vi.mock('../stores/auth-store', async () => {
  const { create } = await import('zustand');
  return { useAuthStore: create(() => ({ isAuthenticated: true, checkAuth: () => Promise.resolve(true) })) };
});

import '../shared/i18n';
import App from '../App';
import { useAuthStore } from '../stores/auth-store';
import { useProjectStore } from '../stores/project-store';
import { useTaskStore } from '../stores/task-store';

const project = (id: string, name: string): Project => ({
  id, name, path: `/src/${id}`, autoBuildPath: '.a', createdAt: new Date('2026-01-01'), updatedAt: new Date('2026-01-01'),
}) as Project;
const alpha = project('id-alpha', 'Alpha');
const beta = project('id-beta', 'Beta');
const tasks = {
  'id-alpha': [{ id: 'task-alpha', title: 'alpha-task', projectId: 'id-alpha' }],
  'id-beta': [{ id: 'task-beta', title: 'beta-task', projectId: 'id-beta' }],
};

function api(tab: Record<string, unknown> = {}) {
  (window as unknown as { API: unknown }).API = new Proxy({
    getTabState: () => Promise.resolve({ success: true, data: tab }),
    getProjects: () => Promise.resolve({ success: true, data: [alpha, beta] }),
    saveTabState: () => Promise.resolve({ success: true }),
    getTasks: (id: keyof typeof tasks) => Promise.resolve({ success: true, data: tasks[id] }),
    getSettings: () => Promise.resolve({ success: true, data: { onboardingCompleted: true, theme: 'dark' } }),
  } as Record<string, unknown>, {
    get: (target, key: string) => target[key] ?? (() => Promise.resolve({ success: false })),
  });
}

let navigate: ReturnType<typeof useNavigate>;
let pathname = '';
let locationState = '';
function LocationProbe() {
  navigate = useNavigate();
  const location = useLocation();
  pathname = location.pathname;
  locationState = location.pathname + location.search + location.hash;
  return null;
}
const renderAt = (path: string) => render(<MemoryRouter initialEntries={[path]}><LocationProbe /><App /></MemoryRouter>);

beforeEach(() => {
  useAuthStore.setState({ isAuthenticated: true });
  useProjectStore.setState({ projects: [], selectedProjectId: null, activeProjectId: null, openProjectIds: [], tabOrder: [], isSwitchingProject: false });
  useTaskStore.setState({ tasks: [] });
  localStorage.clear();
  sessionStorage.clear();
  mounts.count = 0;
  mounts.unmounts = 0;
  window.matchMedia = window.matchMedia || ((() => ({ matches: false, addEventListener() {}, removeEventListener() {} })) as never);
  api();
});

describe('project routing', () => {
  it('lets /p/beta override the saved active and last-selected alpha, retaining its open tab', async () => {
    localStorage.setItem('lastSelectedProjectId', 'id-alpha');
    api({ openProjectIds: ['id-alpha'], activeProjectId: 'id-alpha', tabOrder: ['id-alpha'] });
    renderAt('/p/beta');
    await waitFor(() => expect(screen.getByTestId('planning')).toHaveTextContent('beta-task'));
    expect(useProjectStore.getState().activeProjectId).toBe('id-beta');
    expect(useProjectStore.getState().selectedProjectId).toBe('id-beta');
    expect(useProjectStore.getState().openProjectIds).toContain('id-alpha');
  });

  it('resolves an id and rewrites it to the canonical slug while keeping the view', async () => {
    renderAt('/p/id-beta/insights');
    await waitFor(() => expect(pathname).toBe('/p/beta/insights'));
    expect(screen.getByTestId('insights')).toHaveTextContent('id-beta');
  });

  it('shows a not-found notice and home link without mounting a project view', async () => {
    renderAt('/p/no-such-project');
    expect(await screen.findByText('Project not found: no-such-project')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to my projects' })).toHaveAttribute('href', '/');
    expect(screen.queryByTestId('planning')).toBeNull();
    expect(screen.getByTestId('sidebar-project')).toHaveTextContent('none');
    expect(screen.getByTestId('settings-project')).toHaveTextContent('none');
    expect(screen.queryByTestId('wizard-project')).toBeNull();
    expect(screen.getByTestId('task-modal-project')).toHaveTextContent('none');
  });

  it('shows project choices for a collision and keeps URL-owned actions isolated', async () => {
    const one = project('one-11111111', 'Demo');
    const two = project('two-22222222', 'Demo');
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = async () => ({ success: true, data: [one, two] });
    renderAt('/p/demo');
    expect(await screen.findByText('Choose a project')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /Demo/ })).toHaveLength(2);
    expect(screen.getByTestId('settings-project')).toHaveTextContent('none');
    act(() => { void navigate('/p/demo-one-1111'); });
    await waitFor(() => expect(screen.getByTestId('settings-project')).toHaveTextContent(one.id));
    expect(screen.getByTestId('wizard-project')).toHaveTextContent(one.id);
    expect(screen.getByTestId('task-modal-project')).toHaveTextContent(one.path);
    act(() => { void navigate('/projects'); });
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Projects' })).toBeInTheDocument());
    expect(document.querySelectorAll('main')).toHaveLength(1);
    expect(screen.getByTestId('tab-project')).toHaveTextContent('none');
    expect(screen.getByTestId('sidebar-project')).toHaveTextContent('none');
    expect(screen.queryByTestId('wizard-project')).toBeNull();
    expect(screen.getByTestId('task-modal-project')).toHaveTextContent('none');
  });

  it('keeps the global review queue reachable without a project', async () => {
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = async () => ({ success: true, data: [] });
    renderAt('/pfactory');
    expect(await screen.findByTestId('queue')).toBeInTheDocument();
  });

  it('resyncs on back/forward, isolates tasks, and preserves the terminal until leaving the project route', async () => {
    renderAt('/p/alpha');
    await waitFor(() => expect(screen.getByTestId('planning')).toHaveTextContent('alpha-task'));
    act(() => { void navigate('/p/beta/insights'); });
    await waitFor(() => expect(screen.getByTestId('insights')).toHaveTextContent('id-beta'));
    expect(useTaskStore.getState().tasks.map((task) => task.title)).toEqual(['beta-task']);
    expect(screen.getByTestId('terminal')).toHaveTextContent('/src/id-beta');
    expect(mounts.count).toBe(1);
    act(() => { void navigate(-1); });
    await waitFor(() => expect(screen.getByTestId('planning')).toHaveTextContent('alpha-task'));
    expect(screen.getByTestId('planning')).not.toHaveTextContent('beta-task');
    act(() => { void navigate(1); });
    await waitFor(() => expect(screen.getByTestId('insights')).toHaveTextContent('id-beta'));
    expect(mounts.count).toBe(1);
    act(() => { void navigate('/projects'); });
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Projects' })).toBeInTheDocument());
    expect(mounts.unmounts).toBe(1);
  });

  it('drops a slow superseded task response after switching projects', async () => {
    let resolveAlpha: (value: unknown) => void = () => undefined;
    const getTasks = vi.fn((id: string) => id === 'id-alpha'
      ? new Promise((resolve) => { resolveAlpha = resolve; })
      : Promise.resolve({ success: true, data: tasks['id-beta'] }));
    (window.API as unknown as { getTasks: typeof getTasks }).getTasks = getTasks;
    renderAt('/p/alpha');
    await waitFor(() => expect(getTasks).toHaveBeenCalledWith('id-alpha'));
    act(() => { void navigate('/p/beta'); });
    await waitFor(() => expect(screen.getByTestId('planning')).toHaveTextContent('beta-task'));
    await act(async () => { resolveAlpha({ success: true, data: tasks['id-alpha'] }); });
    expect(useTaskStore.getState().tasks.map((task) => task.title)).toEqual(['beta-task']);
  });

  it('resolves each independent mount from its own URL', async () => {
    const first = renderAt('/p/alpha');
    await waitFor(() => expect(screen.getByTestId('terminal')).toHaveTextContent('/src/id-alpha'));
    first.unmount();
    useProjectStore.setState({ projects: [], selectedProjectId: null, activeProjectId: null, openProjectIds: [], tabOrder: [] });
    const second = renderAt('/p/beta');
    await waitFor(() => expect(screen.getByTestId('terminal')).toHaveTextContent('/src/id-beta'));
    second.unmount();
  });

  it('redirects legacy console links to the canonical project console and retains the spec', async () => {
    renderAt('/console/id-beta/001-spec');
    await waitFor(() => expect(pathname).toBe('/p/beta/console/001-spec'));
    expect(screen.getByTestId('console')).toHaveTextContent('id-beta:001-spec');
  });

  it('resolves legacy console IDs before slug aliases and preserves encoded spec and URL state', async () => {
    const conflict = project('beta', 'Elsewhere');
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = async () => ({ success: true, data: [alpha, beta, conflict] });
    renderAt('/console/beta/a%23b?tail=1#note');
    await waitFor(() => expect(pathname).toBe('/p/elsewhere/console/a%23b'));
    expect(locationState).toBe('/p/elsewhere/console/a%23b?tail=1#note');
    expect(screen.getByTestId('console')).toHaveTextContent('beta:a#b');
  });

  it('does not mount a console for an unknown project', async () => {
    renderAt('/p/missing/console/001');
    expect(await screen.findByRole('alert')).toHaveTextContent('Project not found');
    expect(screen.queryByTestId('console')).toBeNull();
  });

  it('sends the no-spec legacy console link to the project terminals', async () => {
    renderAt('/console/id-beta?tail=1#note');
    await waitFor(() => expect(pathname).toBe('/p/beta/terminals'));
    expect(locationState).toBe('/p/beta/terminals?tail=1#note');
    expect(screen.getByTestId('terminal')).toHaveTextContent('/src/id-beta');
  });

  it('shows a retryable fetch error instead of not-found', async () => {
    (window.API as unknown as { getProjects: () => Promise<unknown> }).getProjects = async () => ({ success: false, error: 'Offline' });
    renderAt('/p/beta');
    expect(await screen.findByRole('alert')).toHaveTextContent('Offline');
    expect(screen.queryByText('Project not found: beta')).toBeNull();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('shows a project-list error and retries into the real projects page', async () => {
    const getProjects = vi.fn().mockResolvedValueOnce({ success: false, error: 'Offline' })
      .mockResolvedValueOnce({ success: true, data: [alpha] });
    (window.API as unknown as { getProjects: typeof getProjects }).getProjects = getProjects;
    renderAt('/projects');
    expect(await screen.findByRole('alert')).toHaveTextContent('Offline');
    expect(screen.queryByText('No projects yet.')).toBeNull();
    act(() => { screen.getByRole('button', { name: 'Retry' }).click(); });
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Projects' })).toBeInTheDocument());
    expect(screen.getByText('Alpha')).toBeInTheDocument();
  });

  it('returns to an unauthenticated project deep link after login', async () => {
    useAuthStore.setState({ isAuthenticated: false });
    const first = renderAt('/p/beta/insights');
    await waitFor(() => expect(screen.getByTestId('login')).toBeInTheDocument());
    expect(sessionStorage.getItem('pf-return-to')).toBe('/p/beta/insights');
    first.unmount();
    useAuthStore.setState({ isAuthenticated: true });
    renderAt('/');
    await waitFor(() => expect(pathname).toBe('/p/beta/insights'));
    expect(screen.getByTestId('insights')).toHaveTextContent('id-beta');
    expect(sessionStorage.getItem('pf-return-to')).toBeNull();
  });
});
