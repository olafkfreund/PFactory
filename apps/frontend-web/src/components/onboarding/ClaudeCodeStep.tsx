import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Terminal, Loader2, Check, AlertTriangle, X, RefreshCw, Download, Info, ExternalLink } from 'lucide-react';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { Progress } from '../ui/progress';
import type { ClaudeCodeVersionInfo } from '../../shared/types/cli';

interface ClaudeCodeStepProps {
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
}

type DetectionStatus = 'loading' | 'installed' | 'outdated' | 'not-found' | 'error';

/**
 * Claude Code CLI installation step for the onboarding wizard.
 *
 * Checks if Claude Code CLI is installed, shows version information,
 * and provides one-click installation/update functionality.
 */
export function ClaudeCodeStep({ onNext, onBack, onSkip }: ClaudeCodeStepProps) {
  const { t } = useTranslation('onboarding');
  const [status, setStatus] = useState<DetectionStatus>('loading');
  const [versionInfo, setVersionInfo] = useState<ClaudeCodeVersionInfo | null>(null);
  const [isInstalling, setIsInstalling] = useState(false);
  const [installProgress, setInstallProgress] = useState(0);
  const [installStepLabel, setInstallStepLabel] = useState('');
  const progressIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [installSuccess, setInstallSuccess] = useState(false);

  // Check Claude Code version on mount
  const checkVersion = useCallback(async () => {
    setStatus('loading');
    setError(null);
    setInstallSuccess(false);

    try {
      if (!window.API?.checkClaudeCodeVersion) {
        console.warn('[ClaudeCodeStep] Version check API not available');
        setStatus('error');
        setError('Version check API not available');
        return;
      }

      const result = await window.API.checkClaudeCodeVersion();

      if (result.success && result.data) {
        setVersionInfo(result.data);

        if (!result.data.installed) {
          setStatus('not-found');
        } else if (result.data.isOutdated) {
          setStatus('outdated');
        } else {
          setStatus('installed');
        }
      } else {
        setStatus('error');
        setError(result.error || 'Failed to check version');
      }
    } catch (err) {
      console.error('Failed to check Claude Code version:', err);
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Unknown error');
    }
  }, []);

  useEffect(() => {
    checkVersion();
  }, [checkVersion]);

  // Auto-refresh version check every 10 seconds when not installed
  // This detects if the user installs Claude Code manually in a terminal
  useEffect(() => {
    if (status !== 'not-found' && status !== 'error') return;
    if (isInstalling) return;

    const interval = setInterval(() => {
      checkVersion();
    }, 10000);

    return () => clearInterval(interval);
  }, [status, isInstalling, checkVersion]);

  // Cleanup progress interval on unmount
  useEffect(() => {
    return () => {
      if (progressIntervalRef.current) {
        clearInterval(progressIntervalRef.current);
      }
    };
  }, []);

  // Handle install/update button click
  const handleInstall = async () => {
    setIsInstalling(true);
    setError(null);
    setInstallProgress(0);

    // Determine if Node.js needs to be installed first
    const needsNode = versionInfo?.nodeAvailable === false;
    const stepLabels = needsNode
      ? [
          t('claudeCode.install.stepFnm', 'Installing Node.js runtime...'),
          t('claudeCode.install.stepNode', 'Setting up Node.js LTS...'),
          t('claudeCode.install.stepClaude', 'Installing Claude Code CLI...'),
        ]
      : [t('claudeCode.install.stepClaude', 'Installing Claude Code CLI...')];

    let stepIndex = 0;
    setInstallStepLabel(stepLabels[0]);

    // Simulated progress with cycling step labels
    const startTime = Date.now();
    const totalDuration = needsNode ? 90 : 60; // seconds
    const stepInterval = totalDuration / stepLabels.length;

    progressIntervalRef.current = setInterval(() => {
      const elapsed = (Date.now() - startTime) / 1000;
      // Ease-out curve: fast at start, slows toward 90%
      const progress = Math.min(90, (elapsed / totalDuration) * 90 * (1 - Math.exp(-elapsed / 20)));
      setInstallProgress(Math.round(progress));

      // Cycle through step labels
      const newStepIndex = Math.min(stepLabels.length - 1, Math.floor(elapsed / stepInterval));
      if (newStepIndex !== stepIndex) {
        stepIndex = newStepIndex;
        setInstallStepLabel(stepLabels[stepIndex]);
      }
    }, 500);

    try {
      if (!window.API?.installClaudeCode) {
        setError('Install API not available');
        return;
      }

      const result = await window.API.installClaudeCode();

      // Stop progress animation
      if (progressIntervalRef.current) {
        clearInterval(progressIntervalRef.current);
        progressIntervalRef.current = null;
      }

      if (result.success) {
        setInstallProgress(100);
        setInstallStepLabel(t('claudeCode.install.stepDone', 'Installation complete'));
        setInstallSuccess(true);
        // Update status directly from install response instead of calling checkVersion()
        // (checkVersion resets installSuccess and may fail to find claude)
        const installData = result.data as Record<string, unknown> | undefined;
        const version = (installData?.version as string) || undefined;
        if (version) {
          setVersionInfo({
            installed: version,
            latest: 'unknown',
            isOutdated: false,
            path: undefined,
            detectionResult: { found: true, source: 'system-path', message: 'Installed via npm' }
          });
          setStatus('installed');
        } else {
          checkVersion();
        }
      } else {
        setInstallProgress(0);
        setInstallStepLabel('');
        setError(result.error || 'Failed to install');
      }
    } catch (err) {
      if (progressIntervalRef.current) {
        clearInterval(progressIntervalRef.current);
        progressIntervalRef.current = null;
      }
      setInstallProgress(0);
      setInstallStepLabel('');
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setIsInstalling(false);
    }
  };

  // Get status icon
  const getStatusIcon = () => {
    switch (status) {
      case 'loading':
        return <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />;
      case 'installed':
        return <Check className="h-6 w-6 text-green-500" />;
      case 'outdated':
        return <AlertTriangle className="h-6 w-6 text-yellow-500" />;
      case 'not-found':
        return <X className="h-6 w-6 text-destructive" />;
      case 'error':
        return <AlertTriangle className="h-6 w-6 text-destructive" />;
    }
  };

  // Get status text
  const getStatusText = () => {
    switch (status) {
      case 'loading':
        return t('claudeCode.detecting', 'Checking Claude Code installation...');
      case 'installed':
        return t('claudeCode.status.installed', 'Installed');
      case 'outdated':
        return t('claudeCode.status.outdated', 'Update Available');
      case 'not-found':
        return t('claudeCode.status.notFound', 'Not Installed');
      case 'error':
        return error || 'Error checking status';
    }
  };

  // Get status color class
  const getStatusColorClass = () => {
    switch (status) {
      case 'installed':
        return 'text-green-500';
      case 'outdated':
        return 'text-yellow-500';
      case 'not-found':
      case 'error':
        return 'text-destructive';
      default:
        return 'text-muted-foreground';
    }
  };

  return (
    <div className="flex h-full flex-col items-center justify-center px-8 py-6">
      <div className="w-full max-w-2xl">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="flex justify-center mb-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Terminal className="h-7 w-7" />
            </div>
          </div>
          <h1 className="text-2xl font-bold text-foreground tracking-tight">
            {t('claudeCode.title', 'Claude Code CLI')}
          </h1>
          <p className="mt-2 text-muted-foreground">
            {t('claudeCode.description', 'Install or update the Claude Code CLI to enable AI-powered features')}
          </p>
        </div>

        {/* Main content */}
        <div className="space-y-6">
          {/* Info card */}
          <Card className="border border-info/30 bg-info/10">
            <CardContent className="p-5">
              <div className="flex items-start gap-4">
                <Info className="h-5 w-5 text-info shrink-0 mt-0.5" />
                <div className="flex-1 space-y-3">
                  <p className="text-sm font-medium text-foreground">
                    {t('claudeCode.info.title', 'What is Claude Code?')}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    {t('claudeCode.info.description', "Claude Code is Anthropic's official CLI that powers AI Factory's AI features. It provides secure authentication and direct access to Claude models.")}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Status card */}
          <Card className={`border ${status === 'installed' ? 'border-green-500/30 bg-green-500/5' : status === 'outdated' ? 'border-yellow-500/30 bg-yellow-500/5' : status === 'not-found' || status === 'error' ? 'border-destructive/30 bg-destructive/5' : 'border-border'}`}>
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  {getStatusIcon()}
                  <div>
                    <p className={`text-sm font-medium ${getStatusColorClass()}`}>
                      {getStatusText()}
                    </p>
                    {versionInfo && status !== 'loading' && (
                      <div className="mt-1 text-xs text-muted-foreground space-y-0.5">
                        {versionInfo.installed && (
                          <p>
                            {t('claudeCode.version.current', 'Current Version')}: <span className="font-mono">{versionInfo.installed}</span>
                          </p>
                        )}
                        {versionInfo.latest && versionInfo.latest !== 'unknown' && (
                          <p>
                            {t('claudeCode.version.latest', 'Latest Version')}: <span className="font-mono">{versionInfo.latest}</span>
                          </p>
                        )}
                        {versionInfo.path && (
                          <p className="truncate max-w-md" title={versionInfo.path}>
                            Path: <span className="font-mono">{versionInfo.path}</span>
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                {/* Refresh button */}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={checkVersion}
                  disabled={status === 'loading' || isInstalling}
                >
                  <RefreshCw className={`h-4 w-4 ${status === 'loading' ? 'animate-spin' : ''}`} />
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Error message */}
          {error && status !== 'loading' && (
            <Card className="border border-destructive/30 bg-destructive/10">
              <CardContent className="p-4">
                <p className="text-sm text-destructive">{error}</p>
              </CardContent>
            </Card>
          )}

          {/* Install success message */}
          {installSuccess && (
            <Card className="border border-green-500/30 bg-green-500/10">
              <CardContent className="p-4">
                <p className="text-sm text-green-700 dark:text-green-400">
                  {t('claudeCode.install.successComplete', 'Claude Code CLI installed successfully!')}
                </p>
              </CardContent>
            </Card>
          )}

          {/* Install/Update section */}
          {(status === 'not-found' || status === 'outdated') && !installSuccess && (
            <div className="space-y-3">
              {/* Info note when Node.js is also missing */}
              {versionInfo?.nodeAvailable === false && !isInstalling && (
                <Card className="border border-info/30 bg-info/5">
                  <CardContent className="p-4">
                    <p className="text-xs text-muted-foreground">
                      {t('claudeCode.install.nodeWillInstall', 'Node.js will be installed automatically alongside Claude Code.')}
                    </p>
                  </CardContent>
                </Card>
              )}

              <div className="flex justify-center">
                <Button
                  onClick={handleInstall}
                  disabled={isInstalling}
                  size="lg"
                  className="gap-2"
                >
                  {isInstalling ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {t('claudeCode.install.inProgress', 'Installing...')}
                    </>
                  ) : (
                    <>
                      <Download className="h-4 w-4" />
                      {status === 'outdated'
                        ? t('claudeCode.install.updating', 'Update Claude Code')
                        : t('claudeCode.install.button', 'Install Claude Code')
                      }
                    </>
                  )}
                </Button>
              </div>

              {/* Progress bar during installation */}
              {isInstalling && (
                <div className="space-y-2 px-4">
                  <Progress value={installProgress} animated className="h-2" />
                  <div className="flex justify-between items-center">
                    <p className="text-xs text-muted-foreground">{installStepLabel}</p>
                    <p className="text-xs text-muted-foreground font-mono">{installProgress}%</p>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Documentation link */}
          <div className="flex justify-center">
            <Button
              variant="link"
              size="sm"
              className="text-muted-foreground gap-1"
              onClick={() => window.API?.openExternal?.('https://claude.ai/code')}
            >
              {t('claudeCode.learnMore', 'Learn more about Claude Code')}
              <ExternalLink className="h-3 w-3" />
            </Button>
          </div>
        </div>

        {/* Navigation buttons */}
        <div className="flex justify-between mt-8 pt-6 border-t border-border">
          <Button variant="outline" onClick={onBack}>
            {t('common:back', 'Back')}
          </Button>

          <div className="flex gap-3">
            <Button variant="ghost" onClick={onSkip}>
              {t('common:skip', 'Skip')}
            </Button>
            <Button
              onClick={onNext}
              disabled={status === 'loading'}
            >
              {status === 'installed'
                ? t('common:continue', 'Continue')
                : t('common:continueAnyway', 'Continue Anyway')
              }
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
