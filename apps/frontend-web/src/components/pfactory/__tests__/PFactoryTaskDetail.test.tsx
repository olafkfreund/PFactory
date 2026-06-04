/**
 * @vitest-environment jsdom
 *
 * Tests for <PFactoryTaskDetail> — Task 10 (#11) commit 3.
 *
 * Uses a URL-aware fetchFn mock so a single test can stub multiple
 * endpoints (detail + verdicts + report) and verify lazy-load
 * behaviour.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { PFactoryTaskDetail, statusSeverity } from '../PFactoryTaskDetail';

beforeEach(() => {
  localStorage.setItem('pfactory-token', 'test-token');
});

// ── statusSeverity unit ───────────────────────────────────────────────

describe('statusSeverity', () => {
  it.each([
    ['stalled', 'destructive'],
    ['stuck', 'destructive'],
    ['triager_failed', 'destructive'],
    ['triaged', 'success'],
    ['generating', 'info'],
    ['replan_needed', 'warning'],
    ['triaged_empty', 'muted'],
  ])('maps %s → %s', (status, expected) => {
    expect(statusSeverity(status)).toBe(expected);
  });

  it('null status → muted', () => {
    expect(statusSeverity(null)).toBe('muted');
  });
});

// ── URL-aware fetch helper ────────────────────────────────────────────

interface UrlResponse {
  ok?: boolean;
  status?: number;
  statusText?: string;
  jsonBody?: unknown;
  textBody?: string;
}

function makeUrlAwareFetch(routes: Record<string, UrlResponse>): typeof fetch {
  return vi.fn().mockImplementation(async (input: RequestInfo) => {
    const url = typeof input === 'string' ? input : input.url;
    // Find first registered route that matches as substring
    let match: UrlResponse | undefined;
    for (const key of Object.keys(routes)) {
      if (url.endsWith(key) || url === key) {
        match = routes[key];
        break;
      }
    }
    const opts: UrlResponse = match ?? { ok: false, status: 404, statusText: 'Not Found' };
    return {
      ok: opts.ok ?? true,
      status: opts.status ?? 200,
      statusText: opts.statusText ?? 'OK',
      json: async () => opts.jsonBody,
      text: async () => opts.textBody ?? '',
    };
  }) as unknown as typeof fetch;
}

const sampleDetail = (overrides: Partial<{
  status: string;
  verdictsExists: boolean;
  reportExists: boolean;
}> = {}) => ({
  task_id: 'spec-x',
  project_id: 'demo',
  spec_id: 'spec-x',
  status_json: { status: overrides.status ?? 'triaged' },
  artefacts: {
    test_plan: { path: 'test_plan.json', exists: true },
    verdicts: { path: 'findings/verdicts.json', exists: overrides.verdictsExists ?? true },
    triage_report_json: { path: 'findings/triage_report.json', exists: true },
    triage_report_md: { path: 'findings/triage_report.md', exists: overrides.reportExists ?? true },
    pr_comment_body: { path: 'findings/pr_comment_body.md', exists: false },
  },
});

// ── Initial fetch / loading / error ───────────────────────────────────

describe('<PFactoryTaskDetail> initial fetch', () => {
  it('shows loading state before detail resolves', () => {
    const fetchFn = vi.fn().mockReturnValue(
      new Promise(() => { /* never resolves */ }),
    ) as unknown as typeof fetch;
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText(/Loading task/i)).toBeInTheDocument();
  });

  it('renders header + tabs after detail resolves', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);

    await waitFor(() =>
      expect(screen.getByTestId('pfactory-task-detail')).toBeInTheDocument(),
    );
    expect(screen.getByRole('heading', { name: 'spec-x' })).toBeInTheDocument();
    expect(screen.getByTestId('tab-status')).toBeInTheDocument();
    expect(screen.getByTestId('tab-lanes')).toBeInTheDocument();
    expect(screen.getByTestId('tab-verdicts')).toBeInTheDocument();
    expect(screen.getByTestId('tab-report')).toBeInTheDocument();
    expect(screen.getByTestId('tab-logs')).toBeInTheDocument();
  });

  it('shows alert when detail fetch returns 500', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': {
        ok: false, status: 500, statusText: 'Internal Server Error',
        jsonBody: { detail: 'database down' },
      },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText(/database down/)).toBeInTheDocument();
  });
});

// ── Default tab: Status ──────────────────────────────────────────────

describe('<PFactoryTaskDetail> status tab', () => {
  it('is the default active tab and shows status_json', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ status: 'triaged' }) },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() =>
      expect(screen.getByTestId('panel-status')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('tab-status')).toHaveAttribute('data-active', 'true');
    // JSON pretty-printed in the panel
    expect(screen.getByTestId('panel-status').textContent).toMatch(/triaged/);
  });
});

// ── Lanes tab ────────────────────────────────────────────────────────

describe('<PFactoryTaskDetail> lanes tab', () => {
  it('switches to lanes tab and renders the grid with unit status', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ status: 'triaged' }) },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-lanes'));

    fireEvent.click(screen.getByTestId('tab-lanes'));
    expect(screen.getByTestId('lane-status-grid')).toBeInTheDocument();
    expect(screen.getByTestId('lane-card-unit')).toHaveAttribute(
      'data-lane-state', 'success',
    );
    expect(screen.getByTestId('lane-card-browser')).toHaveAttribute(
      'data-lane-state', 'idle',
    );
  });
});

// ── Verdicts tab (lazy load) ─────────────────────────────────────────

describe('<PFactoryTaskDetail> verdicts tab', () => {
  it('fetches verdicts lazily on tab open', async () => {
    const verdictsBody = {
      evaluator_version: 'task7-commit5',
      mode: 'initial',
      generated_at: '2026-05-28T00:00:00+00:00',
      verdicts: [
        {
          test_id: 't0', test_file: 'tests/test_0.py',
          verdict: 'accept', reasons: ['ok'],
          signals_summary: {
            coverage_delta_pct: 5.5, stability: 'stable', mutation: 'killed',
          },
        },
        {
          test_id: 't1', test_file: 'tests/test_1.py',
          verdict: 'reject', reasons: ['bad'],
          signals_summary: {
            coverage_delta_pct: 0, stability: 'stable', mutation: 'survived',
          },
        },
      ],
    };
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
      '/spec-x/verdicts.json': { jsonBody: verdictsBody },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));

    // Verdicts NOT requested yet
    expect((fetchFn as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1);

    fireEvent.click(screen.getByTestId('tab-verdicts'));

    await waitFor(() => screen.getByTestId('verdict-table'));
    expect(screen.getByTestId('verdict-row-t0')).toBeInTheDocument();
    expect(screen.getByTestId('verdict-row-t1')).toBeInTheDocument();
    // Now 2 fetches total
    expect((fetchFn as ReturnType<typeof vi.fn>).mock.calls.length).toBe(2);
  });

  it('disables Verdicts tab when artefact is absent', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: false }) },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));
    expect(screen.getByTestId('tab-verdicts')).toBeDisabled();
  });

  it('shows friendly 404 message when verdicts endpoint returns 404', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
      '/spec-x/verdicts.json': {
        ok: false, status: 404, statusText: 'Not Found',
        jsonBody: { detail: 'artefact not found' },
      },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));
    fireEvent.click(screen.getByTestId('tab-verdicts'));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toBeInTheDocument(),
    );
    expect(screen.getByText(/hasn't reached the Evaluator/i)).toBeInTheDocument();
  });
});

// ── Report tab (lazy load) ───────────────────────────────────────────

describe('<PFactoryTaskDetail> report tab', () => {
  it('lazy-fetches the markdown and renders it on tab open', async () => {
    const md = '# Triage Report\n\nLooks good.\n';
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
      '/spec-x/triage-report.md': { textBody: md },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-report'));

    fireEvent.click(screen.getByTestId('tab-report'));
    await waitFor(() => screen.getByTestId('report-md-content'));
    // The report is now rendered as markdown (MarkdownBody), not a raw <pre>
    // dump — so the heading "#" is gone and a real <h1> carries the text.
    const panel = screen.getByTestId('report-md-content');
    expect(panel.textContent).toContain('Triage Report');
    expect(panel.textContent).toContain('Looks good.');
    expect(panel.querySelector('h1')?.textContent).toBe('Triage Report');
  });

  it('disables Report tab when triage_report_md artefact is absent', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ reportExists: false }) },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-report'));
    expect(screen.getByTestId('tab-report')).toBeDisabled();
  });

  it('shows friendly 404 message when report endpoint returns 404', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
      '/spec-x/triage-report.md': {
        ok: false, status: 404, statusText: 'Not Found',
        jsonBody: { detail: 'artefact not found' },
      },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-report'));
    fireEvent.click(screen.getByTestId('tab-report'));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toBeInTheDocument(),
    );
    expect(screen.getByText(/hasn't reached the Triager/i)).toBeInTheDocument();
  });

  it('does not refetch report on subsequent tab opens', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
      '/spec-x/triage-report.md': { textBody: '# Report\n' },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-report'));

    // First open
    fireEvent.click(screen.getByTestId('tab-report'));
    await waitFor(() => screen.getByTestId('report-md-content'));
    const callsAfterFirst = (fetchFn as ReturnType<typeof vi.fn>).mock.calls.length;

    // Navigate away + back
    fireEvent.click(screen.getByTestId('tab-status'));
    fireEvent.click(screen.getByTestId('tab-report'));
    // No additional fetch — cached
    expect((fetchFn as ReturnType<typeof vi.fn>).mock.calls.length).toBe(callsAfterFirst);
  });
});

// ── Tab active state semantics ───────────────────────────────────────

describe('<PFactoryTaskDetail> tab active state', () => {
  it('updates aria-selected as tabs change', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail() },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-lanes'));

    expect(screen.getByTestId('tab-status')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('tab-lanes')).toHaveAttribute('aria-selected', 'false');

    fireEvent.click(screen.getByTestId('tab-lanes'));
    expect(screen.getByTestId('tab-lanes')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('tab-status')).toHaveAttribute('aria-selected', 'false');
  });
});

// ── Evidence tab (Task 16 / #32) ─────────────────────────────────────

/**
 * Verdict body with evidence_urls populated for one test.
 */
const sampleVerdictsWithEvidence = (specId: string, testId: string) => ({
  evaluator_version: 'task7-commit5',
  mode: 'initial',
  generated_at: '2026-05-29T00:00:00+00:00',
  verdicts: [
    {
      test_id: testId,
      test_file: `tests/e2e/${testId}.spec.ts`,
      verdict: 'accept',
      reasons: ['coverage +5%'],
      evidence_urls: {
        screenshots: [
          `/api/pfactory/tasks/${specId}/evidence/${testId}/screenshots/0001.png`,
        ],
        video: `/api/pfactory/tasks/${specId}/evidence/${testId}/video.webm`,
        trace: `/api/pfactory/tasks/${specId}/evidence/${testId}/trace.zip`,
        network: `/api/pfactory/tasks/${specId}/evidence/${testId}/network.har`,
      },
    },
  ],
});

describe('<PFactoryTaskDetail> evidence tab (Task 16)', () => {
  it('renders Evidence tab button when verdicts artefact exists', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: true }) },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-evidence'));
    expect(screen.getByTestId('tab-evidence')).toBeInTheDocument();
  });

  it('disables Evidence tab when verdicts artefact is absent', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: false }) },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-evidence'));
    expect(screen.getByTestId('tab-evidence')).toBeDisabled();
  });

  it('shows empty message when no verdicts loaded yet', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: true }) },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-evidence'));

    fireEvent.click(screen.getByTestId('tab-evidence'));
    await waitFor(() => screen.getByTestId('evidence-empty'));
    expect(screen.getByTestId('evidence-empty')).toBeInTheDocument();
  });

  it('shows evidence panel after verdicts with evidence_urls are loaded', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: true }) },
      '/spec-x/verdicts.json': {
        jsonBody: sampleVerdictsWithEvidence('spec-x', 'ac1-login'),
      },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));

    // Load verdicts first (populates evidence state)
    fireEvent.click(screen.getByTestId('tab-verdicts'));
    await waitFor(() => screen.getByTestId('verdict-table'));

    // Now switch to evidence tab
    fireEvent.click(screen.getByTestId('tab-evidence'));
    await waitFor(() => screen.getByTestId('evidence-panel'));
    expect(screen.getByTestId('evidence-row-ac1-login')).toBeInTheDocument();
  });

  it('renders screenshot thumbnail img tag', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: true }) },
      '/spec-x/verdicts.json': {
        jsonBody: sampleVerdictsWithEvidence('spec-x', 'ac1-login'),
      },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));

    fireEvent.click(screen.getByTestId('tab-verdicts'));
    await waitFor(() => screen.getByTestId('verdict-table'));
    fireEvent.click(screen.getByTestId('tab-evidence'));
    await waitFor(() => screen.getByTestId('evidence-screenshots'));

    const img = screen.getByTestId('evidence-screenshot-img-0001.png');
    expect(img.tagName).toBe('IMG');
    expect((img as HTMLImageElement).src).toContain('evidence/ac1-login/screenshots/0001.png');
  });

  it('renders video player element', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: true }) },
      '/spec-x/verdicts.json': {
        jsonBody: sampleVerdictsWithEvidence('spec-x', 'ac1-login'),
      },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));

    fireEvent.click(screen.getByTestId('tab-verdicts'));
    await waitFor(() => screen.getByTestId('verdict-table'));
    fireEvent.click(screen.getByTestId('tab-evidence'));
    await waitFor(() => screen.getByTestId('evidence-video'));

    const videoEl = screen.getByTestId('evidence-video-player');
    expect(videoEl.tagName).toBe('VIDEO');
    expect((videoEl as HTMLVideoElement).src).toContain('evidence/ac1-login/video.webm');
  });

  it('renders trace.zip download link', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: true }) },
      '/spec-x/verdicts.json': {
        jsonBody: sampleVerdictsWithEvidence('spec-x', 'ac1-login'),
      },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));

    fireEvent.click(screen.getByTestId('tab-verdicts'));
    await waitFor(() => screen.getByTestId('verdict-table'));
    fireEvent.click(screen.getByTestId('tab-evidence'));
    await waitFor(() => screen.getByTestId('evidence-trace-download'));

    const link = screen.getByTestId('evidence-trace-download') as HTMLAnchorElement;
    expect(link.href).toContain('evidence/ac1-login/trace.zip');
    expect(link.getAttribute('download')).not.toBeNull();
  });

  it('renders network.har download link', async () => {
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: true }) },
      '/spec-x/verdicts.json': {
        jsonBody: sampleVerdictsWithEvidence('spec-x', 'ac1-login'),
      },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));

    fireEvent.click(screen.getByTestId('tab-verdicts'));
    await waitFor(() => screen.getByTestId('verdict-table'));
    fireEvent.click(screen.getByTestId('tab-evidence'));
    await waitFor(() => screen.getByTestId('evidence-har-download'));

    const link = screen.getByTestId('evidence-har-download') as HTMLAnchorElement;
    expect(link.href).toContain('evidence/ac1-login/network.har');
  });

  it('shows evidence-empty when verdicts have no evidence_urls', async () => {
    const verdictsNoEvidence = {
      evaluator_version: 'task7-commit5',
      mode: 'initial',
      generated_at: '2026-05-29T00:00:00+00:00',
      verdicts: [
        {
          test_id: 'ac1-login',
          test_file: 'tests/e2e/ac1.spec.ts',
          verdict: 'accept',
          reasons: ['ok'],
          // No evidence_urls field
        },
      ],
    };
    const fetchFn = makeUrlAwareFetch({
      '/spec-x': { jsonBody: sampleDetail({ verdictsExists: true }) },
      '/spec-x/verdicts.json': { jsonBody: verdictsNoEvidence },
    });
    render(<PFactoryTaskDetail specId="spec-x" fetchFn={fetchFn} />);
    await waitFor(() => screen.getByTestId('tab-verdicts'));

    fireEvent.click(screen.getByTestId('tab-verdicts'));
    await waitFor(() => screen.getByTestId('verdict-table'));
    fireEvent.click(screen.getByTestId('tab-evidence'));
    await waitFor(() => screen.getByTestId('evidence-empty'));
    expect(screen.getByTestId('evidence-empty')).toBeInTheDocument();
  });
});
