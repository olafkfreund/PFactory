import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription
} from './ui/dialog';
import { Button } from './ui/button';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from './ui/select';
import { AVAILABLE_MODELS, THINKING_LEVELS, PROVIDER_INFO, DEFAULT_FEATURE_MODELS, DEFAULT_FEATURE_THINKING } from '../shared/constants';
import type { InsightsModelConfig, InsightsProviderInfo, InsightsProvider } from '../shared/types';
import type { ThinkingLevel } from '../shared/types';

interface CustomModelModalProps {
  currentConfig?: InsightsModelConfig;
  availableProviders?: InsightsProviderInfo[];
  onSave: (config: InsightsModelConfig) => void;
  onClose: () => void;
  open?: boolean;
}

export function CustomModelModal({
  currentConfig,
  availableProviders = [],
  onSave,
  onClose,
  open = true
}: CustomModelModalProps) {
  const { t } = useTranslation('dialogs');

  const [provider, setProvider] = useState<InsightsProvider>(
    currentConfig?.provider || 'claude'
  );
  const [model, setModel] = useState<string>(
    currentConfig?.model || DEFAULT_FEATURE_MODELS.insights
  );
  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel>(
    currentConfig?.thinkingLevel || DEFAULT_FEATURE_THINKING.insights as ThinkingLevel
  );

  // Sync internal state when modal opens or config changes
  useEffect(() => {
    if (open) {
      setProvider(currentConfig?.provider || 'claude');
      setModel(currentConfig?.model || DEFAULT_FEATURE_MODELS.insights);
      setThinkingLevel(currentConfig?.thinkingLevel || DEFAULT_FEATURE_THINKING.insights as ThinkingLevel);
    }
  }, [open, currentConfig]);

  // When provider changes, reset model to first available
  useEffect(() => {
    if (provider === 'claude') {
      setModel(currentConfig?.provider === 'claude' ? (currentConfig?.model || DEFAULT_FEATURE_MODELS.insights) : DEFAULT_FEATURE_MODELS.insights);
    } else {
      const providerInfo = availableProviders.find(p => p.provider === provider);
      if (providerInfo && providerInfo.models.length > 0) {
        setModel(providerInfo.models[0].id);
      }
    }
  }, [provider, availableProviders, currentConfig]);

  const handleSave = () => {
    onSave({
      provider,
      profileId: 'custom',
      model,
      thinkingLevel: provider === 'claude' ? thinkingLevel : undefined,
    });
  };

  // Build model options based on selected provider
  const getModelOptions = () => {
    if (provider === 'claude') {
      return AVAILABLE_MODELS.map(m => ({ id: m.value, label: m.label }));
    }
    const providerInfo = availableProviders.find(p => p.provider === provider);
    return providerInfo?.models || [];
  };

  // Filter to providers that are available (always include claude)
  const providerOptions = [
    { id: 'claude' as InsightsProvider, label: PROVIDER_INFO.claude.displayName },
    ...availableProviders
      .filter(p => p.provider !== 'claude' && p.available)
      .map(p => ({ id: p.provider, label: p.displayName })),
  ];

  const modelOptions = getModelOptions();
  const showThinking = provider === 'claude';

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('customModel.title')}</DialogTitle>
          <DialogDescription>
            {t('customModel.description')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Provider selector */}
          <div className="space-y-2">
            <Label htmlFor="provider-select">{t('customModel.provider', 'Provider')}</Label>
            <Select value={provider} onValueChange={(v) => setProvider(v as InsightsProvider)}>
              <SelectTrigger id="provider-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {providerOptions.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Model selector */}
          <div className="space-y-2">
            <Label htmlFor="model-select">{t('customModel.model')}</Label>
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger id="model-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {modelOptions.map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Thinking level (Claude only) */}
          {showThinking && (
            <div className="space-y-2">
              <Label htmlFor="thinking-select">{t('customModel.thinkingLevel')}</Label>
              <Select value={thinkingLevel} onValueChange={(v) => setThinkingLevel(v as ThinkingLevel)}>
                <SelectTrigger id="thinking-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {THINKING_LEVELS.map((level) => (
                    <SelectItem key={level.value} value={level.value}>
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{level.label}</span>
                        <span className="text-xs text-muted-foreground">
                          {level.description}
                        </span>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {!showThinking && (
            <p className="text-xs text-muted-foreground">
              {t('customModel.thinkingLevelHint', 'Thinking level is only available for Claude')}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t('customModel.cancel')}
          </Button>
          <Button onClick={handleSave}>
            {t('customModel.apply')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
