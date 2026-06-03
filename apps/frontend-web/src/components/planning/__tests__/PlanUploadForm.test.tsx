/**
 * @vitest-environment jsdom
 *
 * Tests for <PlanUploadForm> — file + text submit paths.
 *
 * The store is seeded with a mock fetchFn via the component's
 * fetchFn prop so no real network calls are made.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { PlanUploadForm } from '../PlanUploadForm';
import { usePlanStore } from '../../../stores/plan-store';

beforeEach(() => {
  localStorage.setItem('pfactory-token', 'test-token');
  // Reset store state between tests
  usePlanStore.setState({
    sessions: [],
    currentSession: null,
    loading: false,
    sessionLoading: false,
    error: null,
    fetchFn: undefined,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── Fake fetch helper ─────────────────────────────────────────────────

function makeFetch(jsonBody: unknown, ok = true, status = 200): typeof fetch {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: ok ? 'OK' : 'Error',
    json: async () => jsonBody,
    text: async () => '',
  }) as unknown as typeof fetch;
}

const mockSession = {
  session_id: 'sess-001',
  status: 'ingested',
  plan: {
    plan_id: 'p1',
    title: 'Test plan',
    description: '',
    source_format: 'text',
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
};

// ── Render ─────────────────────────────────────────────────────────────

describe('<PlanUploadForm>', () => {
  it('renders file upload mode by default', () => {
    render(<PlanUploadForm />);
    // The drop zone button has aria-label="Drop a file here or click to browse"
    expect(screen.getByLabelText(/drop a file here/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/plan title/i)).toBeInTheDocument();
  });

  it('switches to text mode when Paste text is clicked', () => {
    render(<PlanUploadForm />);
    fireEvent.click(screen.getByText(/paste text/i));
    expect(screen.getByTestId('plan-text-input')).toBeInTheDocument();
  });

  // ── Text submit ───────────────────────────────────────────────────

  it('calls ingest-text API when text is submitted', async () => {
    const fetchFn = makeFetch(mockSession);
    const onSuccess = vi.fn();
    render(<PlanUploadForm onSuccess={onSuccess} fetchFn={fetchFn} />);

    // Switch to text mode
    fireEvent.click(screen.getByText(/paste text/i));

    // Enter text
    const textarea = screen.getByTestId('plan-text-input');
    fireEvent.change(textarea, { target: { value: 'My specification text content' } });

    // Enter title
    fireEvent.change(screen.getByLabelText(/plan title/i), {
      target: { value: 'My Title' },
    });

    // Submit
    fireEvent.click(screen.getByTestId('submit-btn'));

    await waitFor(() => {
      expect(fetchFn).toHaveBeenCalled();
    });

    const [url, init] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/plan/sessions/ingest-text');
    const body = JSON.parse(init.body as string);
    expect(body.text).toBe('My specification text content');
    expect(body.title).toBe('My Title');
  });

  it('calls onSuccess with the session on successful text ingest', async () => {
    const fetchFn = makeFetch(mockSession);
    const onSuccess = vi.fn();
    render(<PlanUploadForm onSuccess={onSuccess} fetchFn={fetchFn} />);

    fireEvent.click(screen.getByText(/paste text/i));
    fireEvent.change(screen.getByTestId('plan-text-input'), {
      target: { value: 'Some plan content that is long enough' },
    });
    fireEvent.click(screen.getByTestId('submit-btn'));

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledWith(expect.objectContaining({ session_id: 'sess-001' }));
    });
  });

  // ── File submit ───────────────────────────────────────────────────

  it('calls ingest (multipart) API when a file is submitted', async () => {
    const fetchFn = makeFetch(mockSession);
    const onSuccess = vi.fn();
    render(<PlanUploadForm onSuccess={onSuccess} fetchFn={fetchFn} />);

    // Simulate a file being dropped onto the hidden input
    const fileInput = screen.getByTestId('file-input') as HTMLInputElement;
    const file = new File(['# Test spec'], 'spec.md', { type: 'text/markdown' });
    Object.defineProperty(fileInput, 'files', { value: [file] });
    fireEvent.change(fileInput);

    // Submit button should now be enabled
    await waitFor(() => {
      expect(screen.getByTestId('submit-btn')).not.toBeDisabled();
    });

    fireEvent.click(screen.getByTestId('submit-btn'));

    await waitFor(() => {
      expect(fetchFn).toHaveBeenCalled();
    });

    const [url, init] = (fetchFn as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/plan/sessions/ingest');
    expect(init.body).toBeInstanceOf(FormData);
  });

  it('shows an error when the API returns a non-2xx on text submit', async () => {
    const fetchFn = makeFetch({ detail: 'validation error' }, false, 422);
    render(<PlanUploadForm fetchFn={fetchFn} />);

    fireEvent.click(screen.getByText(/paste text/i));
    fireEvent.change(screen.getByTestId('plan-text-input'), {
      target: { value: 'content' },
    });
    fireEvent.click(screen.getByTestId('submit-btn'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/validation error/i);
    });
  });

  it('submit button is disabled when text is empty', () => {
    render(<PlanUploadForm />);
    fireEvent.click(screen.getByText(/paste text/i));
    expect(screen.getByTestId('submit-btn')).toBeDisabled();
  });

  it('calls onCancel when Cancel is clicked', () => {
    const onCancel = vi.fn();
    render(<PlanUploadForm onCancel={onCancel} />);
    fireEvent.click(screen.getByText(/cancel/i));
    expect(onCancel).toHaveBeenCalled();
  });
});
