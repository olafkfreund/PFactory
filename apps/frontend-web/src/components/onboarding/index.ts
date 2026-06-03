/**
 * Onboarding module barrel export
 * Provides clean import paths for onboarding wizard components
 */

export { OnboardingWizard } from './OnboardingWizard';
export { WelcomeStep } from './WelcomeStep';
export { ImportCredentialsStep } from './ImportCredentialsStep';
export { AuthChoiceStep } from './AuthChoiceStep';
export { OAuthStep } from './OAuthStep';
export { MemoryStep } from './MemoryStep';
export { OllamaModelSelector } from './OllamaModelSelector';
export { FirstSpecStep } from './FirstSpecStep';
export { CompletionStep } from './CompletionStep';
export { WizardProgress, type WizardStep } from './WizardProgress';

// OpenAI-compatible provider onboarding steps
export { ProviderChoiceStep } from './ProviderChoiceStep';
export { OpenAICompatSetupStep } from './OpenAICompatSetupStep';

// Legacy export for backward compatibility
export { GraphitiStep } from './GraphitiStep';
