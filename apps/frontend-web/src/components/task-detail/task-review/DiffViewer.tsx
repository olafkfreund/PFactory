import { useMemo } from 'react';
import { ScrollArea } from '../../ui/scroll-area';
import { cn } from '../../../lib/utils';

export interface DiffViewerProps {
  /** The raw diff content string */
  diff: string;
  /** Maximum height of the diff viewer (CSS value). Default: "300px" */
  maxHeight?: string;
  /** Show line numbers. Default: true */
  showLineNumbers?: boolean;
  /** Show file headers (--- / +++). Default: true */
  showFileHeaders?: boolean;
  /** Additional className for the container */
  className?: string;
}

interface ParsedLine {
  content: string;
  type: 'addition' | 'deletion' | 'hunk' | 'file-header' | 'context';
  lineNumber?: number;
}

/**
 * Parse a line of diff output to determine its type
 */
function parseLineType(line: string): ParsedLine['type'] {
  if (line.startsWith('+++') || line.startsWith('---')) {
    return 'file-header';
  }
  if (line.startsWith('@@')) {
    return 'hunk';
  }
  if (line.startsWith('+')) {
    return 'addition';
  }
  if (line.startsWith('-')) {
    return 'deletion';
  }
  return 'context';
}

/**
 * Get styling classes for a diff line based on its type
 */
function getLineStyles(type: ParsedLine['type']): { text: string; bg: string } {
  switch (type) {
    case 'addition':
      return { text: 'text-success', bg: 'bg-success/10' };
    case 'deletion':
      return { text: 'text-destructive', bg: 'bg-destructive/10' };
    case 'hunk':
      return { text: 'text-info', bg: 'bg-info/10' };
    case 'file-header':
      return { text: 'text-muted-foreground font-semibold', bg: '' };
    case 'context':
    default:
      return { text: 'text-muted-foreground', bg: '' };
  }
}

/**
 * Reusable diff viewer component with syntax highlighting for git diffs.
 *
 * Provides proper styling for:
 * - Additions (green)
 * - Deletions (red)
 * - Hunk headers (@@ ... @@) (info blue)
 * - File headers (--- / +++) (muted bold)
 * - Context lines (muted)
 *
 * @example
 * ```tsx
 * <DiffViewer diff={gitDiffContent} maxHeight="400px" showLineNumbers />
 * ```
 */
export function DiffViewer({
  diff,
  maxHeight = '300px',
  showLineNumbers = true,
  showFileHeaders = true,
  className
}: DiffViewerProps) {
  const parsedLines = useMemo(() => {
    if (!diff) return [];

    const lines = diff.split('\n');
    const result: ParsedLine[] = [];
    let lineNum = 0;

    for (const line of lines) {
      const type = parseLineType(line);

      // Skip file headers if configured
      if (type === 'file-header' && !showFileHeaders) {
        continue;
      }

      lineNum++;
      result.push({
        content: line,
        type,
        lineNumber: lineNum
      });
    }

    return result;
  }, [diff, showFileHeaders]);

  if (!diff) {
    return (
      <div className={cn('text-center py-4 text-muted-foreground text-sm', className)}>
        No diff content available
      </div>
    );
  }

  return (
    <ScrollArea className={cn('w-full', className)} style={{ maxHeight }}>
      <pre className="text-xs font-mono p-3 bg-muted/50 rounded-md overflow-x-auto">
        {parsedLines.map((line, idx) => {
          const styles = getLineStyles(line.type);

          return (
            <div
              key={idx}
              className={cn('whitespace-pre flex', styles.bg)}
            >
              {showLineNumbers && (
                <span className="select-none text-muted-foreground/50 w-8 shrink-0 text-right pr-2 border-r border-muted mr-2">
                  {line.lineNumber}
                </span>
              )}
              <span className={styles.text}>{line.content}</span>
            </div>
          );
        })}
      </pre>
    </ScrollArea>
  );
}

/**
 * Compact inline diff display for single-line diff summaries
 */
export function InlineDiffStat({
  additions,
  deletions,
  className
}: {
  additions: number;
  deletions: number;
  className?: string;
}) {
  return (
    <div className={cn('flex items-center gap-2 text-xs font-mono', className)}>
      <span className="text-success">+{additions}</span>
      <span className="text-destructive">-{deletions}</span>
    </div>
  );
}

/**
 * Simple diff line indicator icons
 */
export function DiffLineIndicator({ type }: { type: ParsedLine['type'] }) {
  const styles = getLineStyles(type);

  return (
    <span className={cn('inline-block w-4 text-center font-bold', styles.text)}>
      {type === 'addition' && '+'}
      {type === 'deletion' && '-'}
      {type === 'hunk' && '@'}
      {type === 'context' && ' '}
    </span>
  );
}

export default DiffViewer;
