/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router';
import { useEffect } from 'react';
import type { Project } from '../shared/types';

const mounts = vi.hoisted(() => ({ count: 0, unmounts: 0 }));
vi.mock('../components/Sidebar', () => ({ Sidebar: () => null }));
vi.mock('../components/ProjectTabBar', () => ({ ProjectTabBar: () => null }));
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
vi.mock('../components/pfactory/PFactoryPortal', () => ({ PFactoryPortal: () => null }));
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
vi.mock('../components/settings', () => ({ AppSettingsDialog: () => null }));
vi.mock('../components/TaskCreationWizard', () => ({ TaskCreationWizard: () => null }));
vi.mock('../components/CloudCheckDialog', () => ({ CloudCheckDialog: () => null }));
vi.mock('../components/task-detail', () => ({ TaskDetailModal: () => null }));
vi.mock('../components/onboarding', () => ({ OnboardingWizard: () => null }));
vi.mock('../components/ProjectSwitchLoadingModal', () => ({ ProjectSwitchLoadingModal: () => null }));
vi.mock('../components/CommandPalette', () => ({ CommandPalette: () => null }));
vi.mock('../components/ui/toaster', () => ({ Toaster: () => null }));
vi.mock('../pages/EditorPage', () => ({ EditorPage: () => null }));
vi.mock('../pages/ProjectsPage', () => ({ ProjectsPage: () => <div data-testid="projects" /> }));
vi.mock('../pages/LoginPage', () => ({ LoginPage: () => <div data-testid="login" /> }));
vi.mock('../pages/ConsolePage', () => ({ ConsolePage: () => <div data-testid="console" /> }));
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
function LocationProbe() {
  navigate = useNavigate();
  pathname = useLocation().pathname;
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
    await waitFor(() => expect(screen.getByTestId('projects')).toBeInTheDocument());
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
    expect(screen.getByTestId('console')).toBeInTheDocument();
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
