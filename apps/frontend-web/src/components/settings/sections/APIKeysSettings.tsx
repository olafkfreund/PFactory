import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Key, Eye, EyeOff, Info } from 'lucide-react';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import type { AppSettings } from '../../../shared/types/settings';

interface APIKeysSettingsProps {
  settings: AppSettings;
  onSettingsChange: (settings: AppSettings) => void;
}

interface APIKeyFieldProps {
  id: string;
  label: string;
  description: string;
  placeholder: string;
  value: string | undefined;
  onChange: (value: string | undefined) => void;
}

function APIKeyField({ id, label, description, placeholder, value, onChange }: APIKeyFieldProps) {
  const [show, setShow] = useState(false);

  return (
    <div className="space-y-2">
      <Label htmlFor={id} className="text-sm font-medium text-foreground">
        {label}
      </Label>
      <p className="text-xs text-muted-foreground">{description}</p>
      <div className="relative max-w-lg">
        <Input
          id={id}
          type={show ? 'text' : 'password'}
          placeholder={placeholder}
          value={value || ''}
          onChange={(e) => onChange(e.target.value || undefined)}
          className="pr-10 font-mono text-sm"
        />
        <button
          type="button"
          onClick={() => setShow(!show)}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        >
          {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
    </div>
  );
}

export function APIKeysSettings({ settings, onSettingsChange }: APIKeysSettingsProps) {
  const { t } = useTranslation('settings');

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Key className="h-4 w-4 text-muted-foreground" />
        <h4 className="text-sm font-semibold text-foreground">{t('sections.llmProvider.apiKeys.title')}</h4>
      </div>

      <div className="rounded-lg bg-info/10 border border-info/30 p-3">
        <div className="flex items-start gap-2">
          <Info className="h-4 w-4 text-info shrink-0 mt-0.5" />
          <p className="text-xs text-muted-foreground">
            {t('sections.llmProvider.apiKeys.info')}
          </p>
        </div>
      </div>

      <div className="space-y-6">
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200/90">
          <strong className="text-amber-100">⚠ Direct-billing notice:</strong>{' '}
          Setting an Anthropic API key below enables direct API-key billing for
          a small set of advanced features (changelog generation with the
          Anthropic provider, batch insight extraction). PFactory&apos;s default
          path is <strong className="text-amber-100">OAuth via Claude Code</strong>
          {' '}which uses your Claude subscription, <strong>NOT</strong> this key.
          Leave the field empty unless you specifically want direct billing.
        </div>
        <APIKeyField
          id="globalAnthropicKey"
          label={t('sections.llmProvider.apiKeys.anthropic.label')}
          description={t('sections.llmProvider.apiKeys.anthropic.description')}
          placeholder="sk-ant-..."
          value={settings.globalAnthropicApiKey}
          onChange={(value) => onSettingsChange({ ...settings, globalAnthropicApiKey: value })}
        />

        <APIKeyField
          id="globalOpenAIKey"
          label={t('sections.llmProvider.apiKeys.openai.label')}
          description={t('sections.llmProvider.apiKeys.openai.description')}
          placeholder="sk-..."
          value={settings.globalOpenAIApiKey}
          onChange={(value) => onSettingsChange({ ...settings, globalOpenAIApiKey: value })}
        />

        <APIKeyField
          id="globalGoogleKey"
          label={t('sections.llmProvider.apiKeys.google.label')}
          description={t('sections.llmProvider.apiKeys.google.description')}
          placeholder="AIza..."
          value={settings.globalGoogleApiKey}
          onChange={(value) => onSettingsChange({ ...settings, globalGoogleApiKey: value })}
        />

        <APIKeyField
          id="globalGroqKey"
          label={t('sections.llmProvider.apiKeys.groq.label')}
          description={t('sections.llmProvider.apiKeys.groq.description')}
          placeholder="gsk_..."
          value={settings.globalGroqApiKey}
          onChange={(value) => onSettingsChange({ ...settings, globalGroqApiKey: value })}
        />

        <APIKeyField
          id="globalOpenRouterKey"
          label={t('sections.llmProvider.apiKeys.openrouter.label')}
          description={t('sections.llmProvider.apiKeys.openrouter.description')}
          placeholder="sk-or-..."
          value={settings.globalOpenRouterApiKey}
          onChange={(value) => onSettingsChange({ ...settings, globalOpenRouterApiKey: value })}
        />
      </div>
    </div>
  );
}
