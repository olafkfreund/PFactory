/**
 * CodeBlock - Syntax-highlighted code block component
 *
 * Features:
 * - Syntax highlighting via highlight.js
 * - Optional line numbers
 * - Copy to clipboard button
 * - Theme-aware (light/dark mode)
 * - Language auto-detection from filename
 */

import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { Button } from './button';
import { cn } from '../../lib/utils';
import hljs, { detectLanguage, getLanguageDisplayName } from '../../lib/highlight-config';

export interface CodeBlockProps {
  code: string;
  language?: string;
  fileName?: string;
  showLineNumbers?: boolean;
  maxHeight?: string;
  className?: string;
}

export function CodeBlock({
  code,
  language,
  fileName,
  showLineNumbers = false,
  maxHeight = '600px',
  className,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  // Auto-detect language from fileName if not provided
  const detectedLang = language || (fileName ? detectLanguage(fileName) : undefined);

  // Highlight code
  let highlighted: string;
  try {
    if (detectedLang) {
      highlighted = hljs.highlight(code, { language: detectedLang }).value;
    } else {
      highlighted = hljs.highlightAuto(code).value;
    }
  } catch (error) {
    // Fallback to plain text if highlighting fails
    console.warn('Syntax highlighting failed:', error);
    highlighted = code;
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('Failed to copy code:', error);
    }
  };

  const lines = code.split('\n');
  const languageDisplay = detectedLang ? getLanguageDisplayName(detectedLang) : null;

  return (
    <div className={cn('relative group rounded-md border border-border overflow-hidden', className)}>
      {/* Header with language badge and copy button */}
      {(fileName || languageDisplay) && (
        <div className="flex items-center justify-between px-4 py-2 bg-muted/50 border-b border-border">
          <div className="flex items-center gap-2">
            {fileName && (
              <span className="text-xs font-medium text-foreground">{fileName}</span>
            )}
            {languageDisplay && !fileName && (
              <span className="text-xs px-2 py-0.5 rounded bg-primary/10 text-primary font-medium">
                {languageDisplay}
              </span>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity"
            onClick={handleCopy}
            title={copied ? 'Copied!' : 'Copy code'}
          >
            {copied ? (
              <Check className="h-3 w-3 text-success" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
          </Button>
        </div>
      )}

      {/* Code content */}
      <div
        className="overflow-auto bg-card"
        style={{ maxHeight }}
      >
        <pre className="p-4 m-0 font-mono text-xs leading-relaxed">
          {showLineNumbers ? (
            <table className="w-full border-collapse">
              <tbody>
                {lines.map((line, i) => {
                  // Highlight each line individually to preserve line structure
                  let lineHighlighted: string;
                  try {
                    if (detectedLang) {
                      lineHighlighted = hljs.highlight(line || '\n', { language: detectedLang }).value;
                    } else {
                      lineHighlighted = hljs.highlightAuto(line || '\n').value;
                    }
                  } catch {
                    lineHighlighted = line || '\n';
                  }

                  return (
                    <tr key={i}>
                      <td
                        className="select-none pr-4 text-right text-muted-foreground/50 align-top"
                        style={{ width: '1%', minWidth: '3ch' }}
                      >
                        {i + 1}
                      </td>
                      <td className="align-top">
                        <code
                          className="hljs"
                          dangerouslySetInnerHTML={{ __html: lineHighlighted }}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <code
              className="hljs"
              dangerouslySetInnerHTML={{ __html: highlighted }}
            />
          )}
        </pre>
      </div>
    </div>
  );
}
