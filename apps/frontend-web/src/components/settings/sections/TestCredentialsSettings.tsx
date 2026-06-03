/**
 * Test-target Credentials settings panel (#107 task 7).
 *
 * Store the logins a generated test uses to authenticate against the
 * system-under-test (ServiceNow, Salesforce, a staging app, …) so secrets
 * never live in test files. Mirrors GitCredentialsSettings: org-scoped,
 * encrypted at rest, and the secret is NEVER returned after creation.
 *
 * Backed by /api/test-credentials (routes/test_target_credentials.py). A
 * stored credential is referenced from .pfactory.yml via `test_credentials:`
 * + `auth: { type: ref }`, and only injected on egress lanes when a subtask
 * sets `requires_auth` — see guides/test-target-auth.md.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, Plus, Trash2, Loader2, Info, Copy, Eye, EyeOff, ShieldCheck } from 'lucide-react';
import { get, post, del } from '../../../lib/api-client';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { SettingsSection } from '../SettingsSection';

const ORG_STORAGE_KEY = 'pfactory.testCredentials.orgId';

// Mirrors _VALID_KINDS in the backend route.
const KINDS = ['form', 'api_token', 'basic_auth', 'totp'] as const;
type Kind = (typeof KINDS)[number];

interface TestCredential {
  id: string;
  org_id: string;
  name: string;
  kind: string;
  username: string | null;
  created_at: string;
  last_used_at: string | null;
}

export function TestCredentialsSettings() {
  const { t } = useTranslation('settings');

  // org_id entered once, persisted to localStorage (same pattern as the Git
  // credentials panel; a multi-org picker lands with Epic #35).
  const [orgId, setOrgId] = useState<string>(() => {
    try {
      return localStorage.getItem(ORG_STORAGE_KEY) || '';
    } catch {
      return '';
    }
  });

  const [credentials, setCredentials] = useState<TestCredential[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState('');
  const [kind, setKind] = useState<Kind>('form');
  const [username, setUsername] = useState('');
  const [secret, setSecret] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    if (!orgId) {
      setError(t('sections.testCredentials.noOrg', 'No organization configured yet — enter your org ID above.'));
      return;
    }
    setLoading(true);
    setError(null);
    const res = await get<TestCredential[]>(
      `/test-credentials?org_id=${encodeURIComponent(orgId)}`
    );
    if (res.success && res.data) setCredentials(res.data);
    else setError(res.error || 'Failed to load credentials');
    setLoading(false);
  }, [orgId, t]);

  useEffect(() => {
    if (orgId) void load();
  }, [orgId, load]);

  const handleCreate = async () => {
    if (!orgId) return;
    if (!name.trim() || !secret.trim()) {
      setError(t('sections.testCredentials.fieldsRequired', 'Name and secret are required.'));
      return;
    }
    setSubmitting(true);
    setError(null);
    const res = await post<TestCredential>('/test-credentials', {
      org_id: orgId,
      name: name.trim(),
      kind,
      ...(username.trim() ? { username: username.trim() } : {}),
      secret: secret.trim(),
    });
    if (res.success) {
      // The secret is now durable on the backend; we never see it again.
      setName('');
      setKind('form');
      setUsername('');
      setSecret('');
      setShowSecret(false);
      setFormOpen(false);
      await load();
    } else {
      setError(res.error || 'Failed to create credential');
    }
    setSubmitting(false);
  };

  const handleDelete = async (cred: TestCredential) => {
    if (!confirm(t('sections.testCredentials.confirmDelete', `Delete credential "${cred.name}"? This cannot be undone.`))) {
      return;
    }
    setError(null);
    const res = await del(`/test-credentials/${cred.id}`);
    if (res.success) await load();
    else setError(res.error || 'Failed to delete credential');
  };

  return (
    <SettingsSection
      title={t('sections.testCredentials.title', 'Test Credentials')}
      description={t(
        'sections.testCredentials.description',
        'Logins your generated tests use to authenticate against the system under test. Encrypted at rest.',
      )}
    >
      <div className="space-y-4">
        <div className="rounded-lg bg-info/10 border border-info/30 p-3 flex items-start gap-2">
          <Info className="h-4 w-4 text-info shrink-0 mt-0.5" />
          <p className="text-xs text-muted-foreground">
            {t(
              'sections.testCredentials.info',
              'Reference a stored credential from .pfactory.yml (test_credentials + auth: { type: ref }). It is injected only on egress lanes when a subtask sets requires_auth, and scrubbed from every log and artifact. Secrets are encrypted at rest and never returned after creation.',
            )}
          </p>
        </div>

        {/* Org ID input — required once, persisted to localStorage */}
        <div className="space-y-2">
          <Label htmlFor="tc-org-id">
            {t('sections.testCredentials.orgIdLabel', 'Organization ID')}
          </Label>
          <Input
            id="tc-org-id"
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
              'sections.testCredentials.orgIdHelp',
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
              {t('sections.testCredentials.existing', 'Stored credentials')}
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
              {t('sections.testCredentials.add', 'Add credential')}
            </Button>
          </div>

          {loading ? (
            <div className="flex items-center text-sm text-muted-foreground py-4">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              {t('sections.testCredentials.loading', 'Loading credentials…')}
            </div>
          ) : credentials.length === 0 ? (
            <div className="text-sm text-muted-foreground py-4 italic">
              {t('sections.testCredentials.empty', 'No credentials stored yet.')}
            </div>
          ) : (
            <div className="rounded-md border border-border divide-y divide-border">
              {credentials.map((cred) => (
                <div key={cred.id} className="p-3 flex items-center gap-3">
                  <KeyRound className="h-4 w-4 text-muted-foreground shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-foreground truncate">{cred.name}</div>
                    <div className="text-xs text-muted-foreground truncate">
                      {cred.username || t('sections.testCredentials.noUser', 'no username')} · {cred.kind} ·
                      {' '}{t('sections.testCredentials.created', 'created')} {new Date(cred.created_at).toLocaleDateString()}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      void navigator.clipboard.writeText(cred.name);
                    }}
                    title={t('sections.testCredentials.copyName', 'Copy name (reference it in .pfactory.yml)')}
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
              <ShieldCheck className="h-4 w-4 text-muted-foreground" />
              <h4 className="text-sm font-semibold">
                {t('sections.testCredentials.newCredential', 'New credential')}
              </h4>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-2">
                <Label htmlFor="tc-name">{t('sections.testCredentials.name', 'Name')}</Label>
                <Input
                  id="tc-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="servicenow-staging"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="tc-kind">{t('sections.testCredentials.kind', 'Kind')}</Label>
                <select
                  id="tc-kind"
                  value={kind}
                  onChange={(e) => setKind(e.target.value as Kind)}
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  {KINDS.map((k) => (
                    <option key={k} value={k}>{k}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="tc-username">
                {t('sections.testCredentials.username', 'Username (optional)')}
              </Label>
              <Input
                id="tc-username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="test.user@example.com"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="tc-secret">
                {t('sections.testCredentials.secret', 'Secret (password / API token / TOTP seed)')}
              </Label>
              <div className="relative">
                <Input
                  id="tc-secret"
                  type={showSecret ? 'text' : 'password'}
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  placeholder="••••••••"
                  className="pr-10 font-mono text-sm"
                />
                <button
                  type="button"
                  onClick={() => setShowSecret(!showSecret)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              <p className="text-xs text-muted-foreground">
                {t(
                  'sections.testCredentials.secretHelp',
                  'Encrypted at rest. After save it cannot be retrieved again — store a backup elsewhere if you need one.',
                )}
              </p>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" size="sm" onClick={() => setFormOpen(false)} disabled={submitting}>
                {t('sections.testCredentials.cancel', 'Cancel')}
              </Button>
              <Button size="sm" onClick={handleCreate} disabled={submitting || !name.trim() || !secret.trim()}>
                {submitting ? (
                  <>
                    <Loader2 className="h-3 w-3 animate-spin mr-1" />
                    {t('sections.testCredentials.saving', 'Saving…')}
                  </>
                ) : (
                  t('sections.testCredentials.save', 'Save credential')
                )}
              </Button>
            </div>
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
