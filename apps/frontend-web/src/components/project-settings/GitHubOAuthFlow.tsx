import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Github,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  Terminal,
  Copy,
  Check,
  Clock,
  Download
} from 'lucide-react';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';

interface GitHubOAuthFlowProps {
  projectId?: string;
  onSuccess: (username?: string) => void;
  onCancel?: () => void;
}

// Debug logging helper - logs when DEBUG env var is set or in development
const DEBUG = import.meta.env.DEV || import.meta.env.VITE_DEBUG === 'true';

function debugLog(message: string, data?: unknown) {
  if (DEBUG) {
    if (data !== undefined) {
      console.warn(`[GitHubOAuth] ${message}`, data);
    } else {
      console.warn(`[GitHubOAuth] ${message}`);
    }
  }
}

// Authentication timeout in milliseconds (5 minutes)
const AUTH_TIMEOUT_MS = 5 * 60 * 1000;
// Poll interval for checking auth completion (3 seconds)
const POLL_INTERVAL_MS = 3000;

/**
 * GitHub OAuth flow component using gh CLI device code flow.
 *
 * Flow:
 * 1. Check if gh CLI is installed → offer install if not
 * 2. Check if already authenticated → skip to success
 * 3. Start auth → backend returns device code + URL immediately
 * 4. User opens URL on any device, enters code
 * 5. Frontend polls /auth/status until complete
 */
export function GitHubOAuthFlow({ projectId, onSuccess, onCancel }: GitHubOAuthFlowProps) {
  const [status, setStatus] = useState<'checking' | 'need-install' | 'need-auth' | 'authenticating' | 'success' | 'error'>('checking');
  const [error, setError] = useState<string | null>(null);
  const [_cliInstalled, setCliInstalled] = useState(false);
  const [cliVersion, setCliVersion] = useState<string | undefined>();
  const [username, setUsername] = useState<string | undefined>();

  // Device flow state
  const [deviceCode, setDeviceCode] = useState<string | null>(null);
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [codeCopied, setCodeCopied] = useState<boolean>(false);
  const [isTimeout, setIsTimeout] = useState<boolean>(false);

  // Install state
  const [isInstalling, setIsInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);

  // Refs for timers
  const authTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const codeCopyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasCheckedRef = useRef(false);

  // Cleanup all timers
  const cleanupTimers = useCallback(() => {
    if (authTimeoutRef.current) {
      clearTimeout(authTimeoutRef.current);
      authTimeoutRef.current = null;
    }
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cleanupTimers();
      if (codeCopyTimeoutRef.current) {
        clearTimeout(codeCopyTimeoutRef.current);
      }
    };
  }, [cleanupTimers]);

  // Initial check on mount
  useEffect(() => {
    if (hasCheckedRef.current) return;
    hasCheckedRef.current = true;
    debugLog('Component mounted, checking GitHub status...');
    checkGitHubStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const checkGitHubStatus = async () => {
    debugLog('checkGitHubStatus() called');
    setStatus('checking');
    setError(null);

    try {
      const cliResult = await window.API.checkGitHubCli();
      debugLog('checkGitHubCli result:', cliResult);

      if (!cliResult.success) {
        setError(cliResult.error || 'Failed to check GitHub CLI');
        setStatus('error');
        return;
      }

      if (!cliResult.data?.installed) {
        setStatus('need-install');
        setCliInstalled(false);
        return;
      }

      setCliInstalled(true);
      setCliVersion(cliResult.data.version);

      // Check if already authenticated
      const authResult = await window.API.checkGitHubAuth();
      debugLog('checkGitHubAuth result:', authResult);

      if (authResult.success && authResult.data?.authenticated) {
        debugLog('Already authenticated as:', authResult.data.username);
        setUsername(authResult.data.username);
        await fetchAndNotifyToken();
      } else {
        setStatus('need-auth');
      }
    } catch (err) {
      debugLog('Error in checkGitHubStatus:', err);
      setError(err instanceof Error ? err.message : 'Unknown error');
      setStatus('error');
    }
  };

  const fetchAndNotifyToken = async () => {
    debugLog('fetchAndNotifyToken() called');
    try {
      if (projectId) {
        const persistResult = await window.API.persistGitHubToken(projectId);
        if (persistResult.success && persistResult.data?.tokenPersisted) {
          setStatus('success');
          onSuccess(username);
        } else {
          setError(persistResult.error || 'Failed to persist token');
          setStatus('error');
        }
      } else {
        const authResult = await window.API.checkGitHubAuth();
        if (authResult.success && authResult.data?.authenticated) {
          setStatus('success');
          onSuccess(authResult.data.username);
        } else {
          setError('Authentication could not be confirmed');
          setStatus('error');
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to persist token');
      setStatus('error');
    }
  };

  // Poll for auth completion
  const startPolling = useCallback(() => {
    debugLog('Starting auth status polling');
    pollIntervalRef.current = setInterval(async () => {
      try {
        const result = await window.API.checkGitHubAuthStatus();
        debugLog('Auth status poll:', result);

        if (result.success && result.data?.complete) {
          cleanupTimers();

          if (result.data.success) {
            debugLog('Auth completed successfully');
            await fetchAndNotifyToken();
          } else {
            setError(result.data.error || 'Authentication failed');
            setStatus('error');
          }
        }
      } catch (err) {
        debugLog('Polling error:', err);
        // Don't stop polling on transient errors
      }
    }, POLL_INTERVAL_MS);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cleanupTimers, projectId]);

  const handleStartAuth = async () => {
    debugLog('handleStartAuth() called');
    setStatus('authenticating');
    setError(null);
    setDeviceCode(null);
    setAuthUrl(null);
    setCodeCopied(false);
    setIsTimeout(false);
    cleanupTimers();

    // Set timeout
    authTimeoutRef.current = setTimeout(() => {
      debugLog('Authentication timeout triggered');
      cleanupTimers();
      setIsTimeout(true);
      setError('Authentication timed out after 5 minutes. Please try again.');
      setStatus('error');
    }, AUTH_TIMEOUT_MS);

    try {
      debugLog('Calling startGitHubAuth...');
      const result = await window.API.startGitHubAuth();
      debugLog('startGitHubAuth result:', result);

      if (!result.success || !result.data?.success) {
        cleanupTimers();
        // Check if already authenticated
        if (result.data?.message?.includes('Already authenticated')) {
          await fetchAndNotifyToken();
          return;
        }
        setError(result.data?.message || result.error || 'Failed to start authentication');
        setStatus('error');
        return;
      }

      // Got device code — show it and start polling
      if (result.data.deviceCode) {
        setDeviceCode(result.data.deviceCode);
      }
      if (result.data.authUrl) {
        setAuthUrl(result.data.authUrl);
      }

      // Start polling for completion
      if (result.data.awaiting) {
        startPolling();
      } else {
        // Auth completed immediately (shouldn't happen but handle it)
        cleanupTimers();
        await fetchAndNotifyToken();
      }
    } catch (err) {
      cleanupTimers();
      debugLog('Error in handleStartAuth:', err);
      setError(err instanceof Error ? err.message : 'Authentication failed');
      setStatus('error');
    }
  };

  const handleInstallGhCli = async () => {
    debugLog('Installing GitHub CLI...');
    setIsInstalling(true);
    setInstallError(null);

    try {
      const result = await window.API.installGitHubCli();
      if (result.success) {
        checkGitHubStatus();
      } else {
        setInstallError(result.error || 'Installation failed');
      }
    } catch (err) {
      setInstallError(err instanceof Error ? err.message : 'Installation failed');
    } finally {
      setIsInstalling(false);
    }
  };

  const handleRetry = () => {
    cleanupTimers();
    hasCheckedRef.current = false;
    checkGitHubStatus();
  };

  const handleCopyDeviceCode = async () => {
    if (!deviceCode) return;
    try {
      await navigator.clipboard.writeText(deviceCode);
      setCodeCopied(true);
      if (codeCopyTimeoutRef.current) clearTimeout(codeCopyTimeoutRef.current);
      codeCopyTimeoutRef.current = setTimeout(() => setCodeCopied(false), 2000);
    } catch (err) {
      debugLog('Failed to copy device code:', err);
    }
  };

  return (
    <div className="space-y-4">
      {/* Checking status */}
      {status === 'checking' && (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Need to install gh CLI */}
      {status === 'need-install' && (
        <div className="space-y-4">
          <Card className="border border-warning/30 bg-warning/10">
            <CardContent className="p-5">
              <div className="flex items-start gap-4">
                <Terminal className="h-6 w-6 text-warning shrink-0 mt-0.5" />
                <div className="flex-1 space-y-3">
                  <h3 className="text-lg font-medium text-foreground">
                    GitHub CLI Required
                  </h3>
                  <p className="text-sm text-muted-foreground">
                    The GitHub CLI (gh) is required for OAuth authentication. Click below to
                    install it automatically.
                  </p>
                  {installError && (
                    <div className="rounded-lg bg-destructive/10 border border-destructive/30 p-3 text-sm text-destructive">
                      {installError}
                    </div>
                  )}
                  <div className="flex gap-3">
                    <Button onClick={handleInstallGhCli} disabled={isInstalling} className="gap-2">
                      {isInstalling ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Installing...
                        </>
                      ) : (
                        <>
                          <Download className="h-4 w-4" />
                          Install GitHub CLI
                        </>
                      )}
                    </Button>
                    <Button variant="outline" onClick={handleRetry} disabled={isInstalling}>
                      I've Installed It
                    </Button>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Need authentication */}
      {status === 'need-auth' && (
        <div className="space-y-4">
          <Card className="border border-info/30 bg-info/10">
            <CardContent className="p-5">
              <div className="flex items-start gap-4">
                <Github className="h-6 w-6 text-info shrink-0 mt-0.5" />
                <div className="flex-1 space-y-3">
                  <h3 className="text-lg font-medium text-foreground">
                    Connect to GitHub
                  </h3>
                  <p className="text-sm text-muted-foreground">
                    Click the button below to start the GitHub device code authentication flow.
                    You'll receive a code to enter at github.com from any device.
                  </p>
                  {cliVersion && (
                    <p className="text-xs text-muted-foreground">
                      Using GitHub CLI {cliVersion}
                    </p>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="flex justify-center">
            <Button onClick={handleStartAuth} size="lg" className="gap-2">
              <Github className="h-5 w-5" />
              Authenticate with GitHub
            </Button>
          </div>
        </div>
      )}

      {/* Authenticating — device code display */}
      {status === 'authenticating' && (
        <div className="space-y-4">
          {deviceCode ? (
            <Card className="border border-primary/30 bg-primary/5">
              <CardContent className="p-6">
                <div className="text-center space-y-4">
                  <div className="space-y-2">
                    <p className="text-sm font-medium text-foreground">
                      Your one-time code
                    </p>
                    <div className="flex items-center justify-center gap-3">
                      <code className="text-3xl font-mono font-bold tracking-widest text-primary px-4 py-2 bg-primary/10 rounded-lg">
                        {deviceCode}
                      </code>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleCopyDeviceCode}
                        className="shrink-0"
                      >
                        {codeCopied ? (
                          <>
                            <Check className="h-4 w-4 mr-1 text-success" />
                            Copied
                          </>
                        ) : (
                          <>
                            <Copy className="h-4 w-4 mr-1" />
                            Copy
                          </>
                        )}
                      </Button>
                    </div>
                  </div>

                  <div className="text-sm text-muted-foreground space-y-3">
                    <p>
                      Open the link below on any device and enter this code to authenticate.
                    </p>
                    {authUrl && (
                      <Button
                        variant="secondary"
                        onClick={() => window.open(authUrl, '_blank')}
                        className="gap-2"
                      >
                        <ExternalLink className="h-4 w-4" />
                        Open {authUrl}
                      </Button>
                    )}
                  </div>

                  <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground pt-2">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    <span>Waiting for authentication to complete...</span>
                  </div>
                </div>
              </CardContent>
            </Card>
          ) : (
            <Card className="border border-info/30 bg-info/10">
              <CardContent className="p-6">
                <div className="flex items-center gap-4">
                  <Loader2 className="h-6 w-6 animate-spin text-info shrink-0" />
                  <div className="flex-1">
                    <h3 className="text-lg font-medium text-foreground">
                      Starting authentication...
                    </h3>
                    <p className="text-sm text-muted-foreground mt-1">
                      Requesting device code from GitHub...
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Success */}
      {status === 'success' && (
        <Card className="border border-success/30 bg-success/10">
          <CardContent className="p-6">
            <div className="flex items-start gap-4">
              <CheckCircle2 className="h-6 w-6 text-success shrink-0 mt-0.5" />
              <div className="flex-1">
                <h3 className="text-lg font-medium text-success">
                  Successfully Connected
                </h3>
                <p className="text-sm text-success/80 mt-1">
                  {username ? `Connected as ${username}` : 'Your GitHub account is now connected'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Error */}
      {status === 'error' && error && (
        <div className="space-y-4">
          <Card className={`border ${isTimeout ? 'border-warning/30 bg-warning/10' : 'border-destructive/30 bg-destructive/10'}`}>
            <CardContent className="p-5">
              <div className="flex items-start gap-3">
                {isTimeout ? (
                  <Clock className="h-5 w-5 text-warning shrink-0 mt-0.5" />
                ) : (
                  <AlertCircle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
                )}
                <div className="flex-1">
                  <h3 className={`text-lg font-medium ${isTimeout ? 'text-warning' : 'text-destructive'}`}>
                    {isTimeout ? 'Authentication Timed Out' : 'Authentication Failed'}
                  </h3>
                  <p className={`text-sm mt-1 ${isTimeout ? 'text-warning/80' : 'text-destructive/80'}`}>{error}</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="flex justify-center gap-3">
            <Button onClick={handleStartAuth} variant="outline">
              Retry
            </Button>
            {onCancel && (
              <Button onClick={onCancel} variant="ghost">
                Cancel
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Cancel button for non-error states */}
      {status !== 'error' && status !== 'success' && onCancel && (
        <div className="flex justify-center pt-2">
          <Button onClick={onCancel} variant="ghost">
            Cancel
          </Button>
        </div>
      )}
    </div>
  );
}
