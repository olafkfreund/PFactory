/**
 * Log Viewer Component
 *
 * Displays frontend and backend logs with filtering and export capabilities.
 */

import { useState, useEffect, useCallback } from 'react';
import { logger, LogEntry, LogLevel } from '../../lib/logger';
import { get, del } from '../../lib/api-client';
import { getAuthHeaders } from '../../lib/auth';
import { Button } from '../ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { ScrollArea } from '../ui/scroll-area';
import { Badge } from '../ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../ui/select';
import { Download, Trash2, RefreshCw } from 'lucide-react';

interface BackendLogEntry {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
  raw: string;
}

const levelColors: Record<string, string> = {
  debug: 'bg-gray-500',
  info: 'bg-blue-500',
  warn: 'bg-yellow-500',
  warning: 'bg-yellow-500',
  error: 'bg-red-500',
};

function LogEntryRow({ entry }: { entry: LogEntry | BackendLogEntry }) {
  const level = entry.level.toLowerCase();
  const levelColor = levelColors[level] || 'bg-gray-500';

  return (
    <div className="flex items-start gap-2 py-1 px-2 hover:bg-muted/50 font-mono text-xs border-b border-border/50">
      <span className="text-muted-foreground shrink-0 w-[140px]">
        {entry.timestamp.split('T')[1]?.split('.')[0] || entry.timestamp.split(' ')[1] || entry.timestamp}
      </span>
      <Badge className={`${levelColor} text-white shrink-0 w-[60px] justify-center`}>
        {entry.level.toUpperCase()}
      </Badge>
      {'category' in entry ? (
        <span className="text-muted-foreground shrink-0 w-[100px] truncate">
          {entry.category}
        </span>
      ) : (
        <span className="text-muted-foreground shrink-0 w-[150px] truncate">
          {entry.logger}
        </span>
      )}
      <span className="flex-1 break-all">{entry.message}</span>
    </div>
  );
}

export function LogViewer() {
  const [frontendLogs, setFrontendLogs] = useState<LogEntry[]>([]);
  const [backendLogs, setBackendLogs] = useState<BackendLogEntry[]>([]);
  const [levelFilter, setLevelFilter] = useState<string>('all');
  const [backendLogType, setBackendLogType] = useState<string>('server');
  const [loading, setLoading] = useState(false);
  const [clearing, setClearing] = useState(false);

  // Load frontend logs
  const loadFrontendLogs = useCallback(() => {
    const logs = logger.getLogs({ limit: 500 });
    setFrontendLogs(logs.reverse());
  }, []);

  // Load backend logs
  const loadBackendLogs = useCallback(async () => {
    setLoading(true);
    try {
      const result = await get<{ entries: BackendLogEntry[] }>(`/logs/${backendLogType}?lines=500`);
      if (result.success && result.data) {
        setBackendLogs(result.data.entries?.reverse() || []);
      } else {
        logger.error('LogViewer', 'Failed to load backend logs', result.error);
      }
    } catch (error) {
      logger.error('LogViewer', 'Failed to load backend logs', error);
    } finally {
      setLoading(false);
    }
  }, [backendLogType]);

  // Clear backend logs
  const clearBackendLogs = useCallback(async () => {
    setClearing(true);
    try {
      const result = await del<{ success: boolean; message: string }>(`/logs/${backendLogType}`);
      if (result.success) {
        setBackendLogs([]);
        logger.info('LogViewer', `Cleared ${backendLogType} logs`);
      } else {
        logger.error('LogViewer', 'Failed to clear backend logs', result.error);
      }
    } catch (error) {
      logger.error('LogViewer', 'Failed to clear backend logs', error);
    } finally {
      setClearing(false);
    }
  }, [backendLogType]);

  // Initial load
  useEffect(() => {
    loadFrontendLogs();
  }, [loadFrontendLogs]);

  useEffect(() => {
    loadBackendLogs();
  }, [loadBackendLogs]);

  // Subscribe to new frontend logs
  useEffect(() => {
    const unsubscribe = logger.subscribe((entry) => {
      setFrontendLogs((prev) => [entry, ...prev].slice(0, 500));
    });
    return unsubscribe;
  }, []);

  // Filter logs by level
  const filterByLevel = (logs: (LogEntry | BackendLogEntry)[]) => {
    if (levelFilter === 'all') return logs;
    return logs.filter((log) => log.level.toLowerCase() === levelFilter);
  };

  const stats = logger.getStats();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Application Logs</CardTitle>
        <CardDescription>
          View and export frontend and backend logs for debugging
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="frontend">
          <div className="flex items-center justify-between mb-4">
            <TabsList>
              <TabsTrigger value="frontend">
                Frontend ({stats.total})
              </TabsTrigger>
              <TabsTrigger value="backend">Backend</TabsTrigger>
            </TabsList>

            <div className="flex items-center gap-2">
              <Select value={levelFilter} onValueChange={setLevelFilter}>
                <SelectTrigger className="w-[120px]">
                  <SelectValue placeholder="Level" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Levels</SelectItem>
                  <SelectItem value="debug">Debug</SelectItem>
                  <SelectItem value="info">Info</SelectItem>
                  <SelectItem value="warn">Warning</SelectItem>
                  <SelectItem value="error">Error</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <TabsContent value="frontend">
            <div className="flex items-center gap-2 mb-4">
              <div className="flex gap-2 text-sm text-muted-foreground">
                <span>Debug: {stats.byLevel.debug}</span>
                <span>Info: {stats.byLevel.info}</span>
                <span className="text-yellow-500">Warn: {stats.byLevel.warn}</span>
                <span className="text-red-500">Error: {stats.byLevel.error}</span>
              </div>
              <div className="flex-1" />
              <Button
                variant="outline"
                size="sm"
                onClick={() => logger.download('json')}
              >
                <Download className="w-4 h-4 mr-2" />
                Export JSON
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => logger.download('text')}
              >
                <Download className="w-4 h-4 mr-2" />
                Export Text
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  logger.clear();
                  loadFrontendLogs();
                }}
              >
                <Trash2 className="w-4 h-4 mr-2" />
                Clear
              </Button>
            </div>

            <ScrollArea className="h-[400px] border rounded-md">
              <div className="p-2">
                {filterByLevel(frontendLogs).length === 0 ? (
                  <div className="text-center text-muted-foreground py-8">
                    No logs to display
                  </div>
                ) : (
                  filterByLevel(frontendLogs).map((entry, i) => (
                    <LogEntryRow key={i} entry={entry} />
                  ))
                )}
              </div>
            </ScrollArea>
          </TabsContent>

          <TabsContent value="backend">
            <div className="flex items-center gap-2 mb-4">
              <Select value={backendLogType} onValueChange={setBackendLogType}>
                <SelectTrigger className="w-[150px]">
                  <SelectValue placeholder="Log Type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="server">Server Logs</SelectItem>
                  <SelectItem value="errors">Error Logs</SelectItem>
                  <SelectItem value="agent">Agent Logs</SelectItem>
                </SelectContent>
              </Select>
              <div className="flex-1" />
              <Button
                variant="outline"
                size="sm"
                onClick={loadBackendLogs}
                disabled={loading}
              >
                <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
                Refresh
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={async () => {
                  try {
                    const authHeaders = getAuthHeaders();
                    const response = await fetch(`/api/logs/${backendLogType}/download`, {
                      headers: authHeaders,
                    });
                    if (response.ok) {
                      const blob = await response.blob();
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = `${backendLogType}.log`;
                      document.body.appendChild(a);
                      a.click();
                      document.body.removeChild(a);
                      URL.revokeObjectURL(url);
                    } else {
                      logger.error('LogViewer', 'Failed to download logs', response.statusText);
                    }
                  } catch (error) {
                    logger.error('LogViewer', 'Failed to download logs', error);
                  }
                }}
              >
                <Download className="w-4 h-4 mr-2" />
                Download
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={clearBackendLogs}
                disabled={clearing}
              >
                <Trash2 className={`w-4 h-4 mr-2 ${clearing ? 'animate-pulse' : ''}`} />
                Clear
              </Button>
            </div>

            <ScrollArea className="h-[400px] border rounded-md">
              <div className="p-2">
                {backendLogs.length === 0 ? (
                  <div className="text-center text-muted-foreground py-8">
                    {loading ? 'Loading...' : 'No logs to display'}
                  </div>
                ) : (
                  filterByLevel(backendLogs).map((entry, i) => (
                    <LogEntryRow key={i} entry={entry} />
                  ))
                )}
              </div>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
