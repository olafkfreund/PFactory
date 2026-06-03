import { useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { Save, Loader2, BookOpen, ChevronDown, ChevronRight } from 'lucide-react';
import { SettingsSection } from './SettingsSection';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { MicrosoftIcon } from '../icons/MicrosoftIcon';
import { GoogleIcon } from '../icons/GoogleIcon';
import type { AppSettings } from '../../shared/types';

interface IntegrationSettingsProps {
  settings: AppSettings;
  onSettingsChange: (settings: AppSettings) => void;
}

/**
 * Integrations settings - OAuth credentials for email notifications.
 */
export function IntegrationSettings({ settings, onSettingsChange }: IntegrationSettingsProps) {
  const { t } = useTranslation('settings');
  const [clientId, setClientId] = useState(settings.emailMicrosoftClientId || '');
  const [clientSecret, setClientSecret] = useState(settings.emailMicrosoftClientSecret || '');
  const [googleClientId, setGoogleClientId] = useState(settings.emailGoogleClientId || '');
  const [googleClientSecret, setGoogleClientSecret] = useState(settings.emailGoogleClientSecret || '');
  const [isSaving, setIsSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [googleGuideOpen, setGoogleGuideOpen] = useState(false);

  // Derive redirect URIs from the backend URL (VITE_API_URL) or current origin.
  const backendBase = (() => {
    const backendUrl = import.meta.env.VITE_API_URL;
    if (backendUrl) {
      return backendUrl.replace(/\/$/, '');
    }
    return window.location.origin;
  })();

  const outlookRedirectUri = `${backendBase}/api/email/auth/outlook/callback`;
  const gmailRedirectUri = `${backendBase}/api/email/auth/gmail/callback`;

  const hasChanges = clientId !== (settings.emailMicrosoftClientId || '') ||
    clientSecret !== (settings.emailMicrosoftClientSecret || '') ||
    googleClientId !== (settings.emailGoogleClientId || '') ||
    googleClientSecret !== (settings.emailGoogleClientSecret || '');

  const handleSave = () => {
    setIsSaving(true);
    onSettingsChange({
      ...settings,
      emailMicrosoftClientId: clientId || undefined,
      emailMicrosoftClientSecret: clientSecret || undefined,
      emailGoogleClientId: googleClientId || undefined,
      emailGoogleClientSecret: googleClientSecret || undefined,
    });
    setIsSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <SettingsSection
      title={t('sections.integrations.title')}
      description={t('sections.integrations.description')}
    >
      <div className="space-y-6">
        {/* Microsoft OAuth Credentials */}
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <MicrosoftIcon className="h-4 w-4" />
            <Label className="font-medium">{t('email.oauthCredentials')}</Label>
          </div>
          <p className="text-sm text-muted-foreground">
            {t('email.oauthCredentialsDescription')}
          </p>

          {/* Collapsible Setup Guide */}
          <div className="rounded-md border border-border">
            <button
              type="button"
              onClick={() => setGuideOpen(!guideOpen)}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              <BookOpen className="h-4 w-4" />
              <span className="flex-1 text-left">{t('email.setup.title')}</span>
              {guideOpen ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
            </button>
            {guideOpen && (
              <ol className="space-y-3 px-4 pb-4 text-sm text-muted-foreground list-decimal list-inside">
                <li>
                  <Trans
                    i18nKey="email.setup.step1"
                    ns="settings"
                    components={{
                      azureLink: (
                        <a
                          href="https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary underline underline-offset-2 hover:text-primary/80"
                        />
                      ),
                    }}
                  />
                </li>
                <li>{t('email.setup.step2')}</li>
                <li>{t('email.setup.step3')}</li>
                <li>{t('email.setup.step4')}</li>
                <li>
                  {t('email.setup.step5')}
                  <code className="ml-1 rounded bg-muted px-1.5 py-0.5 text-xs font-mono text-foreground">
                    {outlookRedirectUri}
                  </code>
                </li>
                <li>{t('email.setup.step6')}</li>
              </ol>
            )}
          </div>

          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="ms-client-id" className="text-sm">{t('email.microsoftClientId')}</Label>
              <Input
                id="ms-client-id"
                type="text"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ms-client-secret" className="text-sm">{t('email.microsoftClientSecret')}</Label>
              <Input
                id="ms-client-secret"
                type="password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                placeholder="••••••••••••"
              />
            </div>
          </div>
        </div>

        {/* Google OAuth Credentials */}
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <GoogleIcon className="h-4 w-4" />
            <Label className="font-medium">{t('email.googleOauthCredentials')}</Label>
          </div>
          <p className="text-sm text-muted-foreground">
            {t('email.googleOauthCredentialsDescription')}
          </p>

          {/* Collapsible Google Setup Guide */}
          <div className="rounded-md border border-border">
            <button
              type="button"
              onClick={() => setGoogleGuideOpen(!googleGuideOpen)}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              <BookOpen className="h-4 w-4" />
              <span className="flex-1 text-left">{t('email.setupGoogle.title')}</span>
              {googleGuideOpen ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
            </button>
            {googleGuideOpen && (
              <ol className="space-y-3 px-4 pb-4 text-sm text-muted-foreground list-decimal list-inside">
                <li>
                  <Trans
                    i18nKey="email.setupGoogle.step1"
                    ns="settings"
                    components={{
                      googleLink: (
                        <a
                          href="https://console.cloud.google.com/apis/credentials"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary underline underline-offset-2 hover:text-primary/80"
                        />
                      ),
                    }}
                  />
                </li>
                <li>{t('email.setupGoogle.step2')}</li>
                <li>{t('email.setupGoogle.step3')}</li>
                <li>{t('email.setupGoogle.step4')}</li>
                <li>
                  {t('email.setupGoogle.step5')}
                  <code className="ml-1 rounded bg-muted px-1.5 py-0.5 text-xs font-mono text-foreground">
                    {gmailRedirectUri}
                  </code>
                </li>
                <li>{t('email.setupGoogle.step6')}</li>
              </ol>
            )}
          </div>

          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="google-client-id" className="text-sm">{t('email.googleClientId')}</Label>
              <Input
                id="google-client-id"
                type="text"
                value={googleClientId}
                onChange={(e) => setGoogleClientId(e.target.value)}
                placeholder="xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="google-client-secret" className="text-sm">{t('email.googleClientSecret')}</Label>
              <Input
                id="google-client-secret"
                type="password"
                value={googleClientSecret}
                onChange={(e) => setGoogleClientSecret(e.target.value)}
                placeholder="••••••••••••"
              />
            </div>
          </div>
        </div>

        <Button
          variant="default"
          size="sm"
          onClick={handleSave}
          disabled={!hasChanges || isSaving}
        >
          {isSaving ? (
            <Loader2 className="h-4 w-4 animate-spin mr-2" />
          ) : saved ? (
            <Save className="h-4 w-4 mr-2" />
          ) : (
            <Save className="h-4 w-4 mr-2" />
          )}
          {saved ? t('actions.save') + ' ✓' : t('actions.save')}
        </Button>
      </div>
    </SettingsSection>
  );
}
