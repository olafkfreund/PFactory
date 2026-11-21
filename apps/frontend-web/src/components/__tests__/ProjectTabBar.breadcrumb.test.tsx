/** @vitest-environment jsdom */
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { Project } from '../../shared/types';
import { useProjectStore } from '../../stores/project-store';
import { ProjectTabBar } from '../ProjectTabBar';

vi.mock('../SortableProjectTab', () => ({ SortableProjectTab: () => null }));
vi.mock('../PortalSwitcher', () => ({ PortalSwitcher: () => null }));
vi.mock('../ClaudeCodeStatusBadge', () => ({ ClaudeCodeStatusBadge: () => null }));
vi.mock('../CLIToolStatusBadge', () => ({ CLIToolStatusBadge: () => null }));
vi.mock('../OpenAIEndpointsStatusBadge', () => ({ OpenAIEndpointsStatusBadge: () => null }));
vi.mock('../../lib/api-client', () => ({ apiRequest: () => Promise.resolve({ success: false }) }));

const projects = [
  { id: 'one-11111111', name: 'One', path: '/one' },
  { id: 'two-22222222', name: 'Two', path: '/two' },
] as Project[];
beforeEach(() => {
  localStorage.clear();
  useProjectStore.setState({ projects });
  window.matchMedia = (() => ({ matches: false })) as never;
});

it('keeps the breadcrumb outside the scrolling tabs and restores focus on selection and Escape', () => {
  const onProjectSelect = vi.fn();
  const { container } = render(<ProjectTabBar
    projects={projects} activeProjectId={projects[0].id} currentSlug="one"
    onProjectSelect={onProjectSelect} onProjectClose={vi.fn()} onViewAllProjects={vi.fn()}
  />);
  const trigger = screen.getByRole('button', { name: /Project/ });
  expect(container.firstElementChild).not.toHaveClass('overflow-x-auto');
  expect(container.querySelector('.overflow-x-auto')).toBeTruthy();
  fireEvent.click(trigger);
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
  expect(document.activeElement).toBe(trigger);
  expect(onProjectSelect).toHaveBeenCalled();
  fireEvent.click(trigger);
  const all = screen.getByRole('button', { name: 'All projects…' });
  all.focus();
  fireEvent.keyDown(all, { key: 'Escape' });
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  expect(document.activeElement).toBe(trigger);
});
