import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, SlidersHorizontal } from 'lucide-react';
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from '../ui/collapsible';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Switch } from '../ui/switch';
import { SettingsSection } from './SettingsSection';
import { AgentProfileSettings } from './AgentProfileSettings';
import {
  ALL_AVAILABLE_MODELS,
  THINKING_LEVELS,
  DEFAULT_FEATURE_MODELS,
  DEFAULT_FEATURE_THINKING,
  FEATURE_LABELS,
  fetchOllamaModels,
  fetchOpenAICompatibleModels
} from '../../shared/constants';
import type {
  AppSettings,
  FeatureModelConfig,
  FeatureThinkingConfig,
  ThinkingLevel
} from '../../shared/types';

interface GeneralSettingsProps {
  settings: AppSettings;
  onSettingsChange: (settings: AppSettings) => void;
  section: 'agent';
}

/**
 * General settings component for agent configuration
 */
export function GeneralSettings({ settings, onSettingsChange }: GeneralSettingsProps) {
  const { t } = useTranslation('settings');
  const [featureModelOpen, setFeatureModelOpen] = useState(false);
  const [ollamaModels, setOllamaModels] = useState<{ value: string; label: string }[]>([]);
  const [openAICompatModels, setOpenAICompatModels] = useState<{ value: string; label: string }[]>([]);

  const llmOpenaiBaseUrl = settings.llmOpenaiBaseUrl;

  useEffect(() => {
    fetchOllamaModels().then(setOllamaModels);
    fetchOpenAICompatibleModels(llmOpenaiBaseUrl).then(setOpenAICompatModels);
  }, [llmOpenaiBaseUrl]);

  return (
    <div className="space-y-8">
      {/* Agent Profile Selection */}
      <AgentProfileSettings />

      {/* Other Agent Settings */}
      <SettingsSection
        title={t('general.otherAgentSettings')}
        description={t('general.otherAgentSettingsDescription')}
      >
        <div className="space-y-6">
          <div className="space-y-3">
            <Label htmlFor="agentFramework" className="text-sm font-medium text-foreground">{t('general.agentFramework')}</Label>
            <p className="text-sm text-muted-foreground">{t('general.agentFrameworkDescription')}</p>
            <Select
              value={settings.agentFramework}
              onValueChange={(value) => onSettingsChange({ ...settings, agentFramework: value })}
            >
              <SelectTrigger id="agentFramework" className="w-full max-w-md">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pfactory">{t('general.agentFrameworkAutoClaude')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between max-w-md">
              <div className="space-y-1">
                <Label htmlFor="autoNameTerminals" className="text-sm font-medium text-foreground">
                  {t('general.aiTerminalNaming')}
                </Label>
                <p className="text-sm text-muted-foreground">
                  {t('general.aiTerminalNamingDescription')}
                </p>
              </div>
              <Switch
                id="autoNameTerminals"
                checked={settings.autoNameTerminals}
                onCheckedChange={(checked) => onSettingsChange({ ...settings, autoNameTerminals: checked })}
              />
            </div>
          </div>

          <div className="space-y-3">
            <div className="flex items-center justify-between max-w-md">
              <div className="space-y-1">
                <Label htmlFor="bmadSessionSegmentation" className="text-sm font-medium text-foreground">
                  {t('general.bmadSessionSegmentation')}
                </Label>
                <p className="text-sm text-muted-foreground">
                  {t('general.bmadSessionSegmentationDescription')}
                </p>
              </div>
              <Switch
                id="bmadSessionSegmentation"
                checked={settings.bmadSessionSegmentation ?? false}
                onCheckedChange={(checked) => onSettingsChange({ ...settings, bmadSessionSegmentation: checked })}
              />
            </div>
          </div>

          {/* Feature Model Configuration */}
          <Collapsible open={featureModelOpen} onOpenChange={setFeatureModelOpen}>
            <div className="rounded-lg border border-border overflow-hidden">
              <CollapsibleTrigger asChild>
                <button className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-muted/50 transition-colors">
                  <div className="flex items-center gap-3">
                    <div className="text-muted-foreground"><SlidersHorizontal className="h-5 w-5" /></div>
                    <div>
                      <div className="text-sm font-medium text-foreground">{t('general.featureModelSettings')}</div>
                      <div className="text-xs text-muted-foreground">{t('general.featureModelSettingsDescription')}</div>
                    </div>
                  </div>
                  <ChevronDown className={`h-4 w-4 text-muted-foreground shrink-0 transition-transform duration-200 ${featureModelOpen ? 'rotate-180' : ''}`} />
                </button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="border-t border-border px-4 py-4 space-y-4">
                  {(Object.keys(FEATURE_LABELS) as Array<keyof FeatureModelConfig>).map((feature) => {
                    const featureModels = settings.featureModels || DEFAULT_FEATURE_MODELS;
                    const featureThinking = settings.featureThinking || DEFAULT_FEATURE_THINKING;

                    return (
                      <div key={feature} className="space-y-2">
                        <div className="flex items-center justify-between">
                          <Label className="text-sm font-medium text-foreground">
                            {FEATURE_LABELS[feature].label}
                          </Label>
                          <span className="text-xs text-muted-foreground">
                            {FEATURE_LABELS[feature].description}
                          </span>
                        </div>
                        <div className="grid grid-cols-2 gap-3 max-w-md">
                          {/* Model Select */}
                          <div className="space-y-1">
                            <Label className="text-xs text-muted-foreground">{t('general.model')}</Label>
                            <Select
                              value={featureModels[feature]}
                              onValueChange={(value) => {
                                const newFeatureModels: FeatureModelConfig = { ...featureModels, [feature]: value };
                                onSettingsChange({ ...settings, featureModels: newFeatureModels });
                              }}
                            >
                              <SelectTrigger className="h-9">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {[...ALL_AVAILABLE_MODELS, ...ollamaModels, ...openAICompatModels].map((m) => (
                                  <SelectItem key={m.value} value={m.value}>
                                    {m.label}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                          {/* Thinking Level Select */}
                          <div className="space-y-1">
                            <Label className="text-xs text-muted-foreground">{t('general.thinkingLevel')}</Label>
                            <Select
                              value={featureThinking[feature]}
                              onValueChange={(value) => {
                                const newFeatureThinking = { ...featureThinking, [feature]: value as ThinkingLevel };
                                onSettingsChange({ ...settings, featureThinking: newFeatureThinking });
                              }}
                            >
                              <SelectTrigger className="h-9">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {THINKING_LEVELS.map((level) => (
                                  <SelectItem key={level.value} value={level.value}>
                                    {level.label}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </CollapsibleContent>
            </div>
          </Collapsible>
        </div>
      </SettingsSection>
    </div>
  );
}
