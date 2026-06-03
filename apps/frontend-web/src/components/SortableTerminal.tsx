import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { cn } from '../lib/utils';
import { Terminal } from './Terminal';
import type { Task } from '../shared/types';

interface SortableTerminalProps {
  id: string;
  cwd?: string;
  projectPath?: string;
  isActive: boolean;
  onClose: () => void;
  onActivate: () => void;
  tasks: Task[];
  onNewTaskClick?: () => void;
  terminalCount: number;
}

export function SortableTerminal({
  id,
  cwd,
  projectPath,
  isActive,
  onClose,
  onActivate,
  tasks,
  onNewTaskClick,
  terminalCount
}: SortableTerminalProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging
  } = useSortable({
    id,
    data: {
      type: 'terminal',
      terminalId: id
    }
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 50 : undefined
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        'terminal-grid-item h-full min-w-0 overflow-hidden',
        isDragging && 'dragging opacity-60 ring-2 ring-primary/50'
      )}
      {...attributes}
    >
      <Terminal
        id={id}
        cwd={cwd}
        projectPath={projectPath}
        isActive={isActive}
        onClose={onClose}
        onActivate={onActivate}
        tasks={tasks}
        onNewTaskClick={onNewTaskClick}
        terminalCount={terminalCount}
        dragHandleProps={listeners}
      />
    </div>
  );
}
