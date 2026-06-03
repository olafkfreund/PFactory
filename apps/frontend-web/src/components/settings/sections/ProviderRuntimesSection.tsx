/**
 * Provider Runtimes panel (#121 phase 3).
 *
 * Shows installed-vs-latest for each provider CLI/SDK (Claude · Codex ·
 * Copilot · Gemini/Antigravity · Ollama) and lets the operator update to
 * latest or pin a known-good version for rollback. Backed by
 * GET/POST /api/provider-runtimes (see routes/provider_runtimes.py).
 */
import { useCallback, useEffect, useState } from 'react';
import {
  ArrowUpCircle,
  CheckCircle2,
  Loader2,
  Pin,
  PinOff,
  RefreshCw,
  XCircle,
} from 'lucide-react';
import { get, post } from '../../../lib/api-client';
import { Button } from '../../ui/button';

interface Runtime {
  name: string;
  kind: string;
  managed: boolean;
  installed: boolean;
  installedVersion: string | null;
  latestVersion: string | null;
  pinnedVersion: string | null;
  updateAvailable: boolean;
}

export function ProviderRuntimesSection() {
  const [runtimes, setRuntimes] = useState<Runtime[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (checkLatest: boolean) => {
    setLoading(true);
    setError(null);
    const res = await get<{ runtimes: Runtime[] }>(
      `/provider-runtimes?check_latest=${checkLatest}`
    );
    if (res.success && res.data) setRuntimes(res.data.runtimes);
    else setError(res.error ?? 'Failed to load provider runtimes');
    setLoading(false);
  }, []);

  useEffect(() => {
    void load(true);
  }, [load]);

  const doUpdate = async (name: string) => {
    setBusy(name);
    await post(`/provider-runtimes/${name}/update`, {});
    await load(false);
    setBusy(null);
  };

  const doPin = async (name: string, version: string | null) => {
    setBusy(name);
    await post(`/provider-runtimes/${name}/pin`, { version });
    await load(false);
    setBusy(null);
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading provider runtimes…
      </div>
    );
  }
  if (error) {
    return <div className="p-4 text-sm text-destructive">{error}</div>;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Installed vs latest for each provider CLI/SDK. Update to latest, or pin a
          known-good version to roll back when a new release breaks something.
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void load(true)}
          title="Refresh + check latest"
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      <div className="divide-y divide-border rounded-lg border border-border">
        {runtimes.map((rt) => {
          const isBusy = busy === rt.name;
          return (
            <div
              key={rt.name}
              className="flex items-center justify-between gap-3 px-3 py-2.5"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                  <span className="capitalize">{rt.name}</span>
                  {!rt.installed ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                      <XCircle className="h-3 w-3" /> not installed
                    </span>
                  ) : rt.updateAvailable ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-xs text-warning">
                      <ArrowUpCircle className="h-3 w-3" /> update available
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 rounded-full bg-success/15 px-2 py-0.5 text-xs text-success">
                      <CheckCircle2 className="h-3 w-3" /> up to date
                    </span>
                  )}
                  {rt.pinnedVersion && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-info/15 px-2 py-0.5 text-xs text-info">
                      <Pin className="h-3 w-3" /> pinned {rt.pinnedVersion}
                    </span>
                  )}
                  {!rt.managed && (
                    <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                      user-managed
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">
                  installed {rt.installedVersion ?? '—'}
                  {rt.latestVersion ? ` · latest ${rt.latestVersion}` : ''} · {rt.kind}
                </div>
              </div>

              <div className="flex shrink-0 items-center gap-1.5">
                {isBusy && (
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                )}
                {rt.managed && (rt.updateAvailable || !rt.installed) && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={isBusy}
                    onClick={() => void doUpdate(rt.name)}
                  >
                    {rt.installed ? 'Update' : 'Install'}
                  </Button>
                )}
                {rt.managed &&
                  rt.installed &&
                  (rt.pinnedVersion ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={isBusy}
                      onClick={() => void doPin(rt.name, null)}
                      title="Unpin"
                    >
                      <PinOff className="h-4 w-4" />
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={isBusy}
                      onClick={() => void doPin(rt.name, rt.installedVersion)}
                      title={`Pin to ${rt.installedVersion ?? 'installed'}`}
                    >
                      <Pin className="h-4 w-4" />
                    </Button>
                  ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
