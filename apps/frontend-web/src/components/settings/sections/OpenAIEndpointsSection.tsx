import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Plus,
  Trash2,
  Pencil,
  X,
  Check,
  Loader2,
  Activity,
  Eye,
  EyeOff,
  Globe,
} from 'lucide-react';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { get, post, put, del } from '../../../lib/api-client';
import { cn } from '../../../lib/utils';

interface LLMEndpoint {
  id: string;
  label: string;
  base_url: string;
  api_key_preview: string | null;
  default_model: string;
  headers: Record<string, string> | null;
  created_at: string;
  updated_at: string;
}

interface TestResult {
  ok: boolean;
  status_code: number | null;
  models: string[];
  error: string | null;
}

interface FormState {
  label: string;
  base_url: string;
  api_key: string;
  default_model: string;
}

const EMPTY_FORM: FormState = {
  label: '',
  base_url: '',
  api_key: '',
  default_model: '',
};

interface OpenAIEndpointsSectionProps {
  isOpen: boolean;
}

export function OpenAIEndpointsSection({ isOpen }: OpenAIEndpointsSectionProps) {
  const { t } = useTranslation('settings');

  const [endpoints, setEndpoints] = useState<LLMEndpoint[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [isAdding, setIsAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [showApiKey, setShowApiKey] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  const loadEndpoints = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const result = await get<LLMEndpoint[]>('/llm-endpoints');
    if (result.success && result.data) {
      setEndpoints(result.data);
    } else {
      setError(result.error || 'Failed to load endpoints');
    }
    setIsLoading(false);
  }, []);

  useEffect(() => {
    if (isOpen) {
      loadEndpoints();
    }
  }, [isOpen, loadEndpoints]);

  const resetForm = () => {
    setForm(EMPTY_FORM);
    setShowApiKey(false);
    setIsAdding(false);
    setEditingId(null);
  };

  const startEdit = (endpoint: LLMEndpoint) => {
    setForm({
      label: endpoint.label,
      base_url: endpoint.base_url,
      api_key: '',  // never prefill — masked on backend
      default_model: endpoint.default_model,
    });
    setEditingId(endpoint.id);
    setIsAdding(false);
  };

  const handleSave = async () => {
    if (!form.label.trim() || !form.base_url.trim() || !form.default_model.trim()) {
      return;
    }

    setIsSaving(true);
    setError(null);

    const body: Record<string, unknown> = {
      label: form.label.trim(),
      base_url: form.base_url.trim(),
      default_model: form.default_model.trim(),
    };
    // Only send api_key when the user actually typed something.
    // For edits, an empty string leaves the existing key untouched.
    if (form.api_key.trim()) {
      body.api_key = form.api_key.trim();
    } else if (!editingId) {
      body.api_key = null;
    }

    const result = editingId
      ? await put<LLMEndpoint>(`/llm-endpoints/${editingId}`, body)
      : await post<LLMEndpoint>('/llm-endpoints', body);

    if (result.success) {
      await loadEndpoints();
      resetForm();
    } else {
      setError(result.error || 'Failed to save endpoint');
    }
    setIsSaving(false);
  };

  const handleDelete = async (id: string) => {
    if (!confirm(t('openaiEndpoints.confirmDelete', 'Delete this endpoint?'))) {
      return;
    }
    const result = await del(`/llm-endpoints/${id}`);
    if (result.success) {
      await loadEndpoints();
    } else {
      setError(result.error || 'Failed to delete endpoint');
    }
  };

  const handleTest = async (id: string) => {
    setTestingId(id);
    const result = await post<TestResult>(`/llm-endpoints/${id}/test`, {});
    if (result.success && result.data) {
      setTestResults((prev) => ({ ...prev, [id]: result.data! }));
    } else {
      setTestResults((prev) => ({
        ...prev,
        [id]: {
          ok: false,
          status_code: null,
          models: [],
          error: result.error || 'Test failed',
        },
      }));
    }
    setTestingId(null);
  };

  const handleTestForm = async () => {
    if (!form.base_url.trim()) return;
    setTestingId('__form__');
    const result = await post<TestResult>('/llm-endpoints/test', {
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim() || null,
    });
    if (result.success && result.data) {
      setTestResults((prev) => ({ ...prev, __form__: result.data! }));
      // Auto-fill default model when empty and the server returned options
      if (!form.default_model.trim() && result.data.models.length > 0) {
        setForm((prev) => ({ ...prev, default_model: result.data!.models[0] }));
      }
    } else {
      setTestResults((prev) => ({
        ...prev,
        __form__: {
          ok: false,
          status_code: null,
          models: [],
          error: result.error || 'Test failed',
        },
      }));
    }
    setTestingId(null);
  };

  return (
    <div className="space-y-4 pt-6 border-t border-border">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Globe className="h-4 w-4 text-muted-foreground" />
          <h4 className="text-sm font-semibold text-foreground">
            {t('openaiEndpoints.title', 'OpenAI-Compatible Endpoints')}
          </h4>
        </div>
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
            {t('openaiEndpoints.add', 'Add endpoint')}
          </Button>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        {t(
          'openaiEndpoints.description',
          'Connect to LM Studio, vLLM, OpenRouter, Together, Groq, or any service implementing the OpenAI /v1/chat/completions protocol.'
        )}
      </p>

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
              <Label htmlFor="ep-label">
                {t('openaiEndpoints.label', 'Label')}
              </Label>
              <Input
                id="ep-label"
                placeholder="LM Studio (local)"
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="ep-model">
                {t('openaiEndpoints.defaultModel', 'Default model')}
              </Label>
              <Input
                id="ep-model"
                list="ep-model-options"
                placeholder="qwen2.5-coder-32b"
                value={form.default_model}
                onChange={(e) =>
                  setForm({ ...form, default_model: e.target.value })
                }
              />
              {testResults.__form__?.ok &&
                testResults.__form__.models.length > 0 && (
                  <datalist id="ep-model-options">
                    {testResults.__form__.models.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                )}
              {testResults.__form__?.ok &&
                testResults.__form__.models.length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {t(
                      'openaiEndpoints.modelsHint',
                      '{{count}} models discovered — click the field or start typing to pick one.',
                      { count: testResults.__form__.models.length }
                    )}
                  </p>
                )}
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="ep-url">
              {t('openaiEndpoints.baseUrl', 'Base URL')}
            </Label>
            <Input
              id="ep-url"
              placeholder="http://localhost:1234"
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              {t(
                'openaiEndpoints.baseUrlHint',
                'Without the /v1 suffix — we append it automatically.'
              )}
            </p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="ep-key">
              {t('openaiEndpoints.apiKey', 'API key')}{' '}
              <span className="text-xs text-muted-foreground">
                {t('openaiEndpoints.apiKeyOptional', '(optional)')}
              </span>
            </Label>
            <div className="relative">
              <Input
                id="ep-key"
                type={showApiKey ? 'text' : 'password'}
                placeholder={
                  editingId
                    ? t(
                        'openaiEndpoints.apiKeyEditPlaceholder',
                        'Leave blank to keep existing key'
                      )
                    : 'sk-...'
                }
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                className="pr-10"
              />
              <button
                type="button"
                onClick={() => setShowApiKey(!showApiKey)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                {showApiKey ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>

          {testResults.__form__ && (
            <TestResultDisplay result={testResults.__form__} />
          )}

          <div className="flex items-center gap-2 pt-2">
            <Button
              size="sm"
              onClick={handleSave}
              disabled={
                isSaving ||
                !form.label.trim() ||
                !form.base_url.trim() ||
                !form.default_model.trim()
              }
            >
              {isSaving ? (
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              ) : (
                <Check className="h-3 w-3 mr-1" />
              )}
              {editingId
                ? t('openaiEndpoints.save', 'Save')
                : t('openaiEndpoints.create', 'Create')}
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
              {t('openaiEndpoints.test', 'Test')}
            </Button>
            <Button variant="ghost" size="sm" onClick={resetForm}>
              <X className="h-3 w-3 mr-1" />
              {t('openaiEndpoints.cancel', 'Cancel')}
            </Button>
          </div>
        </div>
      )}

      {/* Endpoint list */}
      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
          <Loader2 className="h-3 w-3 animate-spin" />
          {t('openaiEndpoints.loading', 'Loading endpoints…')}
        </div>
      ) : endpoints.length === 0 && !isAdding ? (
        <p className="text-sm text-muted-foreground py-4 italic">
          {t(
            'openaiEndpoints.empty',
            'No endpoints yet. Click "Add endpoint" to connect one.'
          )}
        </p>
      ) : (
        <div className="space-y-2">
          {endpoints.map((endpoint) => (
            <div
              key={endpoint.id}
              className={cn(
                'rounded-lg border transition-colors border-success/30 bg-success/5',
                editingId === endpoint.id && 'opacity-50'
              )}
            >
              <div className="flex items-center justify-between p-3">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <div className="h-7 w-7 rounded-full flex items-center justify-center shrink-0 bg-success/20 text-success">
                    <Globe className="h-3.5 w-3.5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-sm text-foreground">
                      {endpoint.label}
                    </div>
                    <div className="text-xs text-muted-foreground truncate">
                      {endpoint.base_url} · {endpoint.default_model}
                    </div>
                    {endpoint.api_key_preview && (
                      <div className="text-xs text-muted-foreground font-mono">
                        {endpoint.api_key_preview}
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleTest(endpoint.id)}
                    disabled={testingId === endpoint.id}
                    title={t('openaiEndpoints.test', 'Test')}
                  >
                    {testingId === endpoint.id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Activity className="h-3 w-3" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => startEdit(endpoint)}
                    title={t('openaiEndpoints.edit', 'Edit')}
                  >
                    <Pencil className="h-3 w-3" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleDelete(endpoint.id)}
                    title={t('openaiEndpoints.delete', 'Delete')}
                  >
                    <Trash2 className="h-3 w-3 text-destructive" />
                  </Button>
                </div>
              </div>
              {testResults[endpoint.id] && (
                <div className="px-3 pb-3">
                  <TestResultDisplay result={testResults[endpoint.id]} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TestResultDisplay({ result }: { result: TestResult }) {
  const { t } = useTranslation('settings');
  if (result.ok) {
    return (
      <div className="text-xs bg-green-500/10 text-green-700 dark:text-green-400 p-2 rounded space-y-1">
        <div className="flex items-center gap-1 font-medium">
          <Check className="h-3 w-3" />
          {t('openaiEndpoints.testSuccess', 'Connection successful')}
          {result.status_code && ` (HTTP ${result.status_code})`}
        </div>
        {result.models.length > 0 && (
          <div className="font-mono text-[10px] opacity-75">
            {t('openaiEndpoints.modelsFound', '{{count}} models', {
              count: result.models.length,
            })}
            : {result.models.slice(0, 5).join(', ')}
            {result.models.length > 5 && '…'}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="text-xs bg-destructive/10 text-destructive p-2 rounded flex items-center gap-1">
      <X className="h-3 w-3" />
      {result.error || t('openaiEndpoints.testFailed', 'Connection failed')}
    </div>
  );
}
