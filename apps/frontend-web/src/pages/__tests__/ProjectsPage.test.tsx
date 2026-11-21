/** @vitest-environment jsdom */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { Project } from '../../shared/types';
import '../../shared/i18n';
import { ProjectsPage } from '../ProjectsPage';

const { removeProject } = vi.hoisted(() => ({ removeProject: vi.fn(async () => true) }));
vi.mock('../../stores/project-store', () => ({ removeProject }));

const project = { id: 'project-1', name: 'Alpha App', path: '/code/alpha' } as Project;
const props = {
  projects: [project],
  slugs: new Map([['project-1', 'alpha-app']]),
  onAdd: vi.fn(),
  onOpen: vi.fn(),
};

describe('ProjectsPage', () => {
  it('requires the exact project name before unregistering', () => {
    removeProject.mockClear();
    render(<ProjectsPage {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const confirm = screen.getByRole('button', { name: 'Unregister project' });
    expect(removeProject).not.toHaveBeenCalled();
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Project name' }), { target: { value: 'alpha app' } });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Project name' }), { target: { value: project.name } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(removeProject).toHaveBeenCalledExactlyOnceWith(project.id);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens and adds projects through the supplied callbacks', () => {
    props.onAdd.mockClear();
    props.onOpen.mockClear();
    render(<ProjectsPage {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add project' }));
    expect(props.onOpen).toHaveBeenCalledExactlyOnceWith(project);
    expect(props.onAdd).toHaveBeenCalledOnce();
  });
});
