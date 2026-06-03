/**
 * RegistryPanel — lists /api/plan/meta/registry entries.
 * Shows kind, enabled status, version, and capabilities.
 */

import { useEffect, useState } from 'react';
import { Loader2, AlertTriangle, Package } from 'lucide-react';
import { Badge } from '../ui/badge';
import { getRegistry, type FetchOptions } from '../../lib/planning-api';
import type { RegistryEntry } from '../../shared/types/plan';

interface Props {
  fetchFn?: typeof fetch;
}

export function RegistryPanel({ fetchFn }: Props) {
  const [entries, setEntries] = useState<RegistryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const opts: FetchOptions = { fetchFn };
    setLoading(true);
    getRegistry(opts)
      .then((r) => {
        if (!cancelled) { setEntries(r.entries); setLoading(false); }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [fetchFn]);

  if (loading) {
    return (
      <div role="status" className="flex items-center gap-2 p-6 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        <span>Loading registry…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" className="flex items-center gap-2 p-6 text-destructive">
        <AlertTriangle className="h-4 w-4" aria-hidden />
        <span>{error}</span>
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 p-12 text-muted-foreground">
        <Package className="h-8 w-8" aria-hidden />
        <p>No registry entries found.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2" data-testid="registry-panel">
      {entries.map((entry) => (
        <div
          key={entry.id}
          className="flex items-start gap-4 rounded-lg border border-border/60 bg-card/40 px-4 py-3"
        >
          <div className="flex flex-col flex-1 min-w-0 gap-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-sm text-foreground truncate">{entry.title}</span>
              <Badge variant="muted" className="font-mono text-[10px]">v{entry.version}</Badge>
              <Badge variant={entry.kind === 'adapter' ? 'info' : 'secondary'} className="text-[10px]">
                {entry.kind}
              </Badge>
            </div>
            <p className="font-mono text-xs text-muted-foreground">{entry.id}</p>
            {entry.capabilities.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1">
                {entry.capabilities.map((cap) => (
                  <span
                    key={cap}
                    className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                  >
                    {cap}
                  </span>
                ))}
              </div>
            )}
          </div>
          <Badge variant={entry.enabled ? 'success' : 'muted'} className="shrink-0">
            {entry.enabled ? 'enabled' : 'disabled'}
          </Badge>
        </div>
      ))}
    </div>
  );
}
