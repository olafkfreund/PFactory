import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, Loader2, CheckCircle2, AlertCircle, FileKey } from 'lucide-react';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';

interface ImportCredentialsStepProps {
  onNext: () => void;
  onSkipToCompletion: () => void;
  onBack: () => void;
  onSkip: () => void;
}

/**
 * Import Credentials step for the onboarding wizard.
 * Detects existing ~/.claude/.credentials.json and offers to import the token.
 */
export function ImportCredentialsStep({
  onNext,
  onSkipToCompletion,
  onBack,
  onSkip,
}: ImportCredentialsStepProps) {
  const { t } = useTranslation('onboarding');
  const [checking, setChecking] = useState(true);
  const [credentialsExist, setCredentialsExist] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<'success' | 'error' | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Check for existing credentials on mount
  useEffect(() => {
    async function check() {
      try {
        const result = await window.API.checkClaudeCredentialsExist();
        if (result.success && result.data) {
          setCredentialsExist(result.data.exists);
        }
      } catch {
        // If check fails, just proceed to OAuth step
        setCredentialsExist(false);
      } finally {
        setChecking(false);
      }
    }
    check();
  }, []);

  // If no credentials found, auto-advance to OAuth step
  useEffect(() => {
    if (!checking && !credentialsExist) {
      onNext();
    }
  }, [checking, credentialsExist, onNext]);

  const handleImport = async () => {
    setImporting(true);
    setErrorMessage(null);
    try {
      const result = await window.API.importClaudeCredentials();
      if (result.success) {
        setImportResult('success');
        // Auto-advance to completion after short delay
        setTimeout(() => {
          onSkipToCompletion();
        }, 1500);
      } else {
        setImportResult('error');
        setErrorMessage(result.error || 'Failed to import credentials');
      }
    } catch (err) {
      setImportResult('error');
      setErrorMessage(err instanceof Error ? err.message : 'Failed to import credentials');
    } finally {
      setImporting(false);
    }
  };

  // Show loading while checking
  if (checking) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-8 py-6">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        <p className="mt-4 text-sm text-muted-foreground">
          {t('importCredentials.checking', 'Checking for existing credentials...')}
        </p>
      </div>
    );
  }

  // Only render if credentials exist (otherwise useEffect auto-advances)
  if (!credentialsExist) {
    return null;
  }

  return (
    <div className="flex h-full flex-col items-center justify-center px-8 py-6">
      <div className="w-full max-w-2xl">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="flex justify-center mb-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-primary">
              <FileKey className="h-7 w-7" />
            </div>
          </div>
          <h1 className="text-2xl font-bold text-foreground tracking-tight">
            {t('importCredentials.title', 'Existing Credentials Detected')}
          </h1>
          <p className="mt-2 text-muted-foreground">
            {t('importCredentials.description', 'We found existing Claude Code credentials on your system.')}
          </p>
        </div>

        {/* Detection Card */}
        <Card className="border border-primary/30 bg-primary/5 mb-6">
          <CardContent className="p-5">
            <div className="flex items-start gap-4">
              <CheckCircle2 className="h-5 w-5 text-primary shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="text-sm font-medium text-foreground">
                  {t('importCredentials.found', 'Claude Code CLI credentials found')}
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {t('importCredentials.foundDescription', 'A valid OAuth token was found in ~/.claude/.credentials.json. Would you like to import it into PFactory?')}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Import result */}
        {importResult === 'success' && (
          <Card className="border border-success/30 bg-success/10 mb-6">
            <CardContent className="p-4">
              <div className="flex items-start gap-3">
                <CheckCircle2 className="h-5 w-5 text-success shrink-0 mt-0.5" />
                <p className="text-sm text-success">
                  {t('importCredentials.success', 'Credentials imported successfully! Continuing...')}
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {importResult === 'error' && (
          <Card className="border border-destructive/30 bg-destructive/10 mb-6">
            <CardContent className="p-4">
              <div className="flex items-start gap-3">
                <AlertCircle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
                <p className="text-sm text-destructive">
                  {errorMessage || t('importCredentials.error', 'Failed to import credentials. You can set up authentication manually.')}
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Action Buttons */}
        <div className="flex flex-col gap-4 items-center">
          <Button
            size="lg"
            onClick={handleImport}
            disabled={importing || importResult === 'success'}
            className="gap-2 px-8"
          >
            {importing ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <Download className="h-5 w-5" />
            )}
            {t('importCredentials.importButton', 'Yes, Import Credentials')}
          </Button>
          <Button
            variant="ghost"
            onClick={onNext}
            disabled={importing || importResult === 'success'}
            className="text-muted-foreground hover:text-foreground"
          >
            {t('importCredentials.manualSetup', 'No, Set Up Manually')}
          </Button>
        </div>

        {/* Footer navigation */}
        <div className="flex justify-between items-center mt-10 pt-6 border-t border-border">
          <Button
            variant="ghost"
            onClick={onBack}
            className="text-muted-foreground hover:text-foreground"
          >
            {t('common:back', 'Back')}
          </Button>
          <Button
            variant="ghost"
            onClick={onSkip}
            className="text-muted-foreground hover:text-foreground"
          >
            {t('common:skip', 'Skip')}
          </Button>
        </div>
      </div>
    </div>
  );
}
