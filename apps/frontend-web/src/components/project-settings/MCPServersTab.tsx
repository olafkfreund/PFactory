import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, RefreshCw, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { apiRequest } from '../../lib/api-client';
import type { Project } from '../../shared/types';

interface MCPCredentialsStatus {
  available: boolean;
  source: string;
}

interface MCPMarkersStatus {
  matches: boolean;
  reason: string;
  required: string[];
  matched: string[];
}

interface MCPServerStatus {
  id: string;
  would_enable: boolean;
  credentials: MCPCredentialsStatus;
  markers: MCPMarkersStatus;
  default_for_agents: string[];
  docs_url: string;
}

interface MCPStatusResponse {
  project: { id: string; path: string };
  servers: MCPServerStatus[];
}

interface MCPServersTabProps {
  project: Project;
}

/**
 * Read-only matrix view of the MCP catalog × this project × credentials.
 *
 * V1 is intentionally read-only — operators flip per-project overrides
 * via ``.pfactory/.env`` (``AGENT_MCP_<agent>_ADD/REMOVE``), not via
 * an inline editor here.  Keeps the source of truth singular and the
 * UI explanatory rather than a control plane in disguise.
 *
 * Backed by ``GET /api/projects/{id}/mcp-status`` (see
 * ``apps/web-server/server/routes/mcp.py``).
 */
export function MCPServersTab({ project }: MCPServersTabProps) {
  const { t } = useTranslation('settings');
  const [status, setStatus] = useState<MCPStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const result = await apiRequest<MCPStatusResponse>(
      `/projects/${project.id}/mcp-status`
    );
    if (result.success && result.data) {
      setStatus(result.data);
    } else {
      setError(result.error || 'Failed to load MCP status');
    }
    setIsLoading(false);
  }, [project.id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-8">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading MCP status…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-start gap-2 text-sm text-destructive py-4">
        <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
        <div>
          <div className="font-medium">Could not load MCP status</div>
          <div className="text-xs text-muted-foreground mt-1">{error}</div>
        </div>
      </div>
    );
  }

  const servers = status?.servers ?? [];

  if (servers.length === 0) {
    return (
      <div className="text-sm text-muted-foreground py-4">
        {t('projectSections.mcp.empty')}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          {t('projectSections.mcp.explainerHint')}
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void load()}
          disabled={isLoading}
        >
          <RefreshCw className="h-3 w-3 mr-1" />
          {t('projectSections.mcp.refresh')}
        </Button>
      </div>

      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-xs text-muted-foreground">
            <tr>
              <th className="text-left px-3 py-2 font-medium">
                {t('projectSections.mcp.columns.server')}
              </th>
              <th className="text-left px-3 py-2 font-medium">
                {t('projectSections.mcp.columns.status')}
              </th>
              <th className="text-left px-3 py-2 font-medium">
                {t('projectSections.mcp.columns.credentials')}
              </th>
              <th className="text-left px-3 py-2 font-medium">
                {t('projectSections.mcp.columns.markers')}
              </th>
              <th className="text-left px-3 py-2 font-medium">
                {t('projectSections.mcp.columns.agents')}
              </th>
              <th className="text-left px-3 py-2 font-medium">
                {t('projectSections.mcp.columns.docs')}
              </th>
            </tr>
          </thead>
          <tbody>
            {servers.map((s) => (
              <ServerRow key={s.id} server={s} t={t} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface ServerRowProps {
  server: MCPServerStatus;
  t: (key: string) => string;
}

function ServerRow({ server, t }: ServerRowProps) {
  // Status icon — green only when ALL prereqs line up.  Distinguishes
  // "no creds" from "no marker" in the text so the operator knows what
  // to fix.
  const statusIcon = server.would_enable ? (
    <CheckCircle2 className="h-4 w-4 text-success" />
  ) : (
    <AlertCircle className="h-4 w-4 text-muted-foreground" />
  );

  let statusLabel: string;
  let statusVariant: 'success' | 'warning' = 'success';
  if (server.would_enable) {
    statusLabel = t('projectSections.mcp.status.active');
  } else if (!server.credentials.available) {
    statusLabel = t('projectSections.mcp.status.noCreds');
    statusVariant = 'warning';
  } else if (!server.markers.matches) {
    statusLabel = t('projectSections.mcp.status.noMarker');
    statusVariant = 'warning';
  } else {
    statusLabel = t('projectSections.mcp.status.ready');
  }

  return (
    <tr className="border-t border-border">
      <td className="px-3 py-2 font-medium font-mono">{server.id}</td>
      <td className="px-3 py-2">
        <div className="flex items-center gap-2">
          {statusIcon}
          <Badge
            variant={statusVariant === 'success' ? 'default' : 'outline'}
            className="text-xs"
          >
            {statusLabel}
          </Badge>
        </div>
      </td>
      <td className="px-3 py-2 text-xs">
        <span
          className={
            server.credentials.available
              ? 'text-success font-mono'
              : 'text-muted-foreground'
          }
        >
          {server.credentials.available
            ? server.credentials.source
            : 'none'}
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground font-mono">
        {server.markers.reason}
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">
        {server.default_for_agents.join(', ')}
      </td>
      <td className="px-3 py-2">
        {server.docs_url && (
          <a
            href={server.docs_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            {t('projectSections.mcp.openDocs')}
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </td>
    </tr>
  );
}
