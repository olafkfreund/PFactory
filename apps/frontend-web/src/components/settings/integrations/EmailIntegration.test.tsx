/**
 * @vitest-environment jsdom
 */
/**
 * Origin check on the OAuth popup callback (CodeQL js/missing-origin-check).
 *
 * The backend's OAuth result page posts its outcome to this window with a `'*'`
 * targetOrigin, so the message is broadcast and any page holding a handle on
 * this window can forge it. These tests pin the boundary: a message from a
 * foreign origin must not move the component at all.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import { EmailIntegration } from './EmailIntegration';
import type { AppSettings } from '../../../shared/types';
import { apiRequest } from '../../../lib/api-client';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

vi.mock('../../../lib/api-client', () => ({
  apiRequest: vi.fn()
}));

const mockApiRequest = vi.mocked(apiRequest);

function stubApi() {
  mockApiRequest.mockImplementation((endpoint: string) => {
    if (endpoint === '/email/accounts') {
      return Promise.resolve({ success: true, data: [] });
    }
    return Promise.resolve({ success: true, data: { microsoft: true, google: true } });
  });
}

async function renderIntegration() {
  render(
    <EmailIntegration settings={{} as AppSettings} onSettingsChange={vi.fn()} />
  );
  // Wait out the initial loads so a later apiRequest call count is unambiguous.
  await waitFor(() => expect(screen.getByText('email.title')).toBeInTheDocument());
}

function postCallback(origin: string, data: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { origin, data }));
  });
}

const CALLBACK = {
  type: 'email-oauth-callback',
  status: 'success',
  message: 'Connected: victim@example.com'
};

describe('EmailIntegration OAuth callback origin check', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stubApi();
  });

  it('acts on a callback from its own origin', async () => {
    await renderIntegration();
    postCallback(window.location.origin, CALLBACK);
    await waitFor(() => expect(screen.getByText('email.connectionSuccess')).toBeInTheDocument());
  });

  it('ignores a callback from a disallowed origin', async () => {
    await renderIntegration();
    const callsBefore = mockApiRequest.mock.calls.length;

    postCallback('https://evil.example', {
      ...CALLBACK,
      status: 'error',
      message: 'Your session expired. Re-enter your password at evil.example.'
    });

    // The handler did not act: no banner (neither the forged error text nor the
    // success copy) and no refetch triggered by the forged message.
    await waitFor(() => expect(mockApiRequest).toHaveBeenCalledTimes(callsBefore));
    expect(screen.queryByText(/Re-enter your password/)).not.toBeInTheDocument();
    expect(screen.queryByText('email.connectionSuccess')).not.toBeInTheDocument();
    expect(screen.queryByText('email.connectionFailed')).not.toBeInTheDocument();
  });

  it('ignores a callback whose origin merely looks like ours', async () => {
    await renderIntegration();
    // Suffix and scheme confusion, the two ways a substring check would fail.
    for (const origin of [`${window.location.origin}.evil.example`, window.location.origin.replace('http:', 'https:')]) {
      postCallback(origin, CALLBACK);
    }
    expect(screen.queryByText('email.connectionSuccess')).not.toBeInTheDocument();
  });
});
