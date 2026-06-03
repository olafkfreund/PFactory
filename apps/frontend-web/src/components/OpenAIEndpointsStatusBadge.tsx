import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, X, Loader2, RefreshCw, Globe, Activity } from 'lucide-react';
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
import { get, post } from '../lib/api-client';

interface LLMEndpoint {
  id: string;
  label: string;
  base_url: string;
  api_key_preview: string | null;
  default_model: string;
  headers: Record<string, string> | null;
  created_at: string;
  updated_at: string;
}

interface TestResult {
  ok: boolean;
  status_code: number | null;
  models: string[];
  error: string | null;
}

interface OpenAIEndpointsStatusBadgeProps {
  className?: string;
  iconOnly?: boolean;
}

// Refresh every 5 minutes (matches CLIToolStatusBadge)
const REFRESH_INTERVAL_MS = 5 * 60 * 1000;

interface EndpointPopoverProps {
  endpoint: LLMEndpoint;
  status: TestResult | null;
  isProbing: boolean;
  lastChecked: Date | null;
  onProbe: () => void | Promise<void>;
  iconOnly: boolean;
}

function EndpointPopover({
  endpoint,
  status,
  isProbing,
  lastChecked,
  onProbe,
  iconOnly,
}: EndpointPopoverProps) {
  const { t } = useTranslation(['settings', 'common']);
  const [isOpen, setIsOpen] = useState(false);

  const ok = status?.ok ?? false;
  const probed = status !== null;
  const dotColor = !probed
    ? 'bg-muted-foreground/40'
    : ok
      ? 'bg-green-500'
      : 'bg-red-500';

  const statusType: 'ok' | 'failed' | 'unprobed' = !probed
    ? 'unprobed'
    : ok
      ? 'ok'
      : 'failed';

  const tooltipText = (() => {
    if (statusType === 'ok') {
      return `${endpoint.label} — ${t('settings:openaiEndpoints.testSuccess', 'Connection successful')}`;
    }
    if (statusType === 'failed') {
      return `${endpoint.label} — ${t('settings:openaiEndpoints.testFailed', 'Connection failed')}`;
    }
    return `${endpoint.label} — ${endpoint.base_url}`;
  })();

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <StatusBadgeButton
              iconOnly={iconOnly}
              icon={<Globe className="h-4 w-4" />}
              label={endpoint.label}
              dotColor={dotColor}
            />
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side={iconOnly ? 'bottom' : 'right'}>
          {tooltipText}
        </TooltipContent>
      </Tooltip>

      <PopoverContent
        side={iconOnly ? 'bottom' : 'right'}
        align="end"
        className="w-80"
      >
        <div className="space-y-3">
          {/* Header */}
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-success/10">
              <Globe className="h-4 w-4 text-success" />
            </div>
            <div className="min-w-0">
              <h4 className="text-sm font-medium truncate">{endpoint.label}</h4>
              <p className="text-xs text-muted-foreground flex items-center gap-1">
                {statusType === 'ok' && <Check className="h-3 w-3 text-green-600" />}
                {statusType === 'failed' && <X className="h-3 w-3 text-red-600" />}
                {statusType === 'ok'
                  ? t('settings:openaiEndpoints.testSuccess', 'Connection successful')
                  : statusType === 'failed'
                    ? t('settings:openaiEndpoints.testFailed', 'Connection failed')
                    : t('settings:openaiEndpoints.unprobed', 'Not tested yet')}
              </p>
            </div>
          </div>

          {/* Details */}
          <div className="text-xs space-y-1.5 p-2 bg-muted rounded-md">
            <div className="flex justify-between gap-2">
              <span className="text-muted-foreground shrink-0">
                {t('settings:openaiEndpoints.baseUrl', 'Base URL')}:
              </span>
              <span className="font-mono truncate" title={endpoint.base_url}>
                {endpoint.base_url}
              </span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-muted-foreground shrink-0">
                {t('settings:openaiEndpoints.defaultModel', 'Default model')}:
              </span>
              <span className="font-mono truncate" title={endpoint.default_model}>
                {endpoint.default_model}
              </span>
            </div>
            {endpoint.api_key_preview && (
              <div className="flex justify-between gap-2">
                <span className="text-muted-foreground shrink-0">
                  {t('settings:openaiEndpoints.apiKey', 'API key')}:
                </span>
                <span className="font-mono truncate">{endpoint.api_key_preview}</span>
              </div>
            )}
            {lastChecked && (
              <div className="flex justify-between text-muted-foreground">
                <span>{t('common:lastChecked', 'Last checked')}:</span>
                <span>{lastChecked.toLocaleTimeString()}</span>
              </div>
            )}
          </div>

          {/* Test error */}
          {statusType === 'failed' && status?.error && (
            <div className="text-xs p-2 rounded-md bg-red-500/10 text-red-700 dark:text-red-400 flex items-start gap-2">
              <X className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span className="break-all">{status.error}</span>
            </div>
          )}

          {/* Available models on success */}
          {statusType === 'ok' && status && status.models.length > 0 && (
            <div className="text-xs p-2 rounded-md bg-green-500/10 text-green-700 dark:text-green-400">
              <div className="font-medium mb-1">
                {t('settings:openaiEndpoints.modelsFound', '{{count}} models', {
                  count: status.models.length,
                })}
              </div>
              <div className="font-mono text-[10px] opacity-75 max-h-24 overflow-y-auto">
                {status.models.join(', ')}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2 flex-wrap">
            <Button
              size="sm"
              variant="outline"
              className="flex-1 gap-1"
              onClick={onProbe}
              disabled={isProbing}
            >
              {isProbing ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Activity className="h-3 w-3" />
              )}
              {t('settings:openaiEndpoints.test', 'Test')}
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

/**
 * Status badge for user-configured OpenAI-compatible endpoints.
 * Mirrors CLIToolStatusBadge: one icon per saved endpoint, with status dot
 * (green = healthy, red = failed, gray = unprobed) and a popover showing
 * label, base URL, default model and probe details.
 */
export function OpenAIEndpointsStatusBadge({
  className,
  iconOnly = false,
}: OpenAIEndpointsStatusBadgeProps) {
  const [endpoints, setEndpoints] = useState<LLMEndpoint[]>([]);
  const [statuses, setStatuses] = useState<Record<string, TestResult>>({});
  const [probing, setProbing] = useState<Record<string, boolean>>({});
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Use ref so probeAll can read latest endpoints without re-triggering effects
  const endpointsRef = useRef<LLMEndpoint[]>([]);
  endpointsRef.current = endpoints;

  const probeOne = useCallback(async (endpointId: string) => {
    setProbing((prev) => ({ ...prev, [endpointId]: true }));
    const result = await post<TestResult>(`/llm-endpoints/${endpointId}/test`, {});
    if (result.success && result.data) {
      setStatuses((prev) => ({ ...prev, [endpointId]: result.data! }));
    } else {
      setStatuses((prev) => ({
        ...prev,
        [endpointId]: {
          ok: false,
          status_code: null,
          models: [],
          error: result.error || 'Probe failed',
        },
      }));
    }
    setProbing((prev) => ({ ...prev, [endpointId]: false }));
  }, []);

  const probeAll = useCallback(async () => {
    const list = endpointsRef.current;
    if (list.length === 0) return;
    await Promise.all(list.map((e) => probeOne(e.id)));
    setLastChecked(new Date());
  }, [probeOne]);

  const loadEndpoints = useCallback(async () => {
    const result = await get<LLMEndpoint[]>('/llm-endpoints');
    if (result.success && result.data) {
      setEndpoints(result.data);
    }
    setIsLoading(false);
  }, []);

  // Initial load + probe
  useEffect(() => {
    loadEndpoints();
  }, [loadEndpoints]);

  // After endpoints load, do an initial probe and start periodic refresh
  useEffect(() => {
    if (endpoints.length === 0) return;
    probeAll();
    const interval = setInterval(probeAll, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [endpoints, probeAll]);

  if (isLoading || endpoints.length === 0) return null;

  return (
    <div className={cn(iconOnly ? 'flex items-center gap-1' : 'space-y-0.5', className)}>
      {endpoints.map((endpoint) => (
        <EndpointPopover
          key={endpoint.id}
          endpoint={endpoint}
          status={statuses[endpoint.id] ?? null}
          isProbing={!!probing[endpoint.id]}
          lastChecked={lastChecked}
          onProbe={() => probeOne(endpoint.id)}
          iconOnly={iconOnly}
        />
      ))}
    </div>
  );
}
