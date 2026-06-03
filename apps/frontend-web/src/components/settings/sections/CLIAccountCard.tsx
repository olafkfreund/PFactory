import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ArrowUpCircle,
  Check,
  Download,
  Loader2,
  LogIn,
  Trash2,
  Terminal,
  X,
} from 'lucide-react';
import { Button } from '../../ui/button';
import { cn } from '../../../lib/utils';
import { OpenAIIcon } from '../../icons/OpenAIIcon';
import { GeminiIcon } from '../../icons/GeminiIcon';
import type { CLIAccountStatus } from '../../../shared/types';

interface CLIAccountCardProps {
  cli: 'codex' | 'gemini';
  status: CLIAccountStatus | null;
  isLoading: boolean;
  onImport: () => void;
  onStartLogin: () => void;
  onRemove: () => void;
  onInstall: () => Promise<void>;
  onRefresh?: () => void;
}

export function CLIAccountCard({
  cli,
  status,
  isLoading,
  onImport,
  onStartLogin,
  onRemove,
  onInstall,
  onRefresh,
}: CLIAccountCardProps) {
  const { t } = useTranslation('settings');

  const [isLoginPolling, setIsLoginPolling] = useState(false);
  const [isInstalling, setIsInstalling] = useState(false);
  const [loginTerminalId, setLoginTerminalId] = useState<string | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const terminalRef = useRef<HTMLDivElement>(null);

  const Icon = cli === 'codex' ? OpenAIIcon : GeminiIcon;
  const cliName = t(`integrations.${cli}.name`);
  const cliDescription = t(`integrations.${cli}.description`);
  const installHint = t(`integrations.${cli}.installHint`);

  const hasUpdate = status?.installed && status?.latestVersion && status?.version !== status?.latestVersion;

  const getAuthMethodLabel = () => {
    if (!status?.authMethod) return null;
    if (cli === 'codex') {
      return status.authMethod === 'oauth'
        ? t('integrations.codex.viaOAuth')
        : t('integrations.codex.viaApiKey');
    }
    return status.authMethod === 'google_login'
      ? t('integrations.gemini.viaGoogleLogin')
      : t('integrations.gemini.viaApiKey');
  };

  const handleStartLogin = async () => {
    setIsLoginPolling(true);
    setLoginError(null);
    try {
      const result = await window.API.startCLILoginTerminal(cli);
      if (result.success && result.data?.terminalId) {
        setLoginTerminalId(result.data.terminalId);
      } else {
        setLoginError(result.error || 'Failed to start login terminal');
        setIsLoginPolling(false);
      }
    } catch (err) {
      console.error(`Failed to start ${cli} login terminal:`, err);
      setLoginError('Failed to create login terminal');
      setIsLoginPolling(false);
    }
    // Auto-reset after 3 min timeout
    setTimeout(() => {
      setIsLoginPolling(false);
      setLoginTerminalId(null);
    }, 180000);
  };

  const handleCancelLogin = () => {
    if (loginTerminalId) {
      window.API.destroyTerminal(loginTerminalId).catch(() => {});
    }
    setIsLoginPolling(false);
    setLoginTerminalId(null);
    setLoginError(null);
  };

  const handleInstall = async () => {
    setIsInstalling(true);
    try {
      await onInstall();
    } finally {
      setIsInstalling(false);
    }
  };

  // Reset login polling when auth succeeds
  useEffect(() => {
    if (isLoginPolling && status?.authenticated) {
      setIsLoginPolling(false);
      if (loginTerminalId) {
        // Clean up terminal after successful auth
        setTimeout(() => {
          window.API.destroyTerminal(loginTerminalId).catch(() => {});
          setLoginTerminalId(null);
        }, 2000);
      }
      onRefresh?.();
    }
  }, [isLoginPolling, status?.authenticated]);

  // Not installed state
  if (!status || !status.installed) {
    return (
      <div className="rounded-lg border border-dashed border-border p-3">
        <div className="flex items-center gap-3">
          <div className="h-7 w-7 rounded-full flex items-center justify-center bg-muted text-muted-foreground shrink-0">
            <Icon className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-muted-foreground">{cliName}</span>
              <span className="text-xs bg-muted px-1.5 py-0.5 rounded text-muted-foreground">
                {t('integrations.notInstalled')}
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">{cliDescription}</p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={handleInstall}
            disabled={isInstalling}
            className="gap-1 h-7 text-xs shrink-0"
          >
            {isInstalling ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Download className="h-3 w-3" />
            )}
            {isInstalling ? t('integrations.installing') : t('integrations.install')}
          </Button>
        </div>
        <div className="mt-2 ml-10">
          <code className="text-xs bg-muted px-2 py-1 rounded font-mono text-muted-foreground">
            {installHint}
          </code>
        </div>
      </div>
    );
  }

  // Installed state
  return (
    <div
      className={cn(
        'rounded-lg border transition-colors',
        status.authenticated
          ? 'border-success/30 bg-success/5'
          : 'border-border bg-background'
      )}
    >
      <div className="flex items-center justify-between p-3">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              'h-7 w-7 rounded-full flex items-center justify-center shrink-0',
              status.authenticated
                ? 'bg-success/20 text-success'
                : 'bg-muted text-muted-foreground'
            )}
          >
            <Icon className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-foreground">{cliName}</span>
              {status.version && (
                <span className="text-xs text-muted-foreground font-mono">
                  {status.version}
                </span>
              )}
              {status.authenticated ? (
                <span className="text-xs bg-success/20 text-success px-1.5 py-0.5 rounded flex items-center gap-1">
                  <Check className="h-3 w-3" />
                  {t('integrations.authenticated')}
                </span>
              ) : (
                <span className="text-xs bg-warning/20 text-warning px-1.5 py-0.5 rounded">
                  {t('integrations.needsAuth')}
                </span>
              )}
              {hasUpdate && (
                <span className="text-xs bg-blue-500/20 text-blue-600 dark:text-blue-400 px-1.5 py-0.5 rounded flex items-center gap-1">
                  <ArrowUpCircle className="h-3 w-3" />
                  {t('integrations.updateAvailable')}
                </span>
              )}
            </div>
            {status.authenticated && (
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs text-muted-foreground">{getAuthMethodLabel()}</span>
                {status.email && (
                  <span className="text-xs text-muted-foreground">{status.email}</span>
                )}
              </div>
            )}
            {status.tokenExpiresAt && (
              <span className="text-xs text-muted-foreground ml-2">
                {t(`integrations.${cli}.tokenExpires`)}: {new Date(status.tokenExpiresAt).toLocaleDateString()}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1">
          {isLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : (
            <>
              {hasUpdate && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleInstall}
                  disabled={isInstalling}
                  className="gap-1 h-7 text-xs"
                >
                  {isInstalling ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <ArrowUpCircle className="h-3 w-3" />
                  )}
                  {isInstalling ? t('integrations.updating') : t('integrations.update')}
                </Button>
              )}
              {!status.authenticated && (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleStartLogin}
                    disabled={isLoginPolling}
                    className="gap-1 h-7 text-xs"
                  >
                    {isLoginPolling ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <LogIn className="h-3 w-3" />
                    )}
                    {isLoginPolling
                      ? t('integrations.waitingForAuth')
                      : t('integrations.loginInTerminal')}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={onImport}
                    className="gap-1 h-7 text-xs"
                    title={t(`integrations.${cli}.importHint`)}
                  >
                    <Download className="h-3 w-3" />
                    {t('integrations.importCredentials')}
                  </Button>
                </>
              )}
              {status.authenticated && (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={onRemove}
                  className="h-7 w-7 text-destructive hover:text-destructive hover:bg-destructive/10"
                  title={t('integrations.disconnect')}
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Login terminal session */}
      {isLoginPolling && loginTerminalId && (
        <div className="px-3 pb-3 pt-0">
          <div className="bg-muted/30 rounded-lg p-3 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-medium text-foreground">
                <Terminal className="h-3.5 w-3.5" />
                {t('integrations.authenticatingWith', { cli: cliName })}
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={handleCancelLogin}
                className="h-6 w-6 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3 w-3" />
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {t('integrations.completeOAuthInBrowser')}
            </p>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t('integrations.waitingForAuth')}
            </div>
          </div>
        </div>
      )}

      {/* Login error */}
      {loginError && (
        <div className="px-3 pb-3 pt-0">
          <div className="bg-destructive/10 rounded-lg p-3 text-xs text-destructive">
            {loginError}
          </div>
        </div>
      )}

      {/* Credentials detected hint (when not authenticated but CLI credential files exist) */}
      {!status.authenticated && !isLoginPolling && (
        <div className="px-3 pb-2 pt-0">
          <span className="text-xs text-muted-foreground">
            {t('integrations.credentialsDetected')}: <code className="font-mono text-xs">~/.{cli}/</code>
          </span>
        </div>
      )}

    </div>
  );
}
