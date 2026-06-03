/**
 * ProvidersPanel — lists /api/plan/meta/providers and /api/plan/meta/adapters.
 */

import { useEffect, useState } from 'react';
import { Loader2, AlertTriangle, Cpu, Wrench, Database } from 'lucide-react';
import { Badge } from '../ui/badge';
import { getProviders, getAdapters, type FetchOptions } from '../../lib/planning-api';

interface Props {
  fetchFn?: typeof fetch;
}

export function ProvidersPanel({ fetchFn }: Props) {
  const [providers, setProviders] = useState<string[]>([]);
  const [infraAdapters, setInfraAdapters] = useState<string[]>([]);
  const [knowledgeConnectors, setKnowledgeConnectors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const opts: FetchOptions = { fetchFn };
    setLoading(true);
    Promise.all([getProviders(opts), getAdapters(opts)])
      .then(([pr, ad]) => {
        if (!cancelled) {
          setProviders(pr.providers);
          setInfraAdapters(ad.infra_adapters);
          setKnowledgeConnectors(ad.knowledge_connectors);
          setLoading(false);
        }
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
        <span>Loading providers…</span>
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

  return (
    <div className="flex flex-col gap-6" data-testid="providers-panel">
      {/* LLM Providers */}
      <section>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground mb-3">
          <Cpu className="h-4 w-4 text-muted-foreground" aria-hidden />
          LLM Providers ({providers.length})
        </h3>
        {providers.length === 0 ? (
          <p className="text-sm text-muted-foreground">No providers configured.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {providers.map((p) => (
              <Badge key={p} variant="info" className="font-mono">{p}</Badge>
            ))}
          </div>
        )}
      </section>

      {/* Infra adapters */}
      <section>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground mb-3">
          <Wrench className="h-4 w-4 text-muted-foreground" aria-hidden />
          Infra Adapters ({infraAdapters.length})
        </h3>
        {infraAdapters.length === 0 ? (
          <p className="text-sm text-muted-foreground">No infra adapters configured.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {infraAdapters.map((a) => (
              <Badge key={a} variant="secondary" className="font-mono">{a}</Badge>
            ))}
          </div>
        )}
      </section>

      {/* Knowledge connectors */}
      <section>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground mb-3">
          <Database className="h-4 w-4 text-muted-foreground" aria-hidden />
          Knowledge Connectors ({knowledgeConnectors.length})
        </h3>
        {knowledgeConnectors.length === 0 ? (
          <p className="text-sm text-muted-foreground">No knowledge connectors configured.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {knowledgeConnectors.map((k) => (
              <Badge key={k} variant="purple" className="font-mono">{k}</Badge>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
