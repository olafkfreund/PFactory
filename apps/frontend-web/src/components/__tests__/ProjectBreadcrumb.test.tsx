/** @vitest-environment jsdom */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ProjectBreadcrumb } from '../ProjectBreadcrumb';

const projects = [
  { slug: 'alpha', name: 'Alpha App' },
  { slug: 'beta', name: 'Beta' },
];

describe('ProjectBreadcrumb', () => {
  it('shows the current project and no delete or add control', () => {
    render(<ProjectBreadcrumb projects={projects} currentSlug="beta" onSelect={vi.fn()} />);
    expect(screen.getByRole('button', { name: /beta/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /beta/i }));
    expect(screen.queryByText(/delete|remove|add project/i)).toBeNull();
  });

  it('filters by search and selects with click and with Enter', () => {
    const onSelect = vi.fn();
    render(<ProjectBreadcrumb projects={projects} currentSlug="beta" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: /beta/i }));
    const input = screen.getByLabelText('Search projects');
    fireEvent.change(input, { target: { value: 'alp' } });
    expect(screen.getAllByRole('option')).toHaveLength(1);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith('alpha');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('marks the current option, closes on Escape, offers All projects', () => {
    const onViewAll = vi.fn();
    render(<ProjectBreadcrumb projects={projects} currentSlug="beta" onSelect={vi.fn()} onViewAll={onViewAll} />);
    fireEvent.click(screen.getByRole('button', { name: /beta/i }));
    expect(screen.getByRole('option', { name: /beta/i })).toHaveAttribute('aria-current', 'true');
    fireEvent.click(screen.getByText('All projects…'));
    expect(onViewAll).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /beta/i }));
    fireEvent.keyDown(screen.getByLabelText('Search projects'), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});

describe('ProjectBreadcrumb a11y', () => {
  it('exposes the highlighted option through aria-activedescendant', () => {
    render(<ProjectBreadcrumb projects={projects} currentSlug="alpha" onSelect={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /alpha/i }));
    const input = screen.getByLabelText('Search projects');
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    const active = input.getAttribute('aria-activedescendant');
    expect(active).toBeTruthy();
    const opt = document.getElementById(active ?? '');
    expect(opt).toHaveTextContent('Beta');
    expect(opt).toHaveAttribute('aria-selected', 'true');
  });
});
