import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  DndContext,
  DragOverlay,
  type DragEndEvent,
  type DragStartEvent,
  PointerSensor,
  useSensor,
  useSensors
} from '@dnd-kit/core';
import { SortableContext, rectSortingStrategy } from '@dnd-kit/sortable';
import { Plus, Sparkles, Grid2X2, FolderTree, File, Folder, History, ChevronDown, Loader2, TerminalSquare } from 'lucide-react';
import { SortableTerminal } from './SortableTerminal';
import { Button } from './ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from './ui/dropdown-menu';
import { FileExplorerPanel } from './FileExplorerPanel';
import { cn } from '../lib/utils';
import { useTerminalStore } from '../stores/terminal-store';
import { useTaskStore } from '../stores/task-store';
import { useFileExplorerStore } from '../stores/file-explorer-store';
import type { SessionDateInfo } from '../shared/types';

interface TerminalGridProps {
  projectPath?: string;
  onNewTaskClick?: () => void;
  isActive?: boolean;
}

export function TerminalGrid({ projectPath, onNewTaskClick, isActive = false }: TerminalGridProps) {
  const allTerminals = useTerminalStore((state) => state.terminals);
  // Filter terminals to show only those belonging to the current project
  // Also include legacy terminals without projectPath (created before this change)
  const terminals = useMemo(() =>
    projectPath
      ? allTerminals.filter(t => t.projectPath === projectPath || !t.projectPath)
      : allTerminals,
    [allTerminals, projectPath]
  );
  const activeTerminalId = useTerminalStore((state) => state.activeTerminalId);
  const addTerminal = useTerminalStore((state) => state.addTerminal);
  const removeTerminal = useTerminalStore((state) => state.removeTerminal);
  const setActiveTerminal = useTerminalStore((state) => state.setActiveTerminal);
  const canAddTerminal = useTerminalStore((state) => state.canAddTerminal);
  const setClaudeMode = useTerminalStore((state) => state.setClaudeMode);
  const reorderTerminals = useTerminalStore((state) => state.reorderTerminals);

  // Get tasks from task store for task selection dropdown in terminals
  const tasks = useTaskStore((state) => state.tasks);

  // File explorer state
  const fileExplorerOpen = useFileExplorerStore((state) => state.isOpen);
  const toggleFileExplorer = useFileExplorerStore((state) => state.toggle);

  // Session history state
  const [sessionDates, setSessionDates] = useState<SessionDateInfo[]>([]);
  const [isLoadingDates, setIsLoadingDates] = useState(false);
  const [isRestoring, setIsRestoring] = useState(false);

  // Fetch available session dates when project changes
  useEffect(() => {
    if (!projectPath) {
      setSessionDates([]);
      return;
    }

    const fetchSessionDates = async () => {
      setIsLoadingDates(true);
      try {
        const result = await window.API.getTerminalSessionDates(projectPath);
        if (result.success && result.data) {
          setSessionDates(result.data);
        }
      } catch (error) {
        console.error('Failed to fetch session dates:', error);
      } finally {
        setIsLoadingDates(false);
      }
    };

    fetchSessionDates();
  }, [projectPath]);

  // Get addRestoredTerminal from store
  const addRestoredTerminal = useTerminalStore((state) => state.addRestoredTerminal);

  // Handle restoring sessions from a specific date
  const handleRestoreFromDate = useCallback(async (date: string) => {
    if (!projectPath || isRestoring) return;

    setIsRestoring(true);
    try {
      // First get the session data for this date (we need it after restore)
      const sessionsResult = await window.API.getTerminalSessionsForDate(date, projectPath);
      const sessionsToRestore = sessionsResult.success ? sessionsResult.data || [] : [];

      console.warn(`[TerminalGrid] Found ${sessionsToRestore.length} sessions to restore from ${date}`);

      if (sessionsToRestore.length === 0) {
        console.warn('[TerminalGrid] No sessions found for this date');
        setIsRestoring(false);
        return;
      }

      // Close all existing terminals
      for (const terminal of terminals) {
        await window.API.destroyTerminal(terminal.id);
        removeTerminal(terminal.id);
      }

      // Small delay to ensure cleanup
      await new Promise(resolve => setTimeout(resolve, 100));

      // Restore sessions from the selected date (creates PTYs in main process)
      const result = await window.API.restoreTerminalSessionsFromDate(
        date,
        projectPath,
        80,
        24
      );

      if (result.success && result.data) {
        console.warn(`[TerminalGrid] Main process restored ${result.data.restored} sessions from ${date}`);

        // Add each successfully restored session to the renderer's terminal store
        for (const sessionResult of result.data.sessions) {
          if (sessionResult.success) {
            // Find the full session data
            const fullSession = sessionsToRestore.find(s => s.id === sessionResult.id);
            if (fullSession) {
              console.warn(`[TerminalGrid] Adding restored terminal to store: ${fullSession.id}`);
              addRestoredTerminal(fullSession);
            }
          }
        }

        // Refresh session dates to update counts
        const datesResult = await window.API.getTerminalSessionDates(projectPath);
        if (datesResult.success && datesResult.data) {
          setSessionDates(datesResult.data);
        }
      }
    } catch (error) {
      console.error('Failed to restore sessions:', error);
    } finally {
      setIsRestoring(false);
    }
  }, [projectPath, terminals, removeTerminal, addRestoredTerminal, isRestoring]);

  // Setup drag sensors
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8, // 8px movement required before drag starts
      },
    })
  );

  // Track dragging state for overlay
  const [activeDragData, setActiveDragData] = React.useState<{
    type: 'file' | 'terminal';
    // File drag data
    path?: string;
    name?: string;
    isDirectory?: boolean;
    // Terminal drag data
    terminalId?: string;
    terminalTitle?: string;
  } | null>(null);

  const handleCloseTerminal = useCallback((id: string) => {
    window.API.destroyTerminal(id);
    removeTerminal(id);
  }, [removeTerminal]);

  // Re-fit all xterm instances when the terminals view becomes visible again.
  // xterm FitAddon can't measure while container is display:none, so we dispatch
  // a resize event to trigger all ResizeObservers once the DOM is visible.
  useEffect(() => {
    if (isActive && terminals.length > 0) {
      // Small delay to let the browser lay out the now-visible container
      const timer = setTimeout(() => {
        window.dispatchEvent(new Event('resize'));
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [isActive, terminals.length]);

  // Handle keyboard shortcut for new terminal (only when this view is active)
  useEffect(() => {
    if (!isActive) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl+Shift+E for new terminal - not used by browsers/OS
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'E') {
        e.preventDefault();
        if (canAddTerminal()) {
          addTerminal(projectPath, projectPath);
        }
      }
      // Ctrl+W or Cmd+W to close active terminal
      if ((e.ctrlKey || e.metaKey) && e.key === 'w' && activeTerminalId) {
        e.preventDefault();
        handleCloseTerminal(activeTerminalId);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isActive, addTerminal, canAddTerminal, projectPath, activeTerminalId, handleCloseTerminal]);

  const handleAddTerminal = useCallback(() => {
    if (canAddTerminal()) {
      addTerminal(projectPath, projectPath);
    }
  }, [addTerminal, canAddTerminal, projectPath]);

  const handleOpenClaudeAll = useCallback(() => {
    terminals.forEach((terminal) => {
      if (terminal.status === 'running' && !terminal.isClaudeMode) {
        setClaudeMode(terminal.id, true);
        // Send "claude --dangerously-skip-permissions" command + Enter to the terminal
        window.API.sendTerminalInput(terminal.id, 'claude --dangerously-skip-permissions\r');
      }
    });
  }, [terminals, setClaudeMode]);

  // Handle drag start - store dragged item data
  const handleDragStart = useCallback((event: DragStartEvent) => {
    const data = event.active.data.current as {
      type: string;
      path?: string;
      name?: string;
      isDirectory?: boolean;
      terminalId?: string;
    } | undefined;

    if (data?.type === 'file') {
      setActiveDragData({
        type: 'file',
        path: data.path,
        name: data.name,
        isDirectory: data.isDirectory
      });
    } else if (data?.type === 'terminal') {
      const terminal = terminals.find(t => t.id === data.terminalId);
      setActiveDragData({
        type: 'terminal',
        terminalId: data.terminalId,
        terminalTitle: terminal?.title || 'Terminal'
      });
    }
  }, [terminals]);

  // Handle drag end - insert file path into terminal or reorder terminals
  const handleDragEnd = useCallback((event: DragEndEvent) => {
    const { active, over } = event;
    const dragData = activeDragData;

    setActiveDragData(null);

    if (!over) return;

    // Handle terminal reordering
    if (dragData?.type === 'terminal') {
      const activeId = active.id.toString();
      let overId = over.id.toString();

      // The over ID might be from the droppable zone (terminal-{id}) or sortable context ({id})
      if (overId.startsWith('terminal-')) {
        overId = overId.replace('terminal-', '');
      }

      if (activeId === overId) return;

      // Pass IDs directly - the store will find the correct indices
      reorderTerminals(activeId, overId);
      return;
    }

    // Handle file drop onto terminal
    if (dragData?.type === 'file') {
      const overId = over.id.toString();
      if (overId.startsWith('terminal-')) {
        const terminalId = overId.replace('terminal-', '');
        const data = active.data.current as { path?: string } | undefined;

        if (data?.path) {
          // Quote the path if it contains spaces
          const quotedPath = data.path.includes(' ') ? `"${data.path}"` : data.path;
          // Insert the file path into the terminal with a trailing space
          window.API.sendTerminalInput(terminalId, quotedPath + ' ');
        }
      }
    }
  }, [activeDragData, terminals, reorderTerminals]);

  // Calculate grid layout based on number of terminals
  const gridLayout = useMemo(() => {
    const count = terminals.length;
    if (count === 0) return { rows: 0, cols: 0 };
    if (count === 1) return { rows: 1, cols: 1 };
    if (count === 2) return { rows: 1, cols: 2 };
    if (count <= 4) return { rows: 2, cols: 2 };
    if (count <= 6) return { rows: 2, cols: 3 };
    if (count <= 9) return { rows: 3, cols: 3 };
    return { rows: 3, cols: 4 }; // Max 12 terminals = 3x4
  }, [terminals.length]);

  // Get terminal IDs for SortableContext
  const terminalIds = useMemo(() => terminals.map(t => t.id), [terminals]);

  // Empty state
  if (terminals.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-6 p-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="rounded-full bg-card p-4">
            <Grid2X2 className="h-8 w-8 text-muted-foreground" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-foreground">Agent Terminals</h2>
            <p className="mt-1 text-sm text-muted-foreground max-w-md">
              Spawn multiple terminals to run Claude agents in parallel.
              Use <kbd className="px-1.5 py-0.5 text-xs bg-card border border-border rounded">{navigator.platform.includes('Mac') ? '⌘+Shift+E' : 'Ctrl+Shift+E'}</kbd> to create a new terminal.
            </p>
          </div>
        </div>
        <Button onClick={handleAddTerminal} className="gap-2">
          <Plus className="h-4 w-4" />
          New Terminal
        </Button>
      </div>
    );
  }

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
    >
      <div className="flex h-full flex-col min-w-0 overflow-hidden">
        {/* Toolbar */}
        <div className="flex h-10 items-center justify-between border-b border-border bg-card/30 px-3">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">
              {terminals.length} / 12 terminals
            </span>
          </div>
          <div className="flex items-center gap-2">
            {/* Session history dropdown */}
            {projectPath && sessionDates.length > 0 && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs gap-1.5"
                    disabled={isRestoring || isLoadingDates}
                  >
                    {isRestoring ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <History className="h-3 w-3" />
                    )}
                    History
                    <ChevronDown className="h-3 w-3" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-56">
                  <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
                    Restore sessions from...
                  </div>
                  <DropdownMenuSeparator />
                  {sessionDates.map((dateInfo) => (
                    <DropdownMenuItem
                      key={dateInfo.date}
                      onClick={() => handleRestoreFromDate(dateInfo.date)}
                      className="flex items-center justify-between"
                    >
                      <span>{dateInfo.label}</span>
                      <span className="text-xs text-muted-foreground">
                        {dateInfo.sessionCount} session{dateInfo.sessionCount !== 1 ? 's' : ''}
                      </span>
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
            {terminals.some((t) => t.status === 'running' && !t.isClaudeMode) && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs gap-1.5"
                onClick={handleOpenClaudeAll}
              >
                <Sparkles className="h-3 w-3" />
                Open Claude All
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs gap-1.5"
              onClick={handleAddTerminal}
              disabled={!canAddTerminal()}
            >
              <Plus className="h-3 w-3" />
              New Terminal
              <kbd className="ml-1 text-[10px] text-muted-foreground">
                {navigator.platform.includes('Mac') ? '⌘⇧E' : 'Ctrl+Shift+E'}
              </kbd>
            </Button>
            {/* File explorer toggle button */}
            {projectPath && (
              <Button
                variant={fileExplorerOpen ? 'default' : 'outline'}
                size="sm"
                className="h-7 text-xs gap-1.5"
                onClick={toggleFileExplorer}
              >
                <FolderTree className="h-3 w-3" />
                Files
              </Button>
            )}
          </div>
        </div>

        {/* Main content area with terminal grid and file explorer sidebar */}
        <div className="flex flex-1 overflow-hidden">
          {/* Terminal grid using CSS Grid with sortable terminals */}
          <div className={cn(
            "flex-1 overflow-hidden p-2 transition-all duration-300 ease-out",
            fileExplorerOpen && "pr-0"
          )}>
            <SortableContext items={terminalIds} strategy={rectSortingStrategy}>
              <div
                className="grid gap-2 h-full min-w-0"
                style={{
                  gridTemplateColumns: `repeat(${gridLayout.cols}, 1fr)`,
                  gridTemplateRows: `repeat(${gridLayout.rows}, 1fr)`,
                }}
              >
                {terminals.map((terminal) => (
                  <SortableTerminal
                    key={terminal.id}
                    id={terminal.id}
                    cwd={terminal.cwd || projectPath}
                    projectPath={projectPath}
                    isActive={terminal.id === activeTerminalId}
                    onClose={() => handleCloseTerminal(terminal.id)}
                    onActivate={() => setActiveTerminal(terminal.id)}
                    tasks={tasks}
                    onNewTaskClick={onNewTaskClick}
                    terminalCount={terminals.length}
                  />
                ))}
              </div>
            </SortableContext>
          </div>

          {/* File explorer panel (slides from right, pushes content) */}
          {projectPath && <FileExplorerPanel projectPath={projectPath} />}
        </div>

        {/* Drag overlay - shows what's being dragged */}
        <DragOverlay>
          {activeDragData?.type === 'file' && (
            <div className="flex items-center gap-2 bg-card border border-border rounded-md px-3 py-2 shadow-lg">
              {activeDragData.isDirectory ? (
                <Folder className="h-4 w-4 text-warning" />
              ) : (
                <File className="h-4 w-4 text-muted-foreground" />
              )}
              <span className="text-sm">{activeDragData.name}</span>
            </div>
          )}
          {activeDragData?.type === 'terminal' && (
            <div className="flex items-center gap-2 bg-card border border-primary/50 rounded-md px-3 py-2 shadow-xl ring-2 ring-primary/30">
              <TerminalSquare className="h-4 w-4 text-primary" />
              <span className="text-sm font-medium">{activeDragData.terminalTitle}</span>
            </div>
          )}
        </DragOverlay>
      </div>
    </DndContext>
  );
}
