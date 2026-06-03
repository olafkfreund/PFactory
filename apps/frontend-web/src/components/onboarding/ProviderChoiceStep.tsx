import type { ReactNode } from 'react';
import { Bot, Server, Clock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent } from '../ui/card';
import { Button } from '../ui/button';

type Provider = 'claude' | 'openai_compat' | 'skip';

interface ProviderChoiceStepProps {
  onChoose: (provider: Provider) => void;
  onBack: () => void;
}

interface ProviderCardProps {
  icon: ReactNode;
  title: string;
  description: string;
  onClick: () => void;
  'data-testid'?: string;
}

function ProviderCard({ icon, title, description, onClick, 'data-testid': dataTestId }: ProviderCardProps) {
  return (
    <Card
      data-testid={dataTestId}
      className="border border-border bg-card/50 backdrop-blur-sm cursor-pointer transition-all hover:border-primary/50 hover:shadow-md"
      onClick={onClick}
    >
      <CardContent className="p-6">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            {icon}
          </div>
          <div className="flex-1">
            <h3 className="font-semibold text-foreground text-lg">{title}</h3>
            <p className="mt-2 text-sm text-muted-foreground leading-relaxed">{description}</p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * ProviderChoiceStep component for the onboarding wizard.
 *
 * Presents three large choice cards so the user can choose their AI provider:
 * 1. Claude — Sign in with Anthropic account or OAuth token
 * 2. OpenAI Compatible — Any OpenAI-compatible server (LM Studio, Ollama, vLLM, etc.)
 * 3. Skip — Set up later in Settings
 *
 * Props:
 * - onChoose(provider): called with 'claude', 'openai_compat', or 'skip' when a card is clicked
 * - onBack(): called when the user presses the Back button
 */
export function ProviderChoiceStep({ onChoose, onBack }: ProviderChoiceStepProps) {
  const { t } = useTranslation(['onboarding', 'common']);

  return (
    <div className="flex h-full flex-col items-center justify-center px-8 py-6">
      <div className="w-full max-w-2xl">
        {/* Hero Section */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-foreground tracking-tight">
            {t('providerChoice.title')}
          </h1>
          <p className="mt-3 text-muted-foreground text-lg">
            {t('providerChoice.subtitle')}
          </p>
        </div>

        {/* Provider Choice Cards */}
        <div className="flex flex-col gap-4 mb-10">
          <ProviderCard
            icon={<Bot className="h-6 w-6" />}
            title={t('providerChoice.claude.title')}
            description={t('providerChoice.claude.description')}
            onClick={() => onChoose('claude')}
            data-testid="provider-choice-claude"
          />
          <ProviderCard
            icon={<Server className="h-6 w-6" />}
            title={t('providerChoice.openaiCompat.title')}
            description={t('providerChoice.openaiCompat.description')}
            onClick={() => onChoose('openai_compat')}
            data-testid="provider-choice-openai-compat"
          />
          <ProviderCard
            icon={<Clock className="h-6 w-6" />}
            title={t('providerChoice.skip.title')}
            description={t('providerChoice.skip.description')}
            onClick={() => onChoose('skip')}
            data-testid="provider-choice-skip"
          />
        </div>

        {/* Back Button */}
        <div className="flex justify-center">
          <Button
            size="lg"
            variant="ghost"
            onClick={onBack}
            className="text-muted-foreground hover:text-foreground"
          >
            {t('common:back')}
          </Button>
        </div>
      </div>
    </div>
  );
}
