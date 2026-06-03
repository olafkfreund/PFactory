import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Cloud, Package, Server } from 'lucide-react';
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from '../../ui/collapsible';
import { SettingsSection } from '../SettingsSection';
import { LLMAccountsSettings } from './LLMAccountsSettings';
import { OpenAIEndpointsSection } from './OpenAIEndpointsSection';
import { ProviderRuntimesSection } from './ProviderRuntimesSection';
interface LLMProvidersPageProps {
  isOpen: boolean;
}

interface PanelProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

function Panel({ icon, title, description, defaultOpen = false, children }: PanelProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="rounded-lg border border-border overflow-hidden">
        <CollapsibleTrigger asChild>
          <button className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-muted/50 transition-colors">
            <div className="flex items-center gap-3">
              <div className="text-muted-foreground">{icon}</div>
              <div>
                <h4 className="text-sm font-semibold text-foreground">{title}</h4>
                <p className="text-xs text-muted-foreground">{description}</p>
              </div>
            </div>
            <ChevronDown className={`h-4 w-4 text-muted-foreground shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="border-t border-border px-4 py-4">
            {children}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

export function LLMProvidersPage({ isOpen }: LLMProvidersPageProps) {
  const { t } = useTranslation('settings');

  return (
    <SettingsSection
      title={t('sections.llmProvider.title')}
      description={t('sections.llmProvider.description')}
    >
      <div className="space-y-3">
        <Panel
          icon={<Cloud className="h-4 w-4" />}
          title={t('sections.llmProvider.cloudAgents.title')}
          description={t('sections.llmProvider.cloudAgents.description')}
          defaultOpen
        >
          <LLMAccountsSettings isOpen={isOpen} />
        </Panel>

        <Panel
          icon={<Server className="h-4 w-4" />}
          title={t('sections.llmProvider.openaiCompatible.title')}
          description={t('sections.llmProvider.openaiCompatible.description')}
        >
          <OpenAIEndpointsSection isOpen={isOpen} />
        </Panel>

        <Panel
          icon={<Package className="h-4 w-4" />}
          title="Provider Runtimes"
          description="Versions of the provider CLIs/SDKs — update to latest or pin for rollback"
        >
          <ProviderRuntimesSection />
        </Panel>
      </div>
    </SettingsSection>
  );
}
