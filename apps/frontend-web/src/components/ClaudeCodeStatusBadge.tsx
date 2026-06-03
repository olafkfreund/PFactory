import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, AlertTriangle, X, Loader2, Download, RefreshCw, ExternalLink, KeyRound } from 'lucide-react';
import { AnthropicIcon } from './icons/AnthropicIcon';
import { Button } from './ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger
} from './ui/popover';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger
} from './ui/tooltip';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from './ui/alert-dialog';
import { cn } from '../lib/utils';
import { StatusBadgeButton } from './ui/StatusBadgeButton';
import type { ClaudeCodeVersionInfo } from '../shared/types/cli';

interface ClaudeCodeStatusBadgeProps {
  className?: string;
  onOpenOnboarding?: () => void;
  iconOnly?: boolean;
}

type StatusType = 'loading' | 'installed' | 'outdated' | 'not-found' | 'error';

// Check every 24 hours
const CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000;

/**
 * Claude Code CLI status badge for the sidebar.
 * Shows installation status, auth token status, and provides quick access to install/update.
 */
export function ClaudeCodeStatusBadge({ className, onOpenOnboarding, iconOnly = false }: ClaudeCodeStatusBadgeProps) {
  const { t } = useTranslation(['common', 'navigation']);
  const [status, setStatus] = useState<StatusType>('loading');
  const [versionInfo, setVersionInfo] = useState<ClaudeCodeVersionInfo | null>(null);
  const [isInstalling, setIsInstalling] = useState(false);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [showUpdateWarning, setShowUpdateWarning] = useState(false);

  // Auth status
  const [hasToken, setHasToken] = useState<boolean | null>(null);
  const [authSource, setAuthSource] = useState<string | null>(null);
  const [authEmail, setAuthEmail] = useState<string | null>(null);

  // Check Claude Code version
  const checkVersion = useCallback(async () => {
    try {
      if (!window.API?.checkClaudeCodeVersion) {
        setStatus('error');
        return;
      }

      const result = await window.API.checkClaudeCodeVersion();

      if (result.success && result.data) {
        setVersionInfo(result.data);
        setLastChecked(new Date());

        if (!result.data.installed) {
          setStatus('not-found');
        } else if (result.data.isOutdated) {
          setStatus('outdated');
        } else {
          setStatus('installed');
        }
      } else {
        setStatus('error');
      }
    } catch (err) {
      console.error('Failed to check Claude Code version:', err);
      setStatus('error');
    }
  }, []);

  // Check auth status
  const checkAuth = useCallback(async () => {
    try {
      if (!window.API?.getAuthStatus) return;
      const result = await window.API.getAuthStatus();
      if (result.success && result.data) {
        setHasToken(result.data.hasToken);
        setAuthSource(result.data.source);
        setAuthEmail(result.data.email ?? null);
      }
    } catch {
      // Non-critical, just leave as null
    }
  }, []);

  // Initial check and periodic re-check
  useEffect(() => {
    checkVersion();
    checkAuth();

    const interval = setInterval(() => {
      checkVersion();
      checkAuth();
    }, CHECK_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [checkVersion, checkAuth]);

  // Immediate refresh when onboarding wizard closes
  useEffect(() => {
    const handler = () => {
      checkVersion();
      checkAuth();
    };
    window.addEventListener('claude-code-refresh', handler);
    return () => window.removeEventListener('claude-code-refresh', handler);
  }, [checkVersion, checkAuth]);

  // Fast polling (30s) when setup is incomplete — auto-detects install/auth changes
  // after the onboarding wizard finishes without needing a manual refresh
  useEffect(() => {
    const isIncomplete = status === 'not-found' || status === 'error' || hasToken === false;
    if (!isIncomplete || isInstalling) return;

    const fastInterval = setInterval(() => {
      checkVersion();
      checkAuth();
    }, 30_000);

    return () => clearInterval(fastInterval);
  }, [status, hasToken, isInstalling, checkVersion, checkAuth]);

  // Perform the actual install/update
  const performInstall = async () => {
    setIsInstalling(true);
    setShowUpdateWarning(false);
    try {
      if (!window.API?.installClaudeCode) {
        return;
      }

      const result = await window.API.installClaudeCode();

      if (result.success) {
        setTimeout(() => {
          checkVersion();
        }, 5000);
      }
    } catch (err) {
      console.error('Failed to install Claude Code:', err);
    } finally {
      setIsInstalling(false);
    }
  };

  // Handle install/update button click
  const handleInstall = () => {
    if (status === 'outdated') {
      setShowUpdateWarning(true);
    } else {
      performInstall();
    }
  };

  // Determine overall health: green = CLI + token, yellow = partial, red = neither
  const getOverallHealth = (): 'good' | 'partial' | 'bad' => {
    const cliOk = status === 'installed' || status === 'outdated';
    const tokenOk = hasToken === true;
    if (cliOk && tokenOk) return 'good';
    if (cliOk || tokenOk) return 'partial';
    return 'bad';
  };

  const overallHealth = getOverallHealth();

  // Get status indicator color
  const getStatusColor = () => {
    if (status === 'loading') return 'bg-muted-foreground';
    switch (overallHealth) {
      case 'good':
        return 'bg-green-500';
      case 'partial':
        return 'bg-yellow-500';
      case 'bad':
        return 'bg-destructive';
    }
  };

  // Get status icon
  const getStatusIcon = () => {
    switch (status) {
      case 'loading':
        return <Loader2 className="h-3 w-3 animate-spin" />;
      case 'installed':
        return <Check className="h-3 w-3" />;
      case 'outdated':
        return <AlertTriangle className="h-3 w-3" />;
      case 'not-found':
        return <X className="h-3 w-3" />;
      case 'error':
        return <AlertTriangle className="h-3 w-3" />;
    }
  };

  // Get tooltip text
  const getTooltipText = () => {
    if (status === 'loading') return t('navigation:claudeCode.checking', 'Checking Claude Code...');

    const parts: string[] = [];
    if (status === 'installed') parts.push(t('navigation:claudeCode.upToDate', 'Claude Code is up to date'));
    else if (status === 'outdated') parts.push(t('navigation:claudeCode.updateAvailable', 'Claude Code update available'));
    else if (status === 'not-found') parts.push(t('navigation:claudeCode.notInstalled', 'Claude Code not installed'));
    else parts.push(t('navigation:claudeCode.error', 'Error checking Claude Code'));

    if (hasToken === false) parts.push(t('navigation:claudeCode.noToken', 'No auth token configured'));
    else if (hasToken === true) parts.push(t('navigation:claudeCode.tokenOk', 'Auth token configured'));

    return parts.join(' | ');
  };

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <StatusBadgeButton
              iconOnly={iconOnly}
              icon={<AnthropicIcon className="h-4 w-4" />}
              label="Claude Code"
              dotColor={getStatusColor()}
              className={cn(
                overallHealth === 'bad' ? 'text-destructive' : '',
                overallHealth === 'partial' ? 'text-yellow-600 dark:text-yellow-500' : '',
                className
              )}
            >
              {status === 'outdated' && (
                <span className="ml-auto text-[10px] bg-yellow-500/20 text-yellow-600 dark:text-yellow-400 px-1.5 py-0.5 rounded">
                  {t('common:update', 'Update')}
                </span>
              )}
              {status === 'not-found' && (
                <span className="ml-auto text-[10px] bg-destructive/20 text-destructive px-1.5 py-0.5 rounded">
                  {t('common:install', 'Install')}
                </span>
              )}
              {hasToken === false && status !== 'not-found' && (
                <span className="ml-auto text-[10px] bg-yellow-500/20 text-yellow-600 dark:text-yellow-400 px-1.5 py-0.5 rounded">
                  {t('navigation:claudeCode.noAuth', 'No Auth')}
                </span>
              )}
            </StatusBadgeButton>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side={iconOnly ? 'bottom' : 'right'}>
          {getTooltipText()}
        </TooltipContent>
      </Tooltip>

      <PopoverContent side={iconOnly ? 'bottom' : 'right'} align="end" className="w-72">
        <div className="space-y-3">
          {/* Header */}
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <AnthropicIcon className="h-4 w-4 text-primary" />
            </div>
            <div>
              <h4 className="text-sm font-medium">Claude Code CLI</h4>
              <p className="text-xs text-muted-foreground flex items-center gap-1">
                {getStatusIcon()}
                {status === 'installed' && t('navigation:claudeCode.installed', 'Installed')}
                {status === 'outdated' && t('navigation:claudeCode.outdated', 'Update available')}
                {status === 'not-found' && t('navigation:claudeCode.missing', 'Not installed')}
                {status === 'loading' && t('navigation:claudeCode.checking', 'Checking...')}
                {status === 'error' && t('navigation:claudeCode.error', 'Error')}
              </p>
            </div>
          </div>

          {/* Version info */}
          {versionInfo && status !== 'loading' && (
            <div className="text-xs space-y-1 p-2 bg-muted rounded-md">
              {versionInfo.installed && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{t('navigation:claudeCode.current', 'Current')}:</span>
                  <span className="font-mono">{versionInfo.installed}</span>
                </div>
              )}
              {versionInfo.latest && versionInfo.latest !== 'unknown' && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{t('navigation:claudeCode.latest', 'Latest')}:</span>
                  <span className="font-mono">{versionInfo.latest}</span>
                </div>
              )}
              {lastChecked && (
                <div className="flex justify-between text-muted-foreground">
                  <span>{t('navigation:claudeCode.lastChecked', 'Last checked')}:</span>
                  <span>{lastChecked.toLocaleTimeString()}</span>
                </div>
              )}
            </div>
          )}

          {/* Auth token status */}
          {hasToken !== null && (
            <div className={cn(
              'text-xs p-2 rounded-md flex items-center gap-2',
              hasToken ? 'bg-green-500/10 text-green-700 dark:text-green-400' : 'bg-yellow-500/10 text-yellow-700 dark:text-yellow-400'
            )}>
              <KeyRound className="h-3.5 w-3.5 shrink-0" />
              <div className="flex-1 space-y-0.5">
                {hasToken ? (
                  <>
                    <span className="block">{t('navigation:claudeCode.tokenConfigured', 'Auth token configured')}{authSource ? ` (${authSource})` : ''}</span>
                    {authEmail && (
                      <span className="block text-muted-foreground">{authEmail}</span>
                    )}
                  </>
                ) : (
                  <span>{t('navigation:claudeCode.noTokenConfigured', 'No auth token configured')}</span>
                )}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2">
            {(status === 'not-found' || status === 'outdated') && (
              <Button
                size="sm"
                className="flex-1 gap-1"
                onClick={handleInstall}
                disabled={isInstalling}
              >
                {isInstalling ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Download className="h-3 w-3" />
                )}
                {status === 'outdated'
                  ? t('common:update', 'Update')
                  : t('common:install', 'Install')
                }
              </Button>
            )}
            {hasToken === false && onOpenOnboarding && (
              <Button
                variant="outline"
                size="sm"
                className="flex-1 gap-1"
                onClick={() => {
                  setIsOpen(false);
                  onOpenOnboarding();
                }}
              >
                <KeyRound className="h-3 w-3" />
                {t('navigation:claudeCode.setupAuth', 'Set Up Auth')}
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              onClick={() => {
                checkVersion();
                checkAuth();
              }}
              disabled={status === 'loading'}
            >
              <RefreshCw className={cn('h-3 w-3', status === 'loading' && 'animate-spin')} />
              {t('common:refresh', 'Refresh')}
            </Button>
          </div>

          {/* Learn more link */}
          <Button
            variant="link"
            size="sm"
            className="w-full text-xs text-muted-foreground gap-1"
            onClick={() => window.API?.openExternal?.('https://claude.ai/code')}
          >
            {t('navigation:claudeCode.learnMore', 'Learn more about Claude Code')}
            <ExternalLink className="h-3 w-3" />
          </Button>
        </div>
      </PopoverContent>

      {/* Update warning dialog */}
      <AlertDialog open={showUpdateWarning} onOpenChange={setShowUpdateWarning}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('navigation:claudeCode.updateWarningTitle', 'Update Claude Code?')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('navigation:claudeCode.updateWarningDescription', 'Updating will close all running Claude Code sessions. Any unsaved work in those sessions may be lost. Make sure to save your work before proceeding.')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>
              {t('common:cancel', 'Cancel')}
            </AlertDialogCancel>
            <AlertDialogAction onClick={performInstall}>
              {t('navigation:claudeCode.updateAnyway', 'Update Anyway')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Popover>
  );
}
