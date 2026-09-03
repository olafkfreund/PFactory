/**
 * @vitest-environment jsdom
 *
 * Tests for <AnnotationPanel> — accepting suggestions (#701).
 *
 * The panel was read-only: suggestions could be read and never acted on. These
 * assert the accept path sends the right ids and the right TEXT (including a
 * human edit), and that a suggestion with no draft cannot be accepted at all —
 * offering a button that applies nothing is the failure this feature exists to
 * avoid.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { AnnotationPanel } from '../AnnotationPanel';
import { usePlanStore } from '../../../stores/plan-store';

const suggestion = (over: Record<string, unknown> = {}) => ({
  anchor: '',
  anchor_line: 12,
  original_excerpt: 'NFR-008 ...',
  suggestion: "missing required tag 'owner'",
  why: "template 'software-service' requires tag 'owner'",
  severity: 'medium',
  source: 'template:software-service',
  citation: null,
  id: 'S1',
  replacement: 'owner: <owner>',
  mode: 'append_tag',
  ...over,
});

const mockSession = {
  session_id: 'sess-001',
  status: 'processed',
  plan: {
    plan_id: 'p1',
    title: 'T',
    description: 'D',
    source_format: 'markdown',
    source_channel: null,
    criteria: [],
    target_kind: 'software',
    plan_type: 'feature',
    enrichment: { infra: [], knowledge: [] },
    content_hash: 'abc',
    ingested_at: '2026-06-01T00:00:00Z',
  },
  epic: null,
  artifacts: [],
  review: null,
  emit_result: null,
  created_at: '2026-06-01T00:00:00Z',
  original_filename: 'find-friend-plan.md',
  annotation: { suggestions: [suggestion()], improved_markdown: '', change_log: [] },
};

function makeFetch() {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: () => Promise.resolve(mockSession),
    text: () => Promise.resolve(''),
  });
  return {
    fetchFn: fn as unknown as typeof fetch,
    bodyOf: () => JSON.parse(String(fn.mock.calls[0][1].body)) as Record<string, unknown>,
    urlOf: () => String(fn.mock.calls[0][0]),
    fn,
  };
}

function withSuggestions(...ss: ReturnType<typeof suggestion>[]) {
  return { ...mockSession, annotation: { ...mockSession.annotation, suggestions: ss } };
}

beforeEach(() => {
  localStorage.setItem('pfactory-token', 'test-token');
  usePlanStore.setState({
    sessions: [],
    currentSession: mockSession as never,
    loading: false,
    sessionLoading: false,
    error: null,
    fetchFn: undefined,
  });
});

afterEach(() => vi.clearAllMocks());

describe('<AnnotationPanel>', () => {
  it('cannot apply until something is selected', () => {
    render(<AnnotationPanel session={mockSession as never} />);
    expect(screen.getByTestId('apply-suggestions-btn')).toBeDisabled();
  });

  it('sends the selected id and its drafted text to the apply endpoint', async () => {
    const { fetchFn, bodyOf, urlOf } = makeFetch();
    usePlanStore.setState({ fetchFn });
    render(<AnnotationPanel session={mockSession as never} />);

    fireEvent.click(screen.getByTestId('accept-S1'));
    fireEvent.click(screen.getByTestId('apply-suggestions-btn'));

    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    expect(urlOf()).toContain('/api/plan/sessions/sess-001/suggestions/apply');
    expect(bodyOf().accepted).toEqual([{ id: 'S1', replacement: 'owner: <owner>' }]);
    expect(bodyOf().reprocess).toBe(true);
  });

  it('sends the human-edited text, not the draft', async () => {
    const { fetchFn, bodyOf } = makeFetch();
    usePlanStore.setState({ fetchFn });
    render(<AnnotationPanel session={mockSession as never} />);

    fireEvent.click(screen.getByTestId('accept-S1'));
    fireEvent.change(screen.getByTestId('draft-S1'), {
      target: { value: 'owner: platform-team' },
    });
    fireEvent.click(screen.getByTestId('apply-suggestions-btn'));

    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    expect(bodyOf().accepted).toEqual([{ id: 'S1', replacement: 'owner: platform-team' }]);
  });

  it('offers no checkbox for a suggestion with no draft', () => {
    const manual = suggestion({ id: 'S2', suggestion: 'Oversized epic', replacement: '', mode: 'manual' });
    render(<AnnotationPanel session={withSuggestions(manual) as never} />);

    expect(screen.queryByTestId('accept-S2')).not.toBeInTheDocument();
    expect(screen.getByText(/no automatic draft/i)).toBeInTheDocument();
  });

  it('warns that applying clears the review and approval', () => {
    render(<AnnotationPanel session={mockSession as never} />);
    fireEvent.click(screen.getByTestId('accept-S1'));
    expect(screen.getByText(/clears the current review and any approval/i)).toBeInTheDocument();
  });
});
