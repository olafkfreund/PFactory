/**
 * API Keys settings page (Issue #154).
 *
 * Mint / list / revoke scope-gated ``acw_`` API keys that the stdio
 * MCP client picks up via ``$PFACTORY_MCP_KEY`` (or
 * ``~/.pfactory/.mcp-key``) instead of the host-wide admin token at
 * ``~/.pfactory/.token``.
 *
 * The raw key plaintext is shown ONCE at creation time — the backend
 * stores only a SHA-256 hash + 8-char preview, so losing the key means
 * revoking it and minting a new one. Mirrors the GitCredentialsSettings
 * UX exactly so the mental model carries over.
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  KeyRound,
  Plus,
  Trash2,
  Loader2,
  Info,
  Copy,
  Check,
} from 'lucide-react';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { SettingsSection } from '../SettingsSection';
import type {
  ApiKeySummary,
  CreateApiKeyBody,
  CreateApiKeyResponse,
} from '../../../shared/types/ipc';

const ORG_STORAGE_KEY = 'pfactory.apiKeys.orgId';

// Scopes shipped in PR #162 (mcp_stdio proxy).
const AVAILABLE_SCOPES: Array<{ id: string; description: string }> = [
  { id: 'mcp:read', description: 'List + status + logs (read-only tools)' },
  { id: 'project:write', description: 'Create new projects' },
  { id: 'task:write', description: 'Start / stop / recover / approve tasks' },
  { id: 'task:merge', description: 'Create PRs + merge worktrees (high blast radius)' },
];

export function McpApiKeysSettings() {
  const { t } = useTranslation('settings');

  // Same org_id-from-localStorage pattern as GitCredentialsSettings. A
  // real multi-tenant org picker lands with Epic #35.
  const [orgId, setOrgId] = useState<string>(() => {
    try {
      return localStorage.getItem(ORG_STORAGE_KEY) || '';
    } catch {
      return '';
    }
  });

  const [keys, setKeys] = useState<ApiKeySummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mint form state
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState('');
  const [selectedScopes, setSelectedScopes] = useState<Set<string>>(
    new Set(['mcp:read']),
  );
  const [expiresInDays, setExpiresInDays] = useState<string>(''); // "" = never
  const [submitting, setSubmitting] = useState(false);

  // One-time post-creation reveal
  const [revealedKey, setRevealedKey] = useState<CreateApiKeyResponse | null>(
    null,
  );
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await window.API.listApiKeys();
      if (result.success && result.data) {
        // GET /api/keys returns ALL of the current user's keys across orgs;
        // filter to the active org so the list matches what minting here
        // would create.
        const filtered = orgId
          ? result.data.filter((k) => k.org_id === orgId)
          : result.data;
        setKeys(filtered);
      } else {
        setError(result.error || 'Failed to load API keys');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load API keys');
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleScope = (id: string) => {
    setSelectedScopes((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleCreate = async () => {
    if (!orgId) {
      setError(t('sections.apiKeys.noOrg', 'Enter an organization ID first.'));
      return;
    }
    if (!name.trim()) {
      setError(t('sections.apiKeys.nameRequired', 'Name is required.'));
      return;
    }
    if (selectedScopes.size === 0) {
      setError(t('sections.apiKeys.scopeRequired', 'Pick at least one scope.'));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body: CreateApiKeyBody = {
        org_id: orgId,
        name: name.trim(),
        scopes: Array.from(selectedScopes),
      };
      const expiresNum = parseInt(expiresInDays, 10);
      if (!Number.isNaN(expiresNum) && expiresNum > 0) {
        body.expires_in_days = expiresNum;
      }
      const result = await window.API.createApiKey(body);
      if (result.success && result.data) {
        // Stash the raw key for the one-time reveal panel. Reset the
        // form, but don't close the section — the revealed key panel
        // must be acknowledged before it disappears.
        setRevealedKey(result.data);
        setName('');
        setSelectedScopes(new Set(['mcp:read']));
        setExpiresInDays('');
        setFormOpen(false);
        await load();
      } else {
        setError(result.error || 'Failed to mint key');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to mint key');
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async (key: ApiKeySummary) => {
    if (
      !confirm(
        t(
          'sections.apiKeys.confirmRevoke',
          `Revoke key "${key.name}"? Any laptop or shell using this key will immediately lose access.`,
        ),
      )
    ) {
      return;
    }
    setError(null);
    try {
      const result = await window.API.revokeApiKey(key.id);
      if (result.success) {
        await load();
      } else {
        setError(result.error || 'Failed to revoke key');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to revoke key');
    }
  };

  const handleCopyRevealed = async () => {
    if (!revealedKey) return;
    try {
      await navigator.clipboard.writeText(revealedKey.raw_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API may be unavailable (e.g. non-HTTPS context).
      // The full key is still selectable from the read-only input.
    }
  };

  return (
    <SettingsSection
      title={t('sections.apiKeys.title', 'API Keys')}
      description={t(
        'sections.apiKeys.description',
        'Scoped acw_ keys for the stdio MCP control plane. Replaces the host-wide admin token for enterprise deployments.',
      )}
    >
      <div className="space-y-4">
        {/* Explainer */}
        <div className="rounded-lg bg-info/10 border border-info/30 p-3 flex items-start gap-2">
          <Info className="h-4 w-4 text-info shrink-0 mt-0.5" />
          <p className="text-xs text-muted-foreground">
            {t(
              'sections.apiKeys.info',
              'Mint a scoped key, then set $PFACTORY_MCP_KEY (or drop the value in ~/.pfactory/.mcp-key) on the laptop that runs the stdio MCP. The key is shown once at creation — copy it before closing the panel.',
            )}
          </p>
        </div>

        {/* Org ID input */}
        <div className="space-y-2">
          <Label htmlFor="ak-org-id">
            {t('sections.apiKeys.orgIdLabel', 'Organization ID')}
          </Label>
          <Input
            id="ak-org-id"
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
              'sections.apiKeys.orgIdHelp',
              "Your organization's UUID. Multi-org picker lands with Epic #35.",
            )}
          </p>
        </div>

        {error && (
          <div className="text-sm text-destructive bg-destructive/10 rounded-lg p-3">
            {error}
          </div>
        )}

        {/* Existing keys */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-foreground">
              {t('sections.apiKeys.existing', 'Active keys')}
              {keys.length > 0 && (
                <span className="ml-2 text-xs text-muted-foreground">
                  ({keys.length})
                </span>
              )}
            </h4>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setFormOpen((open) => !open)}
              disabled={!orgId}
            >
              <Plus className="h-3 w-3 mr-1" />
              {t('sections.apiKeys.mint', 'Mint key')}
            </Button>
          </div>

          {loading ? (
            <div className="flex items-center text-sm text-muted-foreground py-4">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              {t('sections.apiKeys.loading', 'Loading keys…')}
            </div>
          ) : keys.length === 0 ? (
            <div className="text-sm text-muted-foreground py-4 italic">
              {t('sections.apiKeys.empty', 'No keys minted yet.')}
            </div>
          ) : (
            <div className="rounded-md border border-border divide-y divide-border">
              {keys.map((key) => (
                <div key={key.id} className="p-3 flex items-center gap-3">
                  <KeyRound className="h-4 w-4 text-muted-foreground shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-foreground truncate">
                      {key.name}
                    </div>
                    <div className="text-xs text-muted-foreground truncate">
                      <span className="font-mono">{key.key_preview}…</span>
                      {' · '}
                      {key.scopes && key.scopes.length > 0
                        ? key.scopes.join(', ')
                        : t('sections.apiKeys.noScopes', 'no scopes')}
                      {' · '}
                      {t('sections.apiKeys.created', 'created')}{' '}
                      {new Date(key.created_at).toLocaleDateString()}
                      {key.expires_at && (
                        <>
                          {' · '}
                          {t('sections.apiKeys.expires', 'expires')}{' '}
                          {new Date(key.expires_at).toLocaleDateString()}
                        </>
                      )}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleRevoke(key)}
                    className="text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Mint form */}
        {formOpen && (
          <div className="rounded-lg border border-border p-4 space-y-3 bg-muted/30">
            <h4 className="text-sm font-semibold">
              {t('sections.apiKeys.newKey', 'Mint a new key')}
            </h4>
            <div className="space-y-2">
              <Label htmlFor="ak-name">
                {t('sections.apiKeys.name', 'Name')}
              </Label>
              <Input
                id="ak-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="laptop-ada"
              />
            </div>

            <div className="space-y-2">
              <Label>{t('sections.apiKeys.scopes', 'Scopes')}</Label>
              <div className="space-y-1">
                {AVAILABLE_SCOPES.map((scope) => (
                  <label
                    key={scope.id}
                    className="flex items-start gap-2 cursor-pointer hover:bg-muted/50 rounded px-2 py-1.5"
                  >
                    <input
                      type="checkbox"
                      checked={selectedScopes.has(scope.id)}
                      onChange={() => toggleScope(scope.id)}
                      className="mt-0.5"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-mono">{scope.id}</div>
                      <div className="text-xs text-muted-foreground">
                        {scope.description}
                      </div>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="ak-expires">
                {t('sections.apiKeys.expiresLabel', 'Expires in (days, optional)')}
              </Label>
              <Input
                id="ak-expires"
                type="number"
                min={1}
                max={365}
                value={expiresInDays}
                onChange={(e) => setExpiresInDays(e.target.value)}
                placeholder={t(
                  'sections.apiKeys.expiresPlaceholder',
                  'leave blank for no expiry',
                )}
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setFormOpen(false)}
                disabled={submitting}
              >
                {t('sections.apiKeys.cancel', 'Cancel')}
              </Button>
              <Button
                size="sm"
                onClick={handleCreate}
                disabled={
                  submitting || !name.trim() || selectedScopes.size === 0
                }
              >
                {submitting ? (
                  <>
                    <Loader2 className="h-3 w-3 animate-spin mr-1" />
                    {t('sections.apiKeys.minting', 'Minting…')}
                  </>
                ) : (
                  t('sections.apiKeys.mintConfirm', 'Mint key')
                )}
              </Button>
            </div>
          </div>
        )}

        {/* One-time-visible raw key panel */}
        {revealedKey && (
          <div className="rounded-lg border-2 border-warning bg-warning/10 p-4 space-y-3">
            <div className="flex items-start gap-2">
              <Info className="h-4 w-4 text-warning shrink-0 mt-0.5" />
              <div className="text-sm">
                <div className="font-semibold text-foreground">
                  {t(
                    'sections.apiKeys.copyOnce',
                    'Copy this key now — it will not be shown again.',
                  )}
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {t(
                    'sections.apiKeys.copyOnceHelp',
                    'The backend stores only a hash. If you lose the key you have to revoke and mint a new one.',
                  )}
                </div>
              </div>
            </div>
            <div className="relative">
              <Input
                value={revealedKey.raw_key}
                readOnly
                className="pr-10 font-mono text-sm"
                onFocus={(e) => e.target.select()}
              />
              <button
                type="button"
                onClick={handleCopyRevealed}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                aria-label={t('sections.apiKeys.copyKey', 'Copy key')}
              >
                {copied ? (
                  <Check className="h-4 w-4 text-success" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </button>
            </div>
            <div className="flex justify-end">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setRevealedKey(null)}
              >
                {t('sections.apiKeys.acknowledge', "I've copied it")}
              </Button>
            </div>
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
