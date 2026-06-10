/**
 * Docs-target connections settings panel (plan→docs P4b).
 *
 * Optional Backstage / Confluence sinks for the plan→docs emit. The plan is
 * always written to the repo as Markdown/TechDocs; a connection here adds a
 * remote target. ``enabled_by_default`` makes it part of the default emit set
 * (a per-plan picker can still override). User-scoped, the API token encrypted
 * at rest and never returned after creation.
 *
 * Backed by /api/docs-targets (routes/docs_targets.py). The whole docs emit is
 * gated behind PFACTORY_DOCS_EMIT on the server — off by default.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  BookOpen,
  Plus,
  Trash2,
  Pencil,
  X,
  Check,
  Loader2,
  Activity,
  Eye,
  EyeOff,
} from 'lucide-react';
import { get, post, put, del } from '../../../lib/api-client';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { SettingsSection } from '../SettingsSection';
import { cn } from '../../../lib/utils';

const KINDS = ['backstage', 'confluence'] as const;
type Kind = (typeof KINDS)[number];

interface DocsTarget {
  id: string;
  kind: string;
  label: string;
  base_url: string;
  api_token_preview: string | null;
  space: string | null;
  enabled_by_default: boolean;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

interface TestResult {
  ok: boolean;
  status_code: number | null;
  detail: string | null;
  error: string | null;
}

interface FormState {
  kind: Kind;
  label: string;
  base_url: string;
  api_token: string;
  space: string;
  enabled_by_default: boolean;
}

const EMPTY_FORM: FormState = {
  kind: 'backstage',
  label: '',
  base_url: '',
  api_token: '',
  space: '',
  enabled_by_default: false,
};

export function DocsTargetsSettings() {
  const { t } = useTranslation('settings');

  const [targets, setTargets] = useState<DocsTarget[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [isAdding, setIsAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [showToken, setShowToken] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  const loadTargets = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const result = await get<DocsTarget[]>('/docs-targets');
    if (result.success && result.data) {
      setTargets(result.data);
    } else {
      setError(result.error || 'Failed to load docs targets');
    }
    setIsLoading(false);
  }, []);

  useEffect(() => {
    loadTargets();
  }, [loadTargets]);

  const resetForm = () => {
    setForm(EMPTY_FORM);
    setShowToken(false);
    setIsAdding(false);
    setEditingId(null);
  };

  const startEdit = (target: DocsTarget) => {
    setForm({
      kind: (KINDS as readonly string[]).includes(target.kind)
        ? (target.kind as Kind)
        : 'backstage',
      label: target.label,
      base_url: target.base_url,
      api_token: '', // never prefill — masked on backend
      space: target.space || '',
      enabled_by_default: target.enabled_by_default,
    });
    setEditingId(target.id);
    setIsAdding(false);
  };

  const handleSave = async () => {
    if (!form.label.trim() || !form.base_url.trim()) {
      return;
    }
    setIsSaving(true);
    setError(null);

    const body: Record<string, unknown> = {
      kind: form.kind,
      label: form.label.trim(),
      base_url: form.base_url.trim(),
      space: form.kind === 'confluence' ? form.space.trim() || null : null,
      enabled_by_default: form.enabled_by_default,
    };
    // Only send api_token when typed. For edits, blank keeps the existing one.
    if (form.api_token.trim()) {
      body.api_token = form.api_token.trim();
    } else if (!editingId) {
      body.api_token = null;
    }

    const result = editingId
      ? await put<DocsTarget>(`/docs-targets/${editingId}`, body)
      : await post<DocsTarget>('/docs-targets', body);

    if (result.success) {
      await loadTargets();
      resetForm();
    } else {
      setError(result.error || 'Failed to save docs target');
    }
    setIsSaving(false);
  };

  const handleDelete = async (id: string) => {
    if (!confirm(t('docsTargets.confirmDelete', 'Delete this docs target?'))) {
      return;
    }
    const result = await del(`/docs-targets/${id}`);
    if (result.success) {
      await loadTargets();
    } else {
      setError(result.error || 'Failed to delete docs target');
    }
  };

  const handleTest = async (id: string) => {
    setTestingId(id);
    const result = await post<TestResult>(`/docs-targets/${id}/test`, {});
    setTestResults((prev) => ({
      ...prev,
      [id]:
        result.success && result.data
          ? result.data
          : { ok: false, status_code: null, detail: null, error: result.error || 'Test failed' },
    }));
    setTestingId(null);
  };

  const handleTestForm = async () => {
    if (!form.base_url.trim()) return;
    setTestingId('__form__');
    const result = await post<TestResult>('/docs-targets/test', {
      kind: form.kind,
      base_url: form.base_url.trim(),
      api_token: form.api_token.trim() || null,
      space: form.kind === 'confluence' ? form.space.trim() || null : null,
    });
    setTestResults((prev) => ({
      ...prev,
      __form__:
        result.success && result.data
          ? result.data
          : { ok: false, status_code: null, detail: null, error: result.error || 'Test failed' },
    }));
    setTestingId(null);
  };

  return (
    <SettingsSection
      title={t('docsTargets.title', 'Documentation Targets')}
      description={t(
        'docsTargets.description',
        'Optional Backstage / Confluence sinks for the plan→docs export. Plans are always written to the repo as Markdown/TechDocs; a connection here adds a remote target.'
      )}
    >
      <div className="space-y-4">
        <div className="flex items-center justify-end">
          {!isAdding && !editingId && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setIsAdding(true);
                setForm(EMPTY_FORM);
              }}
            >
              <Plus className="h-3 w-3 mr-1" />
              {t('docsTargets.add', 'Add target')}
            </Button>
          )}
        </div>

        {error && (
          <div className="text-sm text-destructive bg-destructive/10 p-2 rounded">
            {error}
          </div>
        )}

        {/* Add/Edit form */}
        {(isAdding || editingId) && (
          <div className="border border-border rounded-md p-4 space-y-3 bg-muted/30">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="dt-kind">{t('docsTargets.kind', 'Type')}</Label>
                <select
                  id="dt-kind"
                  className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm"
                  value={form.kind}
                  onChange={(e) =>
                    setForm({ ...form, kind: e.target.value as Kind })
                  }
                >
                  <option value="backstage">Backstage</option>
                  <option value="confluence">Confluence</option>
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="dt-label">{t('docsTargets.label', 'Label')}</Label>
                <Input
                  id="dt-label"
                  placeholder="Engineering Backstage"
                  value={form.label}
                  onChange={(e) => setForm({ ...form, label: e.target.value })}
                />
              </div>
            </div>

            <div className="space-y-1">
              <Label htmlFor="dt-url">{t('docsTargets.baseUrl', 'Base URL')}</Label>
              <Input
                id="dt-url"
                placeholder={
                  form.kind === 'backstage'
                    ? 'https://backstage.example.com'
                    : 'https://example.atlassian.net/wiki'
                }
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              />
            </div>

            {form.kind === 'confluence' && (
              <div className="space-y-1">
                <Label htmlFor="dt-space">
                  {t('docsTargets.space', 'Space key')}
                </Label>
                <Input
                  id="dt-space"
                  placeholder="ENG"
                  value={form.space}
                  onChange={(e) => setForm({ ...form, space: e.target.value })}
                />
              </div>
            )}

            <div className="space-y-1">
              <Label htmlFor="dt-token">
                {t('docsTargets.apiToken', 'API token')}{' '}
                <span className="text-xs text-muted-foreground">
                  {form.kind === 'confluence'
                    ? t('docsTargets.apiTokenRequired', '(required)')
                    : t('docsTargets.apiTokenOptional', '(optional)')}
                </span>
              </Label>
              <div className="relative">
                <Input
                  id="dt-token"
                  type={showToken ? 'text' : 'password'}
                  placeholder={
                    editingId
                      ? t('docsTargets.apiTokenEditPlaceholder', 'Leave blank to keep existing token')
                      : 'token…'
                  }
                  value={form.api_token}
                  onChange={(e) => setForm({ ...form, api_token: e.target.value })}
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowToken(!showToken)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm text-foreground">
              <input
                type="checkbox"
                checked={form.enabled_by_default}
                onChange={(e) =>
                  setForm({ ...form, enabled_by_default: e.target.checked })
                }
              />
              {t('docsTargets.enabledByDefault', 'Use by default for new plans')}
            </label>

            {testResults.__form__ && (
              <TestResultDisplay result={testResults.__form__} />
            )}

            <div className="flex items-center gap-2 pt-2">
              <Button
                size="sm"
                onClick={handleSave}
                disabled={isSaving || !form.label.trim() || !form.base_url.trim()}
              >
                {isSaving ? (
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                ) : (
                  <Check className="h-3 w-3 mr-1" />
                )}
                {editingId
                  ? t('docsTargets.save', 'Save')
                  : t('docsTargets.create', 'Create')}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={handleTestForm}
                disabled={!form.base_url.trim() || testingId === '__form__'}
              >
                {testingId === '__form__' ? (
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                ) : (
                  <Activity className="h-3 w-3 mr-1" />
                )}
                {t('docsTargets.test', 'Test')}
              </Button>
              <Button variant="ghost" size="sm" onClick={resetForm}>
                <X className="h-3 w-3 mr-1" />
                {t('docsTargets.cancel', 'Cancel')}
              </Button>
            </div>
          </div>
        )}

        {/* Target list */}
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
            <Loader2 className="h-3 w-3 animate-spin" />
            {t('docsTargets.loading', 'Loading docs targets…')}
          </div>
        ) : targets.length === 0 && !isAdding ? (
          <p className="text-sm text-muted-foreground py-4 italic">
            {t(
              'docsTargets.empty',
              'No docs targets yet. Plans export to the repo by default; add Backstage or Confluence to also publish there.'
            )}
          </p>
        ) : (
          <div className="space-y-2">
            {targets.map((target) => (
              <div
                key={target.id}
                className={cn(
                  'rounded-lg border transition-colors',
                  target.enabled_by_default
                    ? 'border-success/30 bg-success/5'
                    : 'border-border',
                  editingId === target.id && 'opacity-50'
                )}
              >
                <div className="flex items-center justify-between p-3">
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className="h-7 w-7 rounded-full flex items-center justify-center shrink-0 bg-muted text-muted-foreground">
                      <BookOpen className="h-3.5 w-3.5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-sm text-foreground flex items-center gap-2">
                        {target.label}
                        <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                          {target.kind}
                        </span>
                        {target.enabled_by_default && (
                          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-success/20 text-success">
                            {t('docsTargets.default', 'default')}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground truncate">
                        {target.base_url}
                        {target.space ? ` · ${target.space}` : ''}
                      </div>
                      {target.api_token_preview && (
                        <div className="text-xs text-muted-foreground font-mono">
                          {target.api_token_preview}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleTest(target.id)}
                      disabled={testingId === target.id}
                      title={t('docsTargets.test', 'Test')}
                    >
                      {testingId === target.id ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Activity className="h-3 w-3" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => startEdit(target)}
                      title={t('docsTargets.edit', 'Edit')}
                    >
                      <Pencil className="h-3 w-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(target.id)}
                      title={t('docsTargets.delete', 'Delete')}
                    >
                      <Trash2 className="h-3 w-3 text-destructive" />
                    </Button>
                  </div>
                </div>
                {testResults[target.id] && (
                  <div className="px-3 pb-3">
                    <TestResultDisplay result={testResults[target.id]} />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </SettingsSection>
  );
}

function TestResultDisplay({ result }: { result: TestResult }) {
  const { t } = useTranslation('settings');
  if (result.ok) {
    return (
      <div className="text-xs bg-green-500/10 text-green-700 dark:text-green-400 p-2 rounded flex items-center gap-1 font-medium">
        <Check className="h-3 w-3" />
        {result.detail || t('docsTargets.testSuccess', 'Reachable')}
        {result.status_code && ` (HTTP ${result.status_code})`}
      </div>
    );
  }
  return (
    <div className="text-xs bg-destructive/10 text-destructive p-2 rounded flex items-center gap-1">
      <X className="h-3 w-3" />
      {result.error || t('docsTargets.testFailed', 'Connection failed')}
      {result.status_code ? ` (HTTP ${result.status_code})` : ''}
    </div>
  );
}
