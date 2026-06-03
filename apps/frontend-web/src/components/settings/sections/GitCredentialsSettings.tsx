/**
 * Git Credentials settings page (epic #82 PR-C).
 *
 * Lets the user mint / list / revoke stored Personal Access Tokens
 * that the portal's clone service uses when registering private repos
 * via ``POST /api/projects {gitUrl, gitCredentialId}``.
 *
 * Token plaintext is shown ONCE at creation time (after the round-trip)
 * because the backend never returns it again — it's encrypted at rest
 * via ``EncryptedString`` (Epic #26 P2.3).
 *
 * V1 supports HTTPS PATs only. Deploy Keys + GitHub App credentials
 * are tracked as #82 follow-ups.
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { GitBranch, KeyRound, Plus, Trash2, Loader2, Info, Copy, Eye, EyeOff } from 'lucide-react';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { SettingsSection } from '../SettingsSection';
import type {
  GitCredentialSummary,
  CreateGitCredentialBody,
} from '../../../shared/types/ipc';

const ORG_STORAGE_KEY = 'pfactory.gitCredentials.orgId';

export function GitCredentialsSettings() {
  const { t } = useTranslation('settings');

  // The user supplies their org_id once — persisted to localStorage so
  // they don't re-enter it on every visit. Multi-tenant org discovery
  // via a real API lands with Epic #35 (Tenant Isolation).
  const [orgId, setOrgId] = useState<string>(() => {
    try {
      return localStorage.getItem(ORG_STORAGE_KEY) || '';
    } catch {
      return '';
    }
  });

  const [credentials, setCredentials] = useState<GitCredentialSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState('');
  const [host, setHost] = useState('');
  const [username, setUsername] = useState('');
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    if (!orgId) {
      setError(t('sections.gitCredentials.noOrg', 'No organization configured yet — register a project first.'));
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await window.API.listGitCredentials(orgId);
      if (result.success && result.data) {
        setCredentials(result.data);
      } else {
        setError(result.error || 'Failed to load credentials');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load credentials');
    } finally {
      setLoading(false);
    }
  }, [orgId, t]);

  useEffect(() => {
    if (orgId) {
      load();
    }
  }, [orgId, load]);

  const handleCreate = async () => {
    if (!orgId) return;
    if (!name.trim() || !token.trim()) {
      setError(t('sections.gitCredentials.fieldsRequired', 'Name and token are required.'));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body: CreateGitCredentialBody = {
        org_id: orgId,
        name: name.trim(),
        token: token.trim(),
        kind: 'pat',
        ...(host.trim() ? { host: host.trim() } : {}),
        ...(username.trim() ? { username: username.trim() } : {}),
      };
      const result = await window.API.createGitCredential(body);
      if (result.success && result.data) {
        // Reset form + reload list. The token is now durable on the
        // backend; we never see it again.
        setName('');
        setHost('');
        setUsername('');
        setToken('');
        setShowToken(false);
        setFormOpen(false);
        await load();
      } else {
        setError(result.error || 'Failed to create credential');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create credential');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (cred: GitCredentialSummary) => {
    if (!confirm(t('sections.gitCredentials.confirmDelete', `Delete credential "${cred.name}"? This cannot be undone.`))) {
      return;
    }
    setError(null);
    try {
      const result = await window.API.deleteGitCredential(cred.id);
      if (result.success) {
        await load();
      } else {
        setError(result.error || 'Failed to delete credential');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete credential');
    }
  };

  return (
    <SettingsSection
      title={t('sections.gitCredentials.title', 'Git Credentials')}
      description={t(
        'sections.gitCredentials.description',
        'Stored Personal Access Tokens for cloning private repositories. Encrypted at rest.',
      )}
    >
      <div className="space-y-4">
        <div className="rounded-lg bg-info/10 border border-info/30 p-3 flex items-start gap-2">
          <Info className="h-4 w-4 text-info shrink-0 mt-0.5" />
          <p className="text-xs text-muted-foreground">
            {t(
              'sections.gitCredentials.info',
              'When you register a project via Git URL (Add Project → Clone from Git URL), select a credential here to clone private repos. Tokens are encrypted at rest and never logged.',
            )}
          </p>
        </div>

        {/* Org ID input — required once, persisted to localStorage */}
        <div className="space-y-2">
          <Label htmlFor="gc-org-id">
            {t('sections.gitCredentials.orgIdLabel', 'Organization ID')}
          </Label>
          <Input
            id="gc-org-id"
            value={orgId}
            placeholder="uuid-of-your-org"
            onChange={(e) => {
              const v = e.target.value;
              setOrgId(v);
              try {
                if (v) localStorage.setItem(ORG_STORAGE_KEY, v);
                else localStorage.removeItem(ORG_STORAGE_KEY);
              } catch {
                // localStorage may be disabled — non-fatal
              }
            }}
            className="font-mono text-sm"
          />
          <p className="text-xs text-muted-foreground">
            {t(
              'sections.gitCredentials.orgIdHelp',
              "Your organization's UUID. Find it in any org URL or via the orgs API. Multi-org picker lands with Epic #35.",
            )}
          </p>
        </div>

        {error && (
          <div className="text-sm text-destructive bg-destructive/10 rounded-lg p-3">{error}</div>
        )}

        {/* Existing credentials list */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-foreground">
              {t('sections.gitCredentials.existing', 'Existing credentials')}
              {credentials.length > 0 && (
                <span className="ml-2 text-xs text-muted-foreground">({credentials.length})</span>
              )}
            </h4>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setFormOpen((open) => !open)}
              disabled={!orgId}
            >
              <Plus className="h-3 w-3 mr-1" />
              {t('sections.gitCredentials.add', 'Add credential')}
            </Button>
          </div>

          {loading ? (
            <div className="flex items-center text-sm text-muted-foreground py-4">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              {t('sections.gitCredentials.loading', 'Loading credentials…')}
            </div>
          ) : credentials.length === 0 ? (
            <div className="text-sm text-muted-foreground py-4 italic">
              {t('sections.gitCredentials.empty', 'No credentials stored yet.')}
            </div>
          ) : (
            <div className="rounded-md border border-border divide-y divide-border">
              {credentials.map((cred) => (
                <div key={cred.id} className="p-3 flex items-center gap-3">
                  <KeyRound className="h-4 w-4 text-muted-foreground shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-foreground truncate">{cred.name}</div>
                    <div className="text-xs text-muted-foreground truncate">
                      {cred.host || t('sections.gitCredentials.anyHost', 'any host')} · {cred.kind} ·
                      {' '}{t('sections.gitCredentials.created', 'created')} {new Date(cred.created_at).toLocaleDateString()}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      void navigator.clipboard.writeText(cred.id);
                    }}
                    title={t('sections.gitCredentials.copyId', 'Copy credential ID')}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <Copy className="h-3 w-3" />
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleDelete(cred)}
                    className="text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* New credential form */}
        {formOpen && (
          <div className="rounded-lg border border-border p-4 space-y-3 bg-muted/30">
            <div className="flex items-center gap-2">
              <GitBranch className="h-4 w-4 text-muted-foreground" />
              <h4 className="text-sm font-semibold">
                {t('sections.gitCredentials.newCredential', 'New credential')}
              </h4>
            </div>
            <div className="space-y-2">
              <Label htmlFor="gc-name">{t('sections.gitCredentials.name', 'Name')}</Label>
              <Input
                id="gc-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="github-deploy-bot"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-2">
                <Label htmlFor="gc-host">{t('sections.gitCredentials.host', 'Host (optional)')}</Label>
                <Input
                  id="gc-host"
                  value={host}
                  onChange={(e) => setHost(e.target.value)}
                  placeholder="github.com"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="gc-username">
                  {t('sections.gitCredentials.username', 'Username (optional)')}
                </Label>
                <Input
                  id="gc-username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="oauth2"
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="gc-token">
                {t('sections.gitCredentials.token', 'Personal Access Token')}
              </Label>
              <div className="relative">
                <Input
                  id="gc-token"
                  type={showToken ? 'text' : 'password'}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="ghp_…"
                  className="pr-10 font-mono text-sm"
                />
                <button
                  type="button"
                  onClick={() => setShowToken(!showToken)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              <p className="text-xs text-muted-foreground">
                {t(
                  'sections.gitCredentials.tokenHelp',
                  'Token is encrypted at rest. After save it cannot be retrieved again — copy elsewhere if you need a backup.',
                )}
              </p>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" size="sm" onClick={() => setFormOpen(false)} disabled={submitting}>
                {t('sections.gitCredentials.cancel', 'Cancel')}
              </Button>
              <Button size="sm" onClick={handleCreate} disabled={submitting || !name.trim() || !token.trim()}>
                {submitting ? (
                  <>
                    <Loader2 className="h-3 w-3 animate-spin mr-1" />
                    {t('sections.gitCredentials.saving', 'Saving…')}
                  </>
                ) : (
                  t('sections.gitCredentials.save', 'Save credential')
                )}
              </Button>
            </div>
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
