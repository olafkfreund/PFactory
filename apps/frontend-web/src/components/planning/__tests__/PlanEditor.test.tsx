/**
 * @vitest-environment jsdom
 *
 * Tests for <PlanEditor> — the revise loop (#692).
 *
 * The bug these guard: Re-process called processCurrentSession() with no
 * arguments, so a user's edits never left the browser. The backend re-ran the
 * ORIGINAL plan and returned 200, and the UI showed a fresh review as though
 * the edit had been considered. So the assertion that matters is not "a request
 * was made" but "the edited text was IN the request body".
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { translate } from './i18n-mock';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: translate }),
}));

import { PlanEditor } from '../PlanEditor';
import { usePlanStore } from '../../../stores/plan-store';

const mockSession = {
  session_id: 'sess-001',
  status: 'processed',
  plan: {
    plan_id: 'p1',
    title: 'Original title',
    description: 'Original description',
    source_format: 'markdown',
    source_channel: null,
    criteria: [{ id: 'AC#1', text: 'Original criterion' }],
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
};

function makeFetch(): { fetchFn: typeof fetch; bodyOf: () => Record<string, unknown> } {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: () => Promise.resolve(mockSession),
    text: () => Promise.resolve(''),
  });
  return {
    fetchFn: fn as typeof fetch,
    bodyOf: () =>
      JSON.parse(String(fn.mock.calls[0][1].body)) as Record<string, unknown>,
  };
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

describe('<PlanEditor>', () => {
  it('sends the edited description in the re-process request', async () => {
    const { fetchFn, bodyOf } = makeFetch();
    usePlanStore.setState({ fetchFn });
    render(<PlanEditor session={mockSession as never} />);

    fireEvent.change(screen.getByTestId('edit-description'), {
      target: { value: 'Lawful basis: legitimate interest.' },
    });
    fireEvent.click(screen.getByTestId('reprocess-btn'));

    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    expect(bodyOf().description).toBe('Lawful basis: legitimate interest.');
  });

  it('sends edited title and criteria too', async () => {
    const { fetchFn, bodyOf } = makeFetch();
    usePlanStore.setState({ fetchFn });
    render(<PlanEditor session={mockSession as never} />);

    fireEvent.change(screen.getByLabelText(/plan title/i), {
      target: { value: 'Revised title' },
    });
    fireEvent.change(screen.getByLabelText('Criterion AC#1'), {
      target: { value: 'Revised criterion' },
    });
    fireEvent.click(screen.getByTestId('reprocess-btn'));

    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    const body = bodyOf();
    expect(body.title).toBe('Revised title');
    expect(body.criteria).toEqual([{ id: 'AC#1', text: 'Revised criterion' }]);
  });

  it('posts to the session process endpoint', async () => {
    const { fetchFn } = makeFetch();
    usePlanStore.setState({ fetchFn });
    render(<PlanEditor session={mockSession as never} />);

    fireEvent.click(screen.getByTestId('reprocess-btn'));

    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    const [url, init] = (fetchFn as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain('/api/plan/sessions/sess-001/process');
    expect((init as RequestInit).method).toBe('POST');
  });

  it('gives the description room for a full plan document', () => {
    render(<PlanEditor session={mockSession as never} />);
    // rows=4 made a kilobytes-long markdown plan unreadable through a 3-line
    // porthole. Guarding the intent, not the exact number.
    const rows = Number(screen.getByTestId('edit-description').getAttribute('rows'));
    expect(rows).toBeGreaterThanOrEqual(12);
  });
});
