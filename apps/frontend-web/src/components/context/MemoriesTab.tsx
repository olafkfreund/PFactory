import { useState } from 'react';
import {
  RefreshCw,
  Database,
  Brain,
  Search,
  CheckCircle,
  XCircle,
  AlertCircle
} from 'lucide-react';
import { Button } from '../ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Badge } from '../ui/badge';
import { Input } from '../ui/input';
import { ScrollArea } from '../ui/scroll-area';
import { cn } from '../../lib/utils';
import { MemoryCard } from './MemoryCard';
import { InfoItem } from './InfoItem';
import type { GraphitiMemoryStatus, GraphitiMemoryState, MemoryEpisode } from '../../shared/types';

interface MemoriesTabProps {
  memoryStatus: GraphitiMemoryStatus | null;
  memoryState: GraphitiMemoryState | null;
  recentMemories: MemoryEpisode[];
  memoriesLoading: boolean;
  searchResults: Array<{ type: string; content: string; score: number }>;
  searchLoading: boolean;
  searchQuery: string;
  onSearch: (query: string) => void;
}

export function MemoriesTab({
  memoryStatus,
  memoryState,
  recentMemories,
  memoriesLoading,
  searchResults,
  searchLoading,
  searchQuery,
  onSearch
}: MemoriesTabProps) {
  const [localSearchQuery, setLocalSearchQuery] = useState('');

  const handleSearch = () => {
    if (localSearchQuery.trim()) {
      onSearch(localSearchQuery);
    }
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };

  return (
    <ScrollArea className="h-full">
      <div className="p-6 space-y-6">
        {/* Memory Status */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <Database className="h-4 w-4" />
                Graph Memory Status
              </CardTitle>
              {memoryStatus?.available ? (
                <Badge variant="outline" className="bg-success/10 text-success border-success/30">
                  <CheckCircle className="h-3 w-3 mr-1" />
                  Connected
                </Badge>
              ) : (
                <Badge variant="outline" className="bg-muted text-muted-foreground">
                  <XCircle className="h-3 w-3 mr-1" />
                  Not Available
                </Badge>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {memoryStatus?.available ? (
              <>
                <div className="grid gap-3 sm:grid-cols-3 text-sm">
                  <InfoItem label="Database" value={memoryStatus.database || 'pfactory_memory'} />
                  <InfoItem label="Path" value={memoryStatus.dbPath || '~/.pfactory/graphs'} />
                  {memoryState && (
                    <InfoItem label="Episodes" value={memoryState.episode_count.toString()} />
                  )}
                </div>
                {memoryState?.last_session && (
                  <p className="text-xs text-muted-foreground">
                    Last session: #{memoryState.last_session}
                  </p>
                )}
              </>
            ) : (
              <div className="text-sm text-muted-foreground">
                <p>{memoryStatus?.reason || 'Graphiti memory is not configured'}</p>
                <p className="mt-2 text-xs">
                  To enable graph memory, set <code className="bg-muted px-1 py-0.5 rounded">GRAPHITI_ENABLED=true</code> in project settings.
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Search */}
        <div className="space-y-4">
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
            Search Memories
          </h3>
          <div className="flex gap-2">
            <Input
              placeholder="Search for patterns, insights, gotchas..."
              value={localSearchQuery}
              onChange={(e) => setLocalSearchQuery(e.target.value)}
              onKeyDown={handleSearchKeyDown}
            />
            <Button onClick={handleSearch} disabled={searchLoading}>
              <Search className={cn('h-4 w-4', searchLoading && 'animate-pulse')} />
            </Button>
          </div>

          {/* No Results Alert */}
          {searchQuery && searchResults.length === 0 && !searchLoading && (
            <div className="flex items-center gap-3 p-4 rounded-lg bg-muted/50 text-muted-foreground border border-border">
              <AlertCircle className="h-5 w-5 shrink-0" />
              <p className="text-sm">Nothing was found</p>
            </div>
          )}

          {/* Search Results */}
          {searchResults.length > 0 && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {searchResults.length} result{searchResults.length !== 1 ? 's' : ''} found
              </p>
              {searchResults.map((result, idx) => {
                // Transform search result to MemoryEpisode format for MemoryCard
                const memoryData: MemoryEpisode = {
                  id: (result as any).id || `search-${idx}`,
                  type: (result.type as MemoryEpisode['type']) || 'session_insight',
                  timestamp: (result as any).timestamp || new Date().toISOString(),
                  session_number: (result as any).sessionNumber,
                  // Store full data as JSON in content for MemoryCard to parse
                  content: JSON.stringify({
                    spec_id: (result as any).specId,
                    session_number: (result as any).sessionNumber,
                    subtasks_completed: (result as any).subtasksCompleted || [],
                    what_worked: (result as any).whatWorked || [],
                    what_failed: (result as any).whatFailed || [],
                    recommendations_for_next_session: (result as any).recommendations || [],
                    discoveries: (result as any).discoveries || {}
                  })
                };
                return (
                  <div key={idx} className="relative">
                    <Badge
                      variant="secondary"
                      className="absolute -top-2 right-2 z-10 text-xs"
                    >
                      Score: {result.score?.toFixed(1) || '0'}
                    </Badge>
                    <MemoryCard memory={memoryData} />
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Recent Memories */}
        <div className="space-y-4">
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
            Recent Memories
          </h3>

          {memoriesLoading && (
            <div className="flex items-center justify-center py-8">
              <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {!memoriesLoading && recentMemories.length === 0 && (
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <Brain className="h-10 w-10 text-muted-foreground mb-3" />
              <p className="text-sm text-muted-foreground">
                No memories recorded yet. Memories are created during AI agent sessions.
              </p>
            </div>
          )}

          {recentMemories.length > 0 && (
            <div className="space-y-3">
              {recentMemories.map((memory) => (
                <MemoryCard key={memory.id} memory={memory} />
              ))}
            </div>
          )}
        </div>
      </div>
    </ScrollArea>
  );
}
