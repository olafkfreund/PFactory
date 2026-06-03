import { useState } from 'react';
import { Server, Loader2, CheckCircle2, XCircle, ArrowRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { EgressBadge } from '../EgressBadge';
import { post } from '../../lib/api-client';

interface OpenAICompatSetupStepProps {
  onNext: () => void;
  onBack: () => void;
}

type TestStatus = 'idle' | 'testing' | 'success' | 'error';

interface TestResult {
  modelCount: number;
  message?: string;
}

/**
 * OpenAICompatSetupStep component for the onboarding wizard.
 *
 * Provides a form to configure an OpenAI-compatible server:
 * 1. Base URL input (placeholder 'http://localhost:1234')
 * 2. Optional API Key input
 * 3. 'Test Connection' button that calls POST /settings/openai-compat/test
 *    and displays success/failure with model count
 * 4. 'Continue' button enabled after a successful test (or can be skipped)
 *
 * On Continue: saves llmOpenaiBaseUrl and llmProvider='openai' to settings,
 * then calls onNext().
 *
 * Props:
 * - onNext(): called after the user clicks Continue and settings are saved
 * - onBack(): called when the user presses Back
 */
export function OpenAICompatSetupStep({ onNext, onBack }: OpenAICompatSetupStepProps) {
  const { t } = useTranslation(['onboarding', 'common']);

  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [testStatus, setTestStatus] = useState<TestStatus>('idle');
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const canContinue = testStatus === 'success';

  const handleTestConnection = async () => {
    if (!baseUrl.trim()) return;

    setTestStatus('testing');
    setTestResult(null);
    setTestError(null);

    try {
      const result = await post<{ success: boolean; modelCount?: number; error?: string }>(
        '/settings/openai-compat/test',
        {
          baseUrl: baseUrl.trim(),
          apiKey: apiKey.trim() || undefined,
        }
      );

      if (result.success && result.data?.success) {
        const modelCount = result.data.modelCount ?? 0;
        setTestResult({ modelCount });
        setTestStatus('success');
      } else {
        const errorMsg =
          result.data?.error ||
          result.error ||
          t('openaiCompatSetup.testError');
        setTestError(errorMsg);
        setTestStatus('error');
      }
    } catch (err) {
      setTestError(err instanceof Error ? err.message : t('openaiCompatSetup.testError'));
      setTestStatus('error');
    }
  };

  const handleContinue = async () => {
    setIsSaving(true);
    try {
      await window.API.saveSettings({
        llmOpenaiBaseUrl: baseUrl.trim(),
        llmProvider: 'openai',
      });
    } finally {
      setIsSaving(false);
      onNext();
    }
  };

  const handleSkip = () => {
    onNext();
  };

  return (
    <div className="flex h-full flex-col items-center justify-center px-8 py-6">
      <div className="w-full max-w-lg">
        {/* Hero Section */}
        <div className="text-center mb-8">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10">
            <Server className="h-8 w-8 text-primary" />
          </div>
          <h1 className="text-3xl font-bold text-foreground tracking-tight">
            {t('openaiCompatSetup.title')}
          </h1>
          <p className="mt-3 text-muted-foreground text-base">
            {t('openaiCompatSetup.subtitle')}
          </p>
        </div>

        {/* Form */}
        <div className="space-y-5">
          {/* Base URL */}
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor="openai-compat-base-url" className="text-sm font-medium">
                {t('openaiCompatSetup.baseUrlLabel')}
              </Label>
              {/* Honest data-egress posture for the entered endpoint */}
              <EgressBadge baseUrl={baseUrl} />
            </div>
            <Input
              id="openai-compat-base-url"
              type="url"
              value={baseUrl}
              onChange={(e) => {
                setBaseUrl(e.target.value);
                setTestStatus('idle');
                setTestResult(null);
                setTestError(null);
              }}
              placeholder="http://localhost:1234"
              className="font-mono text-sm"
            />
            <p className="text-xs text-muted-foreground">
              {t('openaiCompatSetup.baseUrlHint')}
            </p>
          </div>

          {/* API Key (Optional) */}
          <div className="space-y-2">
            <Label htmlFor="openai-compat-api-key" className="text-sm font-medium">
              {t('openaiCompatSetup.apiKeyLabel')}
              <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                ({t('common:labels.optional')})
              </span>
            </Label>
            <Input
              id="openai-compat-api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t('openaiCompatSetup.apiKeyPlaceholder')}
              autoComplete="off"
            />
          </div>

          {/* Test Connection Button */}
          <Button
            variant="outline"
            onClick={handleTestConnection}
            disabled={!baseUrl.trim() || testStatus === 'testing'}
            className="w-full"
          >
            {testStatus === 'testing' ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                {t('openaiCompatSetup.testing')}
              </>
            ) : (
              t('openaiCompatSetup.testButton')
            )}
          </Button>

          {/* Test Result */}
          {testStatus === 'success' && testResult && (
            <div className="flex items-start gap-3 rounded-lg border border-success/30 bg-success/10 p-4">
              <CheckCircle2 className="h-5 w-5 text-success shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-foreground">
                  {t('openaiCompatSetup.testSuccess')}
                </p>
                <p className="text-sm text-muted-foreground mt-0.5">
                  {t('openaiCompatSetup.testSuccessModels', { count: testResult.modelCount })}
                </p>
              </div>
            </div>
          )}

          {testStatus === 'error' && testError && (
            <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/10 p-4">
              <XCircle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-foreground">
                  {t('openaiCompatSetup.testFailed')}
                </p>
                <p className="text-sm text-muted-foreground mt-0.5">{testError}</p>
              </div>
            </div>
          )}
        </div>

        {/* Navigation */}
        <div className="mt-8 flex flex-col gap-3">
          <Button
            size="lg"
            onClick={handleContinue}
            disabled={!canContinue || isSaving}
            className="w-full"
          >
            {isSaving ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                {t('common:buttons.saving')}
              </>
            ) : (
              <>
                {t('common:buttons.continue')}
                <ArrowRight className="h-4 w-4 ml-2" />
              </>
            )}
          </Button>

          <Button
            size="lg"
            variant="ghost"
            onClick={handleSkip}
            className="w-full text-muted-foreground hover:text-foreground"
          >
            {t('openaiCompatSetup.skip')}
          </Button>
        </div>

        {/* Back Button */}
        <div className="mt-4 flex justify-center">
          <Button
            variant="ghost"
            onClick={onBack}
            className="text-muted-foreground hover:text-foreground text-sm"
          >
            {t('common:buttons.back')}
          </Button>
        </div>
      </div>
    </div>
  );
}
