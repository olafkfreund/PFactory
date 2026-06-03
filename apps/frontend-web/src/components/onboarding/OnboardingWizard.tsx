import { useState, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Wand2 } from 'lucide-react';
import {
  FullScreenDialog,
  FullScreenDialogContent,
  FullScreenDialogHeader,
  FullScreenDialogBody,
  FullScreenDialogTitle,
  FullScreenDialogDescription
} from '../ui/full-screen-dialog';
import { ScrollArea } from '../ui/scroll-area';
import { WizardProgress, WizardStep } from './WizardProgress';
import { WelcomeStep } from './WelcomeStep';
import { ProviderChoiceStep } from './ProviderChoiceStep';
import { OpenAICompatSetupStep } from './OpenAICompatSetupStep';
import { ImportCredentialsStep } from './ImportCredentialsStep';
import { ClaudeCodeStep } from './ClaudeCodeStep';
import { OAuthStep } from './OAuthStep';
import { CompletionStep } from './CompletionStep';
import { useSettingsStore } from '../../stores/settings-store';

interface OnboardingWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onOpenTaskCreator?: () => void;
  onOpenSettings?: () => void;
}

// Wizard step identifiers
type WizardStepId =
  | 'welcome'
  | 'provider-choice'
  | 'import-credentials'
  | 'claude-code'
  | 'oauth'
  | 'openai-compat-setup'
  | 'completion';

// Chosen provider type
type ChosenProvider = 'claude' | 'openai_compat' | 'skip' | null;

// Build the wizard steps array dynamically based on the chosen provider
function getWizardSteps(provider: ChosenProvider): { id: WizardStepId; labelKey: string }[] {
  const base: { id: WizardStepId; labelKey: string }[] = [
    { id: 'welcome', labelKey: 'steps.welcome' },
    { id: 'provider-choice', labelKey: 'steps.providerChoice' },
  ];

  if (provider === 'claude') {
    return [
      ...base,
      { id: 'import-credentials', labelKey: 'steps.importCredentials' },
      { id: 'claude-code', labelKey: 'steps.claudeCode' },
      { id: 'oauth', labelKey: 'steps.auth' },
      { id: 'completion', labelKey: 'steps.done' },
    ];
  }

  if (provider === 'openai_compat') {
    return [
      ...base,
      { id: 'openai-compat-setup', labelKey: 'steps.openaiCompatSetup' },
      { id: 'completion', labelKey: 'steps.done' },
    ];
  }

  if (provider === 'skip') {
    return [
      ...base,
      { id: 'completion', labelKey: 'steps.done' },
    ];
  }

  // No provider chosen yet — show only the first two steps
  return base;
}

/**
 * Main onboarding wizard component.
 * Provides a full-screen, multi-step wizard experience for new users.
 *
 * Flow:
 * 1. Welcome — Brief intro to PFactory
 * 2. Provider Choice — Claude / OpenAI Compatible / Skip
 *    a. Claude path:  Import Credentials → Claude Code CLI → OAuth → Done
 *    b. OpenAI path:  OpenAI Compat Setup → Done
 *    c. Skip path:    Done (immediately)
 */
export function OnboardingWizard({
  open,
  onOpenChange,
  onOpenTaskCreator,
  onOpenSettings
}: OnboardingWizardProps) {
  const { t } = useTranslation('onboarding');
  const { updateSettings } = useSettingsStore();

  const [currentStepId, setCurrentStepId] = useState<WizardStepId>('welcome');
  const [chosenProvider, setChosenProvider] = useState<ChosenProvider>(null);
  const [completedSteps, setCompletedSteps] = useState<Set<WizardStepId>>(new Set());

  // Dynamic step list derived from the chosen provider
  const wizardSteps = useMemo(() => getWizardSteps(chosenProvider), [chosenProvider]);

  // Current index inside the active step list
  const currentStepIndex = useMemo(
    () => wizardSteps.findIndex(s => s.id === currentStepId),
    [wizardSteps, currentStepId]
  );

  // Build step data for the progress indicator
  const steps: WizardStep[] = useMemo(
    () =>
      wizardSteps.map((step, index) => ({
        id: step.id,
        label: t(step.labelKey),
        completed: completedSteps.has(step.id) || index < currentStepIndex,
      })),
    [wizardSteps, completedSteps, currentStepIndex, t]
  );

  // Navigate to the next step in the current wizard step list
  const goToNextStep = useCallback(() => {
    setCompletedSteps(prev => new Set(prev).add(currentStepId));
    const nextIndex = currentStepIndex + 1;
    if (nextIndex < wizardSteps.length) {
      setCurrentStepId(wizardSteps[nextIndex].id);
    }
  }, [currentStepId, currentStepIndex, wizardSteps]);

  // Navigate to the previous step
  const goToPreviousStep = useCallback(() => {
    if (currentStepIndex > 0) {
      setCurrentStepId(wizardSteps[currentStepIndex - 1].id);
    }
  }, [currentStepIndex, wizardSteps]);

  // Handle provider selection from the provider-choice step
  const handleProviderChosen = useCallback((provider: 'claude' | 'openai_compat' | 'skip') => {
    setCompletedSteps(prev => new Set(prev).add('provider-choice'));
    setChosenProvider(provider);

    if (provider === 'claude') {
      setCurrentStepId('import-credentials');
    } else if (provider === 'openai_compat') {
      setCurrentStepId('openai-compat-setup');
    } else {
      // skip → go directly to completion
      setCurrentStepId('completion');
    }
  }, []);

  // Skip directly to completion (used when credentials are imported)
  const goToCompletion = useCallback(() => {
    setCompletedSteps(prev => {
      const next = new Set(prev);
      wizardSteps.forEach(s => {
        if (s.id !== 'completion') next.add(s.id);
      });
      return next;
    });
    setCurrentStepId('completion');
  }, [wizardSteps]);

  // Reset wizard state
  const resetWizard = useCallback(() => {
    setCurrentStepId('welcome');
    setChosenProvider(null);
    setCompletedSteps(new Set());
  }, []);

  const skipWizard = useCallback(async () => {
    try {
      const result = await window.API.saveSettings({ onboardingCompleted: true });
      if (!result?.success) {
        console.error('Failed to save onboarding completion:', result?.error);
      }
    } catch (err) {
      console.error('Error saving onboarding completion:', err);
    }
    updateSettings({ onboardingCompleted: true });
    onOpenChange(false);
    resetWizard();
    window.dispatchEvent(new Event('claude-code-refresh'));
  }, [updateSettings, onOpenChange, resetWizard]);

  const finishWizard = useCallback(async () => {
    try {
      const result = await window.API.saveSettings({ onboardingCompleted: true });
      if (!result?.success) {
        console.error('Failed to save onboarding completion:', result?.error);
      }
    } catch (err) {
      console.error('Error saving onboarding completion:', err);
    }
    updateSettings({ onboardingCompleted: true });
    onOpenChange(false);
    resetWizard();
    window.dispatchEvent(new Event('claude-code-refresh'));
  }, [updateSettings, onOpenChange, resetWizard]);

  const handleOpenTaskCreator = useCallback(() => {
    if (onOpenTaskCreator) {
      onOpenChange(false);
      onOpenTaskCreator();
    }
  }, [onOpenTaskCreator, onOpenChange]);

  const handleOpenSettings = useCallback(() => {
    if (onOpenSettings) {
      finishWizard();
      onOpenSettings();
    }
  }, [onOpenSettings, finishWizard]);

  // Render current step content
  const renderStepContent = () => {
    switch (currentStepId) {
      case 'welcome':
        return (
          <WelcomeStep
            onGetStarted={goToNextStep}
            onSkip={skipWizard}
          />
        );
      case 'provider-choice':
        return (
          <ProviderChoiceStep
            onChoose={handleProviderChosen}
            onBack={goToPreviousStep}
          />
        );
      case 'openai-compat-setup':
        return (
          <OpenAICompatSetupStep
            onNext={goToNextStep}
            onBack={goToPreviousStep}
          />
        );
      case 'import-credentials':
        return (
          <ImportCredentialsStep
            onNext={goToNextStep}
            onSkipToCompletion={goToCompletion}
            onBack={goToPreviousStep}
            onSkip={skipWizard}
          />
        );
      case 'claude-code':
        return (
          <ClaudeCodeStep
            onNext={goToNextStep}
            onBack={goToPreviousStep}
            onSkip={goToNextStep}
          />
        );
      case 'oauth':
        return (
          <OAuthStep
            onNext={goToNextStep}
            onBack={goToPreviousStep}
            onSkip={skipWizard}
          />
        );
      case 'completion':
        return (
          <CompletionStep
            onFinish={finishWizard}
            onOpenTaskCreator={handleOpenTaskCreator}
            onOpenSettings={handleOpenSettings}
          />
        );
      default:
        return null;
    }
  };

  // Handle dialog close
  const handleOpenChange = useCallback((newOpen: boolean) => {
    if (!newOpen) {
      skipWizard();
    } else {
      onOpenChange(newOpen);
    }
  }, [skipWizard, onOpenChange]);

  // Show progress bar for all steps except welcome and completion
  const showProgress =
    currentStepId !== 'welcome' &&
    currentStepId !== 'completion';

  return (
    <FullScreenDialog open={open} onOpenChange={handleOpenChange}>
      <FullScreenDialogContent>
        <FullScreenDialogHeader>
          <FullScreenDialogTitle className="flex items-center gap-3">
            <Wand2 className="h-6 w-6" />
            {t('wizard.title')}
          </FullScreenDialogTitle>
          <FullScreenDialogDescription>
            {t('wizard.description')}
          </FullScreenDialogDescription>

          {/* Progress indicator — shown for all intermediate steps */}
          {showProgress && (
            <div className="mt-6">
              <WizardProgress currentStep={currentStepIndex} steps={steps} />
            </div>
          )}
        </FullScreenDialogHeader>

        <FullScreenDialogBody>
          <ScrollArea className="h-full">
            {renderStepContent()}
          </ScrollArea>
        </FullScreenDialogBody>
      </FullScreenDialogContent>
    </FullScreenDialog>
  );
}
