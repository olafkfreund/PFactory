import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Bug, FileText, Loader2, AlertCircle, Check } from 'lucide-react';
import { SettingsSection } from './SettingsSection';
import { LogViewer } from './LogViewer';

interface DebugInfo {
  systemInfo: Record<string, string>;
  recentErrors: string[];
  logsPath: string;
  debugReport: string;
}

/**
 * Debug settings component for accessing logs and debug information
 */
export function DebugSettings() {
  const { t } = useTranslation('settings');
  const [debugInfo, setDebugInfo] = useState<DebugInfo | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Automatically load debug info on component mount
  useEffect(() => {
    const loadDebugInfo = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const info = await window.API.getDebugInfo();
        setDebugInfo(info);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load debug info');
      } finally {
        setIsLoading(false);
      }
    };

    loadDebugInfo();
  }, []);

  return (
    <SettingsSection
      title={t('debug.title', 'Debug & Logs')}
      description={t('debug.description', 'Access logs and debug information for troubleshooting')}
    >
      <div className="space-y-6">
        {/* Loading Indicator */}
        {isLoading && (
          <div className="flex items-center gap-2 p-3 rounded-md bg-muted/30 text-muted-foreground text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t('debug.loading', 'Loading debug information...')}
          </div>
        )}

        {/* Error Display */}
        {error && (
          <div className="flex items-start gap-2 p-3 rounded-md bg-destructive/10 text-destructive text-sm">
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
            {error}
          </div>
        )}

        {/* Debug Info Display */}
        {debugInfo && (
          <div className="space-y-4">
            {/* System Information */}
            <div className="rounded-lg border border-border p-4">
              <h4 className="font-medium text-sm mb-3 flex items-center gap-2">
                <Bug className="h-4 w-4" />
                {t('debug.systemInfo', 'System Information')}
              </h4>
              <div className="grid grid-cols-2 gap-2 text-xs">
                {Object.entries(debugInfo.systemInfo).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-2">
                    <span className="text-muted-foreground">{key}:</span>
                    <span className="font-mono text-right truncate" title={value}>{value}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Logs Path */}
            <div className="rounded-lg border border-border p-4">
              <h4 className="font-medium text-sm mb-2 flex items-center gap-2">
                <FileText className="h-4 w-4" />
                {t('debug.logsLocation', 'Logs Location')}
              </h4>
              <code className="text-xs text-muted-foreground bg-muted/50 px-2 py-1 rounded block truncate">
                {debugInfo.logsPath}
              </code>
            </div>

            {/* Recent Errors */}
            {debugInfo.recentErrors.length > 0 && (
              <div className="rounded-lg border border-border p-4">
                <h4 className="font-medium text-sm mb-3 flex items-center gap-2">
                  <AlertCircle className="h-4 w-4 text-amber-500" />
                  {t('debug.recentErrors', 'Recent Errors')} ({debugInfo.recentErrors.length})
                </h4>
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {debugInfo.recentErrors.map((error, index) => (
                    <div key={index} className="text-xs font-mono text-muted-foreground bg-muted/30 px-2 py-1 rounded">
                      {error}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {debugInfo.recentErrors.length === 0 && (
              <div className="rounded-lg border border-border p-4">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Check className="h-4 w-4 text-green-500" />
                  {t('debug.noRecentErrors', 'No recent errors')}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Help Text */}
        <div className="text-xs text-muted-foreground bg-muted/30 p-3 rounded-md">
          <p className="font-medium mb-1">{t('debug.helpTitle', 'Reporting Issues')}</p>
          <p>
            {t('debug.helpText', 'When reporting bugs, include the system information and recent errors shown above to help us diagnose the issue.')}
          </p>
        </div>

        {/* Application Logs Section */}
        <div className="mt-6">
          <LogViewer />
        </div>
      </div>
    </SettingsSection>
  );
}
