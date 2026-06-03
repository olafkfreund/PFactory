/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Translate keys to their inline English fallback so assertions read naturally.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback?: string) => fallback ?? _key }),
}));

vi.mock('../../../lib/api-client', () => ({
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
}));

import { get, post, del } from '../../../lib/api-client';
import { TestCredentialsSettings } from './TestCredentialsSettings';

const ORG_KEY = 'pfactory.testCredentials.orgId';
const mockGet = vi.mocked(get);
const mockPost = vi.mocked(post);
const mockDel = vi.mocked(del);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mockGet.mockResolvedValue({ success: true, data: [] });
  mockPost.mockResolvedValue({ success: true, data: {} });
  mockDel.mockResolvedValue({ success: true });
});

describe('TestCredentialsSettings', () => {
  it('is a named export function', () => {
    expect(typeof TestCredentialsSettings).toBe('function');
  });

  it('does not fetch when no org id is configured', () => {
    render(<TestCredentialsSettings />);
    expect(mockGet).not.toHaveBeenCalled();
    expect(screen.getByText('No credentials stored yet.')).toBeInTheDocument();
  });

  it('lists stored credentials for the configured org (metadata only)', async () => {
    localStorage.setItem(ORG_KEY, 'org-123');
    mockGet.mockResolvedValue({
      success: true,
      data: [
        {
          id: 'c1',
          org_id: 'org-123',
          name: 'servicenow-staging',
          kind: 'form',
          username: 'svc',
          created_at: '2026-06-01T00:00:00Z',
          last_used_at: null,
        },
      ],
    });
    render(<TestCredentialsSettings />);
    expect(await screen.findByText('servicenow-staging')).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith(expect.stringContaining('org_id=org-123'));
  });

  it('opens the new-credential form (with a secret field) on Add', async () => {
    localStorage.setItem(ORG_KEY, 'org-123');
    render(<TestCredentialsSettings />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    fireEvent.click(screen.getByText('Add credential'));
    expect(screen.getByText('New credential')).toBeInTheDocument();
    expect(
      screen.getByText('Secret (password / API token / TOTP seed)')
    ).toBeInTheDocument();
  });

  it('posts a new credential and reloads the list', async () => {
    localStorage.setItem(ORG_KEY, 'org-123');
    render(<TestCredentialsSettings />);
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText('Add credential'));
    fireEvent.change(screen.getByPlaceholderText('servicenow-staging'), {
      target: { value: 'sap-prod' },
    });
    fireEvent.change(screen.getByPlaceholderText('••••••••'), {
      target: { value: 'hunter2' },
    });
    fireEvent.click(screen.getByText('Save credential'));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        '/test-credentials',
        expect.objectContaining({ org_id: 'org-123', name: 'sap-prod', secret: 'hunter2', kind: 'form' })
      )
    );
    // list re-fetched after a successful create
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });
});
