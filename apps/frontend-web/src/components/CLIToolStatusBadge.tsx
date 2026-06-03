import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Check,
  AlertTriangle,
  X,
  Loader2,
  Download,
  RefreshCw,
  KeyRound,
} from 'lucide-react';
import { Button } from './ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from './ui/popover';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from './ui/tooltip';
import { cn } from '../lib/utils';
import { StatusBadgeButton } from './ui/StatusBadgeButton';
import { OpenAIIcon } from './icons/OpenAIIcon';
import { GeminiIcon } from './icons/GeminiIcon';
import type { CLIAccountStatus, CLIAccountsDetectionResult } from '../shared/types';

interface CLIToolStatusBadgeProps {
  className?: string;
  iconOnly?: boolean;
}

// Refresh every 5 minutes
const REFRESH_INTERVAL_MS = 5 * 60 * 1000;

interface CLIToolPopoverProps {
  cli: 'codex' | 'gemini';
  status: CLIAccountStatus | null;
  Icon: React.ComponentType<{ className?: string }>;
  label: string;
  lastChecked: Date | null;
  onRefresh: () => void | Promise<void>;
  iconOnly?: boolean;
}

function CLIToolPopover({ cli, status, Icon, label, lastChecked, onRefresh, iconOnly = false }: CLIToolPopoverProps) {
  const { t } = useTranslation(['navigation', 'common']);
  const [isOpen, setIsOpen] = useState(false);
  const [isInstalling, setIsInstalling] = useState(false);
  const installed = status?.installed ?? false;
  const authenticated = status?.authenticated ?? false;
  const hasUpdate = installed && status?.latestVersion && status?.version !== status?.latestVersion;

  // Determine status type
  const statusType = !installed ? 'not-installed' : authenticated ? 'authenticated' : 'installed';

  // Dot color for the trigger button
  const dotColor =
    statusType === 'authenticated' ? 'bg-green-500' :
    statusType === 'installed' ? 'bg-yellow-500' :
    'bg-muted-foreground/40';

  // Auth method label
  const getAuthMethodLabel = () => {
    if (!status?.authMethod) return null;
    if (cli === 'codex') {
      return status.authMethod === 'oauth'
        ? t('navigation:cliTools.viaOAuth')
        : t('navigation:cliTools.viaApiKey');
    }
    return status.authMethod === 'google_login'
      ? t('navigation:cliTools.viaGoogleLogin')
      : t('navigation:cliTools.viaApiKey');
  };

  // Tooltip text
  const tooltipText = (() => {
    switch (statusType) {
      case 'authenticated':
        return `${label} — ${t('navigation:cliTools.authenticated')}`;
      case 'installed':
        return `${label} — ${t('navigation:cliTools.needsAuth')}`;
      default:
        return `${label} — ${t('navigation:cliTools.notInstalled')}`;
    }
  })();

  // Status icon inside popover header
  const statusIcon = (() => {
    if (!installed) return <X className="h-3 w-3" />;
    if (hasUpdate) return <AlertTriangle className="h-3 w-3" />;
    return <Check className="h-3 w-3" />;
  })();

  // Status text inside popover header
  const statusText = (() => {
    if (!installed) return t('navigation:cliTools.notInstalled');
    if (hasUpdate) return t('navigation:cliTools.updateAvailable');
    return t('navigation:cliTools.installed');
  })();

  // --- Action handlers ---

  const handleInstall = async () => {
    if (!window.API?.installCLI) return;
    setIsInstalling(true);
    try {
      await window.API.installCLI(cli);
      await onRefresh();
    } catch (err) {
      console.error(`Failed to install/update ${cli} CLI:`, err);
    } finally {
      setIsInstalling(false);
    }
  };

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <StatusBadgeButton
              iconOnly={iconOnly}
              icon={<Icon className="h-4 w-4" />}
              label={label}
              dotColor={dotColor}
              className={cn(
                statusType === 'not-installed' && 'opacity-50',
                statusType === 'installed' && 'text-yellow-600 dark:text-yellow-500',
              )}
            >
              {hasUpdate && (
                <span className="ml-auto text-[10px] bg-blue-500/20 text-blue-600 dark:text-blue-400 px-1.5 py-0.5 rounded">
                  {t('common:update', 'Update')}
                </span>
              )}
              {statusType === 'not-installed' && (
                <span className="ml-auto text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded">
                  {t('navigation:cliTools.notInstalled')}
                </span>
              )}
            </StatusBadgeButton>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side={iconOnly ? 'bottom' : 'right'}>
          {tooltipText}
        </TooltipContent>
      </Tooltip>

      <PopoverContent side={iconOnly ? 'bottom' : 'right'} align="end" className="w-72">
        <div className="space-y-3">
          {/* Header */}
          <div className="flex items-center gap-2">
            <div className={cn(
              'flex h-8 w-8 items-center justify-center rounded-lg',
              cli === 'codex' ? 'bg-emerald-500/10' : 'bg-blue-500/10',
            )}>
              <Icon className={cn(
                'h-4 w-4',
                cli === 'codex' ? 'text-emerald-600 dark:text-emerald-400' : 'text-blue-600 dark:text-blue-400',
              )} />
            </div>
            <div>
              <h4 className="text-sm font-medium">{label}</h4>
              <p className="text-xs text-muted-foreground flex items-center gap-1">
                {statusIcon}
                {statusText}
              </p>
            </div>
          </div>

          {/* Version info */}
          {installed && (
            <div className="text-xs space-y-1 p-2 bg-muted rounded-md">
              {status?.version && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{t('navigation:cliTools.current')}:</span>
                  <span className="font-mono">{status.version}</span>
                </div>
              )}
              {status?.latestVersion && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{t('navigation:cliTools.latest')}:</span>
                  <span className="font-mono">{status.latestVersion}</span>
                </div>
              )}
              {lastChecked && (
                <div className="flex justify-between text-muted-foreground">
                  <span>{t('navigation:cliTools.lastChecked')}:</span>
                  <span>{lastChecked.toLocaleTimeString()}</span>
                </div>
              )}
            </div>
          )}

          {/* Auth status */}
          {installed && (
            <div className={cn(
              'text-xs p-2 rounded-md flex items-start gap-2',
              authenticated
                ? 'bg-green-500/10 text-green-700 dark:text-green-400'
                : 'bg-yellow-500/10 text-yellow-700 dark:text-yellow-400',
            )}>
              <KeyRound className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <div className="flex-1 space-y-0.5">
                {authenticated ? (
                  <>
                    <span className="block">
                      {t('navigation:cliTools.authenticated')}
                      {getAuthMethodLabel() && ` ${getAuthMethodLabel()}`}
                    </span>
                    {status?.email && (
                      <span className="block text-muted-foreground">{status.email}</span>
                    )}
                    {status?.tokenExpiresAt && (
                      <span className="block text-muted-foreground">
                        {t('navigation:cliTools.tokenExpires')}: {new Date(status.tokenExpiresAt).toLocaleDateString()}
                      </span>
                    )}
                  </>
                ) : (
                  <span>{t('navigation:cliTools.needsAuth')}</span>
                )}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="space-y-2">
            <div className="flex gap-2 flex-wrap">
              {/* Install / Update */}
              {(!installed || hasUpdate) && (
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
                  {!installed
                    ? t('common:install', 'Install')
                    : t('common:update', 'Update')
                  }
                </Button>
              )}

              {/* Refresh */}
              <Button
                variant="outline"
                size="sm"
                className="gap-1"
                onClick={onRefresh}
              >
                <RefreshCw className="h-3 w-3" />
                {t('common:refresh', 'Refresh')}
              </Button>
            </div>

          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

/**
 * CLI tool status badges for the sidebar.
 * Shows Codex CLI and Antigravity CLI status with brand icons, colored indicators,
 * and rich popover modals with version info, auth status, and action buttons.
 */
export function CLIToolStatusBadge({ className, iconOnly = false }: CLIToolStatusBadgeProps) {
  const { t } = useTranslation(['settings']);
  const [accounts, setAccounts] = useState<CLIAccountsDetectionResult | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  const detect = useCallback(async () => {
    try {
      if (!window.API?.detectCLIAccounts) return;
      const result = await window.API.detectCLIAccounts();
      if (result.success && result.data) {
        setAccounts(result.data);
        setLastChecked(new Date());
      }
    } catch (err) {
      console.error('Failed to detect CLI accounts:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Initial detection + periodic refresh
  useEffect(() => {
    detect();
    const interval = setInterval(detect, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [detect]);

  // Listen for WebSocket auth events for immediate refresh
  useEffect(() => {
    if (!window.API?.onCLIAccountAuth) return;
    const unsubscribe = window.API.onCLIAccountAuth((info) => {
      if (info.success) {
        detect();
      }
    });
    return unsubscribe;
  }, [detect]);

  if (isLoading) return null;

  const clis: Array<{ key: 'codex' | 'gemini'; Icon: typeof OpenAIIcon; label: string }> = [
    { key: 'codex', Icon: OpenAIIcon, label: t('settings:codex.name', 'Codex CLI') },
    { key: 'gemini', Icon: GeminiIcon, label: t('settings:gemini.name', 'Antigravity CLI') },
  ];

  return (
    <div className={cn(iconOnly ? 'flex items-center gap-1' : 'space-y-0.5', className)}>
      {clis.map(({ key, Icon, label }) => (
        <CLIToolPopover
          key={key}
          cli={key}
          status={accounts?.[key] ?? null}
          Icon={Icon}
          label={label}
          lastChecked={lastChecked}
          onRefresh={detect}
          iconOnly={iconOnly}
        />
      ))}
    </div>
  );
}
